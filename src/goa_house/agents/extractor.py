from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
from typing import Any, Optional

from collections.abc import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from goa_house.diffs import (
    ExtractorResult,
    RequirementDiff,
    validate_projection,
)
from goa_house.state import House, Requirement


EXTRACTOR_SYSTEM_PROMPT = """\
You are the Requirement Extractor for a local-first Goa house designer. You
translate the user's natural-language prompt into a structured response that a
human will review before any state mutates. You do NOT mutate state directly.

You have four MCP tools:
- mcp__goa__get_house            — current house.json (plot, rooms, openings, style)
- mcp__goa__list_recent_requirements — last N approved Requirement records
- mcp__goa__room_geometry_hint   — suggest an axis-aligned polygon+camera for a
                                    new room at a named corner ("NE","NW","SE",
                                    "SW","N","S","E","W","center")
- mcp__goa__validate_projection  — apply candidate diffs to a copy of the house
                                    and return ValidationIssue[]; empty list = clean

Workflow on every prompt:
  1. Call get_house. If the prompt references prior context, also call
     list_recent_requirements.
  2. Decide whether to ask a clarifying question or propose diffs.
  3. For ANY new or resized room, call room_geometry_hint to get coordinates
     — never invent polygon vertices yourself. Use the returned polygon and
     camera verbatim.
  4. Assemble a list of RequirementDiff objects, then call validate_projection
     with the full list. If issues come back, revise and re-validate. Iterate
     until clean OR until you decide a clarification is needed.
  5. Emit your final answer wrapped in <output>...</output> as a single JSON
     object matching ExtractorResult.

ExtractorResult is one of:

  {"kind": "diffs", "diffs": [<RequirementDiff>, ...]}
  {"kind": "clarification", "question": "<single short question>"}

A RequirementDiff is:
{
  "proposed": {
    "scope": "<existing room_id | new snake_case room_id | 'global'>",
    "type": "orientation|dimension|adjacency|material|feature|constraint",
    "statement": "<short declarative sentence, <= 160 chars>"
  },
  "affected_rooms": ["<room_id>", ...],
  "conflicts_with": ["req_XXXX", ...],
  "suggested_resolution": "<free text or null>",
  "source_span": "<exact contiguous substring of the user prompt>",
  "mutation": <Mutation | null>
}

Mutation is one of (discriminated by "op"):
  {"op":"add_room", "room": {<full Room JSON: id, name, polygon, floor, ceiling_height_m, openings, camera>}}
  {"op":"update_room", "room_id":"...", "name":?, "polygon":?, "floor":?, "camera":?, "ceiling_height_m":?}
  {"op":"remove_room", "room_id":"..."}
  {"op":"add_opening", "room_id":"...", "opening": {<Opening: type, wall, position_m, width_m, height_m?, to_room?>}}
  {"op":"remove_opening", "room_id":"...", "opening_index": <int>}

A new room is unreachable until at least one connecting opening (door or
stairs) joins it to an existing room. Emit the door as a SECOND diff with an
add_opening mutation on a neighbouring existing room — connectivity is
required for validate_projection to pass. Remember add_room's openings field
adds openings on the NEW room itself.

Rules:
1. 0..N diffs per prompt. A single prompt may decompose into several diffs.
2. If the prompt is ambiguous, off-topic, vague ("big", "nice"), or modifies
   a room that does not exist (move/enlarge/delete), return a clarification.
   Creation intents ("add", "include", "build a ...") MAY propose a new
   snake_case room id.
3. source_span MUST be an exact contiguous substring of the user prompt.
4. Never invent numbers. Only emit a "dimension" diff when a dimension is
   stated. "Big bedroom" → ask for the size.
5. Detect conflicts against approved Requirements. Populate conflicts_with
   with req_ids and suggest a resolution (e.g. "supersede req_0003" or "merge").
6. Use compass directions (N, S, E, W, NE, NW, SE, SW) consistent with
   plot.north_deg. Do not output raw degrees unless the user gave them.
7. Wall labels for openings are N/S/E/W of the ROOM bounding box.
8. Emit diffs only after validate_projection returns []. If you cannot get to
   clean, return a clarification describing the blocker.
9. Statements are declarative ("Master bedroom faces NE"), not commands.
10. Final answer MUST be a single <output>...</output> block containing one
    JSON object — no prose, no markdown fences, nothing else inside the tags.
"""


class ExtractorError(RuntimeError):
    """Raised when the agent fails to produce a parseable ExtractorResult."""


def build_extractor_user_message(
    user_prompt: str,
    house: House,
    recent_requirements: list[Requirement],
) -> dict[str, Any]:
    """Legacy helper retained for callers that want the raw input bundle."""
    return {
        "user_prompt": user_prompt,
        "house": house.model_dump(mode="json"),
        "recent_requirements": [r.model_dump(mode="json") for r in recent_requirements],
    }


def _make_tools(house: House, recent_requirements: list[Requirement]):
    @tool(
        "get_house",
        "Returns the current house.json as a JSON string: plot, rooms, openings, style.",
        {},
    )
    async def get_house(_args):
        return _text_result(house.model_dump(mode="json"))

    @tool(
        "list_recent_requirements",
        "Returns up to `limit` most-recent approved Requirement records (newest first).",
        {"limit": int},
    )
    async def list_recent(args):
        n = int(args.get("limit") or 20)
        approved = [r for r in recent_requirements if r.status == "approved"]
        recent = list(reversed(approved[-n:]))
        return _text_result([r.model_dump(mode="json") for r in recent])

    @tool(
        "validate_projection",
        (
            "Apply the given RequirementDiffs to a copy of the house and return "
            "ValidationIssue[]. Empty list means the projection is clean. Input "
            "`diffs_json` is a JSON-encoded array of RequirementDiff objects."
        ),
        {"diffs_json": str},
    )
    async def validate_proj(args):
        try:
            raw = json.loads(args["diffs_json"])
            if not isinstance(raw, list):
                raise ValueError("diffs_json must be a JSON array")
            diffs = [RequirementDiff.model_validate(d) for d in raw]
            issues = validate_projection(house, diffs)
            payload = [i.model_dump(mode="json") for i in issues]
        except Exception as exc:
            payload = [{"severity": "hard", "code": "diff_invalid", "message": str(exc)}]
        return _text_result(payload)

    @tool(
        "room_geometry_hint",
        (
            "Suggest an axis-aligned polygon and camera for a new room placed at "
            "a named position inside the buildable envelope. corner is one of "
            "NE,NW,SE,SW,N,S,E,W,center. Returns {polygon, camera, floor} or "
            "{error}."
        ),
        {"corner": str, "width_m": float, "depth_m": float, "floor": int},
    )
    async def geometry_hint(args):
        try:
            payload = compute_geometry_hint(
                house,
                str(args["corner"]),
                float(args["width_m"]),
                float(args["depth_m"]),
                int(args.get("floor") or 0),
            )
        except ValueError as exc:
            payload = {"error": str(exc)}
        return _text_result(payload)

    return [get_house, list_recent, validate_proj, geometry_hint]


def _text_result(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def compute_geometry_hint(
    house: House,
    corner: str,
    width_m: float,
    depth_m: float,
    floor: int,
) -> dict[str, Any]:
    if width_m <= 0 or depth_m <= 0:
        raise ValueError("width and depth must be positive")
    bldable = house.buildable_area()
    if bldable.is_empty:
        raise ValueError("buildable envelope is empty")
    minx, miny, maxx, maxy = bldable.bounds
    bw, bh = maxx - minx, maxy - miny
    if width_m > bw + 1e-6 or depth_m > bh + 1e-6:
        raise ValueError(
            f"requested room {width_m}x{depth_m}m does not fit buildable "
            f"envelope {bw:.2f}x{bh:.2f}m"
        )
    key = corner.strip().upper()
    placements = {
        "NE": (maxx - width_m, maxy - depth_m),
        "NW": (minx, maxy - depth_m),
        "SE": (maxx - width_m, miny),
        "SW": (minx, miny),
        "N":  (minx + (bw - width_m) / 2, maxy - depth_m),
        "S":  (minx + (bw - width_m) / 2, miny),
        "E":  (maxx - width_m, miny + (bh - depth_m) / 2),
        "W":  (minx, miny + (bh - depth_m) / 2),
        "CENTER": (minx + (bw - width_m) / 2, miny + (bh - depth_m) / 2),
    }
    if key not in placements:
        raise ValueError(
            f"unknown corner {corner!r}; use one of {sorted(placements)}"
        )
    x0, y0 = placements[key]
    polygon = [
        (round(x0, 3), round(y0, 3)),
        (round(x0 + width_m, 3), round(y0, 3)),
        (round(x0 + width_m, 3), round(y0 + depth_m, 3)),
        (round(x0, 3), round(y0 + depth_m, 3)),
    ]
    return {
        "polygon": [list(p) for p in polygon],
        "camera": {
            "x": round(x0 + width_m / 2, 3),
            "y": round(y0 + depth_m / 2, 3),
            "z": 1.6,
            "yaw_deg": 0.0,
        },
        "floor": floor,
    }


_OUTPUT_RE = re.compile(r"<output>\s*(.*?)\s*</output>", re.S | re.I)
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse_final_output(text: str) -> Optional[ExtractorResult]:
    """Extract the agent's final ExtractorResult JSON from its assistant text."""
    if not text:
        return None
    m = _OUTPUT_RE.search(text)
    if m:
        try:
            return ExtractorResult.model_validate_json(m.group(1))
        except Exception:
            pass
    m = _FENCE_RE.search(text)
    if m:
        try:
            return ExtractorResult.model_validate_json(m.group(1))
        except Exception:
            pass
    try:
        return ExtractorResult.model_validate_json(text.strip())
    except Exception:
        return None


TOOL_LABELS: dict[str, str] = {
    "get_house": "Reading the plan…",
    "list_recent_requirements": "Checking past decisions…",
    "room_geometry_hint": "Sketching room placement…",
    "validate_projection": "Checking that it fits…",
}


def friendly_tool_label(tool_name: str) -> str:
    """Map an MCP tool name to a user-facing status line; fall back to the bare name."""
    bare = tool_name.split("__")[-1] if "__" in tool_name else tool_name
    return TOOL_LABELS.get(bare, f"Working ({bare})…")


def _needs_proactor_workaround() -> bool:
    """On Windows, uvicorn --reload spawns a child whose event loop lacks
    subprocess support.  We detect this and run the SDK on a dedicated
    ProactorEventLoop thread instead."""
    if sys.platform != "win32":
        return False
    try:
        loop = asyncio.get_running_loop()
        return not isinstance(loop, asyncio.ProactorEventLoop)
    except RuntimeError:
        return False


async def extract_diffs_stream(
    user_prompt: str,
    house: House,
    recent_requirements: list[Requirement],
    *,
    session_id: Optional[str] = None,
    resume: Optional[str] = None,
    model: Optional[str] = None,
    max_turns: int = 12,
    max_budget_usd: Optional[float] = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run the agent and yield chronological events for streaming consumers.

    Event shapes:
        {"type": "status", "tool": "<mcp__goa__...>", "label": "Reading the plan…"}
        {"type": "result", "extractor_result": {...}}
        {"type": "error", "message": "..."}

    Pass `session_id` to pin the SDK session id for a fresh run, or `resume`
    to continue an existing session (carries the model's prior conversation).
    Mutually exclusive in normal use; passing both is allowed but `resume`
    takes precedence in the SDK.
    """
    if _needs_proactor_workaround():
        async for event in _extract_via_thread(
            user_prompt, house, recent_requirements,
            session_id=session_id, resume=resume, model=model,
            max_turns=max_turns, max_budget_usd=max_budget_usd,
        ):
            yield event
        return

    async for event in _extract_diffs_core(
        user_prompt, house, recent_requirements,
        session_id=session_id, resume=resume, model=model,
        max_turns=max_turns, max_budget_usd=max_budget_usd,
    ):
        yield event


async def _extract_diffs_core(
    user_prompt: str,
    house: House,
    recent_requirements: list[Requirement],
    *,
    session_id: Optional[str] = None,
    resume: Optional[str] = None,
    model: Optional[str] = None,
    max_turns: int = 12,
    max_budget_usd: Optional[float] = None,
) -> AsyncIterator[dict[str, Any]]:
    """Core implementation that requires a subprocess-capable event loop."""
    tools = _make_tools(house, recent_requirements)
    server = create_sdk_mcp_server(name="goa", tools=tools)

    options = ClaudeAgentOptions(
        system_prompt=EXTRACTOR_SYSTEM_PROMPT,
        mcp_servers={"goa": server},
        # Lock the agent to ONLY our MCP tools; without these locks the spawned
        # `claude` CLI inherits built-ins (Bash, Edit, Read, etc.) plus the
        # ambient project's CLAUDE.md / hooks, which causes it to drift off
        # task (e.g. trying to git-commit on its own).
        tools=[],
        setting_sources=[],
        allowed_tools=[
            "mcp__goa__get_house",
            "mcp__goa__list_recent_requirements",
            "mcp__goa__validate_projection",
            "mcp__goa__room_geometry_hint",
        ],
        model=model,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        permission_mode="dontAsk",
        session_id=session_id,
        resume=resume,
    )

    last_assistant_text = ""
    try:
        async for msg in query(prompt=user_prompt, options=options):
            if not isinstance(msg, AssistantMessage):
                continue
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    yield {
                        "type": "status",
                        "tool": block.name,
                        "label": friendly_tool_label(block.name),
                    }
            chunks = [b.text for b in msg.content if isinstance(b, TextBlock)]
            text = "".join(chunks).strip()
            if text:
                last_assistant_text = text
    except Exception as exc:
        yield {"type": "error", "message": f"agent run failed: {exc}"}
        return

    parsed = parse_final_output(last_assistant_text)
    if parsed is None:
        yield {
            "type": "error",
            "message": (
                "extractor did not produce a parseable ExtractorResult; "
                f"last assistant message was: {last_assistant_text[:300]!r}"
            ),
        }
        return
    yield {"type": "result", "extractor_result": parsed.model_dump(mode="json")}


async def _extract_via_thread(
    user_prompt: str,
    house: House,
    recent_requirements: list[Requirement],
    **kwargs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Run _extract_diffs_core on a background thread with a ProactorEventLoop.

    Events are shuttled back to the caller's loop via an asyncio.Queue.
    """
    caller_loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Optional[dict[str, Any]]] = asyncio.Queue()
    _SENTINEL = None  # marks end of stream

    def _run() -> None:
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                _collect_into_queue(
                    queue, caller_loop,
                    user_prompt, house, recent_requirements, **kwargs,
                )
            )
        finally:
            loop.close()

    async def _collect_into_queue(
        q: asyncio.Queue[Optional[dict[str, Any]]],
        target_loop: asyncio.AbstractEventLoop,
        prompt: str,
        h: House,
        reqs: list[Requirement],
        **kw: Any,
    ) -> None:
        try:
            async for event in _extract_diffs_core(prompt, h, reqs, **kw):
                target_loop.call_soon_threadsafe(q.put_nowait, event)
        except Exception as exc:
            target_loop.call_soon_threadsafe(
                q.put_nowait,
                {"type": "error", "message": f"agent run failed: {exc}"},
            )
        finally:
            target_loop.call_soon_threadsafe(q.put_nowait, _SENTINEL)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    while True:
        event = await queue.get()
        if event is None:
            break
        yield event

    thread.join(timeout=5)


async def extract_diffs(
    user_prompt: str,
    house: House,
    recent_requirements: list[Requirement],
    *,
    session_id: Optional[str] = None,
    resume: Optional[str] = None,
    model: Optional[str] = None,
    max_turns: int = 12,
    max_budget_usd: Optional[float] = None,
) -> ExtractorResult:
    """Non-streaming wrapper around `extract_diffs_stream` — consumes the
    stream and returns the final ExtractorResult, raising ExtractorError
    on failure."""
    final: Optional[ExtractorResult] = None
    err: Optional[str] = None
    async for event in extract_diffs_stream(
        user_prompt,
        house,
        recent_requirements,
        session_id=session_id,
        resume=resume,
        model=model,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
    ):
        if event["type"] == "result":
            final = ExtractorResult.model_validate(event["extractor_result"])
        elif event["type"] == "error":
            err = event["message"]

    if err is not None:
        raise ExtractorError(err)
    if final is None:
        raise ExtractorError("extractor stream ended without a result")
    return final

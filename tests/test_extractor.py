from __future__ import annotations

import json

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

from goa_house.agents.extractor import (
    EXTRACTOR_SYSTEM_PROMPT,
    ExtractorError,
    _make_tools,
    compute_geometry_hint,
    extract_diffs,
    extract_diffs_stream,
    friendly_tool_label,
    parse_final_output,
)
from goa_house.diffs import ExtractorResult
from goa_house.state import (
    Camera,
    House,
    Opening,
    Plot,
    Room,
    Setbacks,
)


PLOT = Plot(
    boundary=[(0, 0), (20, 0), (20, 15), (0, 15)],
    north_deg=0.0,
    setbacks=Setbacks(front=3.0, rear=3.0, side=2.0),
)


def _living() -> Room:
    return Room(
        id="living_room",
        name="Living Room",
        polygon=[(3, 3), (10, 3), (10, 9), (3, 9)],
        openings=[Opening(type="window", wall="N", position_m=2.0, width_m=1.5)],
        camera=Camera(x=6.5, y=6.0, z=1.6, yaw_deg=0.0),
    )


def _house() -> House:
    return House(plot=PLOT, rooms=[_living()])


# ---- compute_geometry_hint ----------------------------------------------


def test_geometry_hint_ne_corner():
    h = compute_geometry_hint(_house(), "NE", 4.0, 5.0, 0)
    # buildable bounds: side=2, front=3 → (2,3) to (18,12). NE → (14,7)..(18,12)
    poly = h["polygon"]
    assert poly[0] == [14.0, 7.0]
    assert poly[2] == [18.0, 12.0]
    assert h["camera"]["x"] == 16.0
    assert h["camera"]["y"] == 9.5
    assert h["floor"] == 0


def test_geometry_hint_sw_corner():
    h = compute_geometry_hint(_house(), "sw", 3.0, 3.0, 0)
    assert h["polygon"][0] == [2.0, 3.0]
    assert h["polygon"][2] == [5.0, 6.0]


def test_geometry_hint_center():
    h = compute_geometry_hint(_house(), "center", 4.0, 4.0, 1)
    # bw=16, bh=9; center → (2 + 6, 3 + 2.5) = (8, 5.5)
    assert h["polygon"][0] == [8.0, 5.5]
    assert h["floor"] == 1


def test_geometry_hint_too_large_raises():
    with pytest.raises(ValueError, match="does not fit"):
        compute_geometry_hint(_house(), "NE", 50.0, 50.0, 0)


def test_geometry_hint_unknown_corner_raises():
    with pytest.raises(ValueError, match="unknown corner"):
        compute_geometry_hint(_house(), "UP", 4.0, 5.0, 0)


def test_geometry_hint_negative_raises():
    with pytest.raises(ValueError, match="positive"):
        compute_geometry_hint(_house(), "NE", -1.0, 5.0, 0)


# ---- parse_final_output --------------------------------------------------


def _diffs_payload() -> str:
    return json.dumps(
        {
            "kind": "diffs",
            "diffs": [
                {
                    "proposed": {
                        "scope": "global",
                        "type": "material",
                        "statement": "Use Mangalore tile roof.",
                    },
                    "affected_rooms": [],
                    "conflicts_with": [],
                    "suggested_resolution": None,
                    "source_span": "Mangalore tile roof",
                    "mutation": None,
                }
            ],
        }
    )


def test_parse_output_tag():
    text = f"some prose <output>{_diffs_payload()}</output> more prose"
    result = parse_final_output(text)
    assert result is not None
    assert result.kind == "diffs"
    assert len(result.diffs) == 1


def test_parse_code_fence():
    text = f"```json\n{_diffs_payload()}\n```"
    result = parse_final_output(text)
    assert result is not None
    assert result.kind == "diffs"


def test_parse_raw_json():
    result = parse_final_output(_diffs_payload())
    assert result is not None
    assert result.kind == "diffs"


def test_parse_clarification():
    payload = json.dumps({"kind": "clarification", "question": "How big?"})
    text = f"<output>{payload}</output>"
    result = parse_final_output(text)
    assert result is not None
    assert result.kind == "clarification"
    assert result.question == "How big?"


def test_parse_malformed_returns_none():
    assert parse_final_output("nothing structured here") is None
    assert parse_final_output("<output>not json</output>") is None
    assert parse_final_output("") is None


# ---- tool factory --------------------------------------------------------


@pytest.mark.asyncio
async def test_get_house_tool_returns_current_state():
    house = _house()
    tools = _make_tools(house, [])
    by_name = {t.name: t for t in tools}
    result = await by_name["get_house"].handler({})
    payload = json.loads(result["content"][0]["text"])
    assert payload["rooms"][0]["id"] == "living_room"


@pytest.mark.asyncio
async def test_validate_projection_tool_clean_for_no_diffs():
    house = _house()
    tools = _make_tools(house, [])
    by_name = {t.name: t for t in tools}
    result = await by_name["validate_projection"].handler({"diffs_json": "[]"})
    issues = json.loads(result["content"][0]["text"])
    assert issues == []


@pytest.mark.asyncio
async def test_validate_projection_tool_surfaces_invalid_diff():
    house = _house()
    tools = _make_tools(house, [])
    by_name = {t.name: t for t in tools}
    # malformed diffs_json
    result = await by_name["validate_projection"].handler({"diffs_json": "{not json"})
    issues = json.loads(result["content"][0]["text"])
    assert issues[0]["code"] == "diff_invalid"


@pytest.mark.asyncio
async def test_geometry_hint_tool_returns_polygon():
    house = _house()
    tools = _make_tools(house, [])
    by_name = {t.name: t for t in tools}
    result = await by_name["room_geometry_hint"].handler(
        {"corner": "NE", "width_m": 4.0, "depth_m": 5.0, "floor": 0}
    )
    payload = json.loads(result["content"][0]["text"])
    assert payload["polygon"][0] == [14.0, 7.0]


@pytest.mark.asyncio
async def test_geometry_hint_tool_returns_error_object_on_bad_input():
    house = _house()
    tools = _make_tools(house, [])
    by_name = {t.name: t for t in tools}
    result = await by_name["room_geometry_hint"].handler(
        {"corner": "NE", "width_m": 999.0, "depth_m": 999.0, "floor": 0}
    )
    payload = json.loads(result["content"][0]["text"])
    assert "error" in payload


def test_system_prompt_mentions_each_tool():
    for name in ("get_house", "list_recent_requirements", "validate_projection", "room_geometry_hint"):
        assert name in EXTRACTOR_SYSTEM_PROMPT


def test_extractor_result_round_trip_via_pydantic():
    payload = json.loads(_diffs_payload())
    result = ExtractorResult.model_validate(payload)
    assert result.diffs[0].proposed.statement == "Use Mangalore tile roof."


# ---- Streaming + tool-label mapping -------------------------------------


def test_friendly_label_maps_known_names():
    assert friendly_tool_label("mcp__goa__get_house") == "Reading the plan…"
    assert friendly_tool_label("mcp__goa__validate_projection") == "Checking that it fits…"
    assert friendly_tool_label("get_house") == "Reading the plan…"


def test_friendly_label_falls_back_for_unknown():
    assert friendly_tool_label("mystery_tool") == "Working (mystery_tool)…"


def _assistant_msg(*blocks) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model="claude-sonnet-4-6")


def _final_text() -> str:
    payload = json.dumps(
        {
            "kind": "diffs",
            "diffs": [
                {
                    "proposed": {
                        "scope": "global",
                        "type": "material",
                        "statement": "Use Mangalore tile roof.",
                    },
                    "affected_rooms": [],
                    "conflicts_with": [],
                    "suggested_resolution": None,
                    "source_span": "Mangalore tile roof",
                    "mutation": None,
                }
            ],
        }
    )
    return f"<output>{payload}</output>"


def _patch_query(monkeypatch: pytest.MonkeyPatch, messages: list):
    """Replace claude_agent_sdk.query with a stub that yields `messages`."""

    async def fake_query(*, prompt, options=None, transport=None):
        for m in messages:
            yield m

    monkeypatch.setattr("goa_house.agents.extractor.query", fake_query)


@pytest.mark.asyncio
async def test_stream_emits_status_for_each_tool_use(monkeypatch: pytest.MonkeyPatch):
    msgs = [
        _assistant_msg(ToolUseBlock(id="t1", name="mcp__goa__get_house", input={})),
        _assistant_msg(
            ToolUseBlock(id="t2", name="mcp__goa__validate_projection", input={"diffs_json": "[]"})
        ),
        _assistant_msg(TextBlock(_final_text())),
    ]
    _patch_query(monkeypatch, msgs)

    events: list[dict] = []
    async for ev in extract_diffs_stream("hi", _house(), []):
        events.append(ev)

    statuses = [e for e in events if e["type"] == "status"]
    assert [s["label"] for s in statuses] == [
        "Reading the plan…",
        "Checking that it fits…",
    ]
    assert events[-1]["type"] == "result"
    assert events[-1]["extractor_result"]["kind"] == "diffs"


@pytest.mark.asyncio
async def test_stream_emits_error_on_unparseable_output(monkeypatch: pytest.MonkeyPatch):
    msgs = [_assistant_msg(TextBlock("not structured at all"))]
    _patch_query(monkeypatch, msgs)

    events = [e async for e in extract_diffs_stream("hi", _house(), [])]
    assert events[-1]["type"] == "error"
    assert "did not produce" in events[-1]["message"]


@pytest.mark.asyncio
async def test_stream_emits_error_when_query_raises(monkeypatch: pytest.MonkeyPatch):
    async def boom(*, prompt, options=None, transport=None):
        raise RuntimeError("boom")
        yield  # pragma: no cover  — make this a generator

    monkeypatch.setattr("goa_house.agents.extractor.query", boom)
    events = [e async for e in extract_diffs_stream("hi", _house(), [])]
    assert events[-1]["type"] == "error"
    assert "boom" in events[-1]["message"]


@pytest.mark.asyncio
async def test_extract_diffs_returns_final_result(monkeypatch: pytest.MonkeyPatch):
    msgs = [_assistant_msg(TextBlock(_final_text()))]
    _patch_query(monkeypatch, msgs)

    result = await extract_diffs("hi", _house(), [])
    assert result.kind == "diffs"
    assert len(result.diffs) == 1


@pytest.mark.asyncio
async def test_extract_diffs_raises_on_error(monkeypatch: pytest.MonkeyPatch):
    msgs = [_assistant_msg(TextBlock("nonsense"))]
    _patch_query(monkeypatch, msgs)

    with pytest.raises(ExtractorError):
        await extract_diffs("hi", _house(), [])


@pytest.mark.asyncio
async def test_stream_propagates_session_kwargs(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    async def fake_query(*, prompt, options=None, transport=None):
        captured["session_id"] = options.session_id
        captured["resume"] = options.resume
        yield _assistant_msg(TextBlock(_final_text()))

    monkeypatch.setattr("goa_house.agents.extractor.query", fake_query)

    async for _ in extract_diffs_stream(
        "hi", _house(), [], session_id="abc", resume=None
    ):
        pass
    assert captured == {"session_id": "abc", "resume": None}

    captured.clear()
    async for _ in extract_diffs_stream(
        "hi", _house(), [], session_id=None, resume="prev"
    ):
        pass
    assert captured == {"session_id": None, "resume": "prev"}

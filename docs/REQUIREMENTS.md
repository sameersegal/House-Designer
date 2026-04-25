# Requirements — Goa House Designer

A local-first web app that lets the user iteratively design a house on a fixed
plot via natural-language prompts. The system maintains a structured source of
truth (plot + layout + requirements log), generates equirectangular panoramas
per room grounded in that state, and renders a navigable Pannellum tour. Each
prompt is parsed into requirement diffs that the user approves before state
mutates.

## Tech stack

- **Backend:** Python 3.11+, FastAPI, Pydantic v2
- **Agents:** Anthropic SDK (Claude Sonnet 4.6 for extraction/validation)
- **Image gen:** OpenAI **gpt-image-2** (equirectangular panos, image-conditioned)
- **Geometry:** Shapely, matplotlib
- **Frontend:** Single-page vanilla JS + Pannellum, served by FastAPI
- **Storage:** JSON files on disk, Git-versioned (no DB)
- **Target OS:** Windows dev (PowerShell), Linux-compatible

## Data model

`state/house.json` is the source of truth: `version`, `plot` (boundary,
`north_deg`, setbacks), `rooms[]` (id, name, polygon, floor, ceiling height,
openings, camera), `style` (period, materials, lighting, mood tokens).

`state/requirements.jsonl` is an append-only log of `Requirement` records
typed `orientation | dimension | adjacency | material | feature | constraint`,
with status transitions `proposed → approved | rejected | superseded`.

## Functional requirements

- **FR1 — State bootstrap.** Load `house.json`; init from a plot polygon if
  missing. Validate on load; fail loudly with specific errors.
- **FR2 — Prompt intake.** `POST /prompt {text}` → extractor returns 0..N
  diffs (each with proposed requirement, affected rooms, conflicts, suggested
  resolution). Does **not** mutate state.
- **FR3 — Human approval gate.** `POST /requirements/approve {diff_ids}` and
  `POST /requirements/reject {diff_ids, reason?}`. Approval appends to log,
  marks superseded, and updates `house.json` for geometry-affecting diffs.
- **FR4 — Layout validation.** Deterministic checks before geometry mutation:
  rooms inside plot minus setbacks; no overlaps; door-graph connectivity;
  openings on walls. Hard failures block; soft issues warn.
- **FR5 — Massing image generation.** Top-down plot (all rooms, north arrow,
  highlight target room, camera + facing) plus a crude first-person massing
  block (walls, door/window rectangles, no textures) per room.
- **FR6 — Panorama generation.** Build a deterministic prompt per room (style
  + room facts + scoped requirements + camera + output spec); call
  `gpt-image-2` with the massing image as reference; request 4096×2048
  equirectangular. Post-validate aspect and seam continuity. Save versioned
  to `state/panos/{room_id}.v{n}.jpg`.
- **FR7 — Selective regeneration.** On approved diffs compute the affected
  room set (target + neighbors if adjacency changed). Re-render only those.
- **FR8 — Tour assembly.** Generate Pannellum config from `house.json`: one
  scene per room; door hotspots from camera↔opening geometry; compass HUD
  driven by `north_deg` and per-scene `northOffset`.
- **FR9 — Web UI.** Single page, three panes: prompt + requirements log
  (left), Pannellum viewer + room switcher + compass (center), pending
  diffs panel with approve/reject and inline conflict warnings (right).
- **FR10 — Undo.** Each approved change writes a new `house.v{n}.json`
  snapshot and a new requirements entry. `POST /undo` reverts to the
  previous snapshot and marks the latest requirements as superseded.

## Agent contracts

**Extractor** — Input: `user_prompt`, `house`, last 20 approved requirements.
Output: JSON array of proposed diffs (cite the user-prompt span justifying
each), or a clarifying question when conversational/ambiguous.

**Prompt builder** — Deterministic, not LLM. Input: `room_id`, `house`.
Output: prompt with sections `[STYLE] [ROOM FACTS] [REQUIREMENTS] [CAMERA]
[OUTPUT SPEC]`.

## Non-functional requirements

- **Determinism of state.** Same `requirements.jsonl` reproduces the same
  `house.json`. Provide a replay command.
- **Idempotent renders.** Same state + same seed → same pano filename;
  skip regeneration if the input hash is unchanged.
- **Cost guardrails.** Config-level cap on panos per session; warn before
  exceeding.
- **Logging.** Every LLM call (prompt, response, tokens, cost) is logged to
  `state/logs/`.
- **Windows compatibility.** All paths via `pathlib`; no shell-specific
  commands in code.

## Build order

1. Pydantic models + load/save + validation (`state.py`) with tests.
2. Plot/massing renderer (`render/massing.py`).
3. Pannellum tour builder from a hand-authored fixture `house.json`.
4. Prompt builder + panorama renderer with a single hardcoded room.
5. Extractor agent + approval API.
6. Validator + conflict detection in the approve flow.
7. Selective regeneration + undo.
8. Web UI polish.

## Out of scope (v1)

- Multi-floor stairs geometry (single floor only; `floor` field reserved).
- True 6DoF walkthrough.
- Real-time collaboration.
- Export to CAD/IFC.
- Cost estimation.

## Acceptance criteria

- Fresh clone → `uv sync` → `python -m goa_house.cli init --plot
  fixtures/plot.json` → `uvicorn goa_house.api:app` → browser shows empty plot.
- "Add a 4×5m master bedroom in the NE corner with a window facing north"
  produces one diff, approves cleanly, renders one pano, shows it in the tour.
- "Move the kitchen to the SW" with no kitchen defined returns a clarifying
  question, not a diff.
- A conflicting prompt is flagged with the specific conflicting `req_id`.
- Deleting `house.json` and running replay reconstructs identical state from
  `requirements.jsonl`.

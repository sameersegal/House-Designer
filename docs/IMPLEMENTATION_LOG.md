# Implementation Log

Tracks what is built vs pending against the [Requirements](./REQUIREMENTS.md).
Update on every merged change.

## Done

### Build step 1 — State, validation, extractor prompt
- `pyproject.toml` with FastAPI, Pydantic v2, Shapely, matplotlib, Pillow,
  Anthropic + OpenAI SDKs; `[src]` layout; pytest configured.
- `src/goa_house/state.py` — Pydantic models: `House`, `Plot`, `Room`,
  `Opening`, `Camera`, `Style`, `Requirement`, `ValidationIssue`. Cross-field
  invariants: doors must reference `to_room`; windows must not; room ids
  snake_case; unique room ids per house.
- Load/save: `load_house`, `save_house` writes `house.v{n}.json` snapshots;
  append-only `requirements.jsonl` with auto-numbered `req_XXXX` ids.
- `validate_house` returns structured issues by code: `plot_invalid`,
  `room_invalid`, `room_outside_plot`, `room_violates_setback`,
  `room_overlap`, `room_unreachable`, `door_target_missing`,
  `opening_off_wall`. Hard vs soft severity.
- Directional setback envelope for axis-aligned plots with `north_deg=0`;
  fallback to max-inset buffer for arbitrary polygons.
- `src/goa_house/agents/extractor.py` — `EXTRACTOR_SYSTEM_PROMPT` drafted
  (diffs-or-clarification contract, conflict detection, compass rules,
  `source_span` quoting). LLM call stub waiting on step 5.
- `fixtures/plot.json` — 20×15m plot, 3/3/2m setbacks.

### Build step 2 — Top-down massing renderer
- `src/goa_house/render/massing.py` — matplotlib top-down: plot, setback
  envelope (dashed), rooms (highlight optional), door/window markers, camera
  star with yaw arrow, north arrow.
- *Pending sub-piece:* the crude first-person massing block (FR5 second
  half). Lands with build step 4 since it is the input to `gpt-image-2`.

### Build step 3 — Pannellum tour + viewer (sample)
- `fixtures/house.sample.json` — 3-room Goa house (Living, Kitchen, Master)
  on the 20×15m plot with a connected door graph, windows on
  cross-ventilated walls, Indo-Portuguese style.
- `src/goa_house/tour/pannellum.py` — `build_tour` returns a Pannellum
  config with one scene per room. Door hotspot yaw/pitch from
  camera↔opening geometry; per-scene `northOffset` from camera yaw and
  `north_deg`.
- `src/goa_house/render/placeholder.py` — procedural 2048×1024 equirect
  panos: compass letters, labelled door/window outlines at correct
  yaw/pitch, room title, seam-wrapped left/right column. Lets the tour be
  walked before any LLM/image-gen runs.
- `src/goa_house/api.py` — FastAPI app serving `/`, `/tour.json`,
  `/house.json`, `/static/*`, `/panos/*`, `/massing/*`.
- `src/goa_house/cli.py` — `init`, `build-sample`, `build-tour`, `validate`
  subcommands.
- `web/index.html` + `web/app.js` — three-pane UI with room switcher, live
  compass needle, per-room info, style chips. Pannellum from CDN.

### Tests (66 green)
- State invariants and validation codes (18 cases).
- Tour builder hotspot math, sample-fixture round trip (8 cases).
- Placeholder pano aspect, top-down render, full CLI build pipeline (4).

### Build step 4 — Prompt builder + panorama renderer
- `src/goa_house/agents/prompt_builder.py` — deterministic prompt with
  `[STYLE] [ROOM FACTS] [ROOM CHARACTER] [REQUIREMENTS] [CAMERA]
  [OUTPUT SPEC]`. Wall labels mapped to compass via `north_deg`. The
  `[ROOM CHARACTER]` block injects per-room-type furnishing cues (sala,
  Goan kitchen, dining, bedroom, stairwell, landing, etc.) plus a stair
  UP/DOWN narrative on rooms with a `stairs` opening — explicitly
  describing a continuous floor for ground-level stair rooms and a
  balustraded floor void for upper-level landings.
- `src/goa_house/render/panorama.py` — `gpt-image-2` (`client.images.edit`)
  conditioned on the placeholder pano as reference. JPEG output to
  `<panos_dir>/<room>.jpg`; `.massing.png` debug sidecar; `.hash`
  sidecar (`sha256(prompt + ref_bytes + model + quality + size +
  fidelity)`) for idempotent skip; `--force` to override.
- Placeholder pano stripped of painted text labels (compass letters, room
  name, opening labels) — image-edit models were preserving them as
  signage on wall surfaces. Replaced with tiny tick marks at the
  cornice/skirting for orientation only.
- JSONL call log at `state/logs/image_calls.jsonl` (one record per
  call, success or failure, with token usage).
- CLI: `goa-house render-room <room_id>` and `render-all` with
  `--quality`, `--size`, `--force` flags; `OPENAI_API_KEY` env var.

### Build step 4.5 — Multi-floor support + two-floor sample
- `Room.floor: int` field; per-floor uniqueness for room overlap; new
  `Opening.type="stairs"` with paired source/target rooms on different
  floors.
- Validation codes added: `door_crosses_floors`, `stairs_same_floor`,
  `stairs_target_missing`. Door-or-stairs graph connectivity replaces
  the previous door-only check.
- `tour.pannellum.build_tour` — stair openings render as scene-link
  hotspots with `text="Go up/down to X"` and
  `cssClass="goa-stairs goa-stairs-{up,down}"`. Stair pitch aims at
  mid-flight (UP) or floor void (DOWN); doors keep door-center pitch.
- `render.massing.render_topdown` — per-floor PNGs
  (`topdown-floor{N}.png`) plus per-room overviews scoped to the
  room's floor.
- Viewer: header design dropdown; rooms grouped under
  "Ground floor" / "First floor" headings; floor-overview tabs above
  the topdown image; stair hotspots styled with up/down arrow glyphs.
- `designs/goa-two-floor/` — 8 rooms (living, kitchen, dining,
  stairwell_g, master_bedroom, bedroom_2, bedroom_3, landing) with
  paired stairs.

> Note: panos in `designs/goa-two-floor/panos/` were rendered against
> the older labeled placeholder. Re-run `goa-house render-all
> --house designs/goa-two-floor/house.json --panos-dir
> designs/goa-two-floor/panos --force` to pick up the cleaned-up
> placeholder + the [ROOM CHARACTER] / stair-direction prompt blocks.

## Pending

### Build step 5 — Extractor agent + approval API
- [ ] Wire `extract_diffs` to call Claude Sonnet 4.6 with the existing
      `EXTRACTOR_SYSTEM_PROMPT`; validate response against a
      `RequirementDiff` Pydantic model; one retry on malformed JSON.
- [ ] `POST /prompt` — runs extractor, returns diffs or clarification, no
      mutation.
- [ ] `POST /requirements/approve` — assign `req_XXXX`, validate proposed
      mutation, append to log, mark superseded targets, mutate `house.json`
      for `dimension`/`feature`/`adjacency`, snapshot.
- [ ] `POST /requirements/reject` — log rejection with optional reason.
- [ ] Trigger selective re-render of affected rooms after approval.
- [ ] Right-pane Diffs panel in `web/app.js` with approve/reject and inline
      conflict warnings.

### Build step 6 — Validator + conflict detection in approve flow
- [ ] Run `validate_house` against the projected post-mutation state and
      block on hard issues with structured error payloads.
- [ ] Conflict detection: same-scope+type collisions, contradictory
      orientations/dimensions; surface `req_id`s in the diff response.

### Build step 7 — Selective regen + undo
- [ ] Compute affected room set on approval (room + adjacency neighbours).
- [ ] `POST /undo` reverts to previous `house.v{n-1}.json`; mark latest
      requirements `superseded`.
- [ ] `goa-house replay` rebuilds `house.json` from `requirements.jsonl`
      (determinism guarantee).

### Build step 8 — Web UI polish
- [ ] Requirements log filterable by scope/status with timeline view.
- [ ] Pending diffs panel: side-by-side before/after summary.
- [ ] Loading states during pano regen; toast on completion.
- [ ] Vendor Pannellum locally for fully offline operation (currently CDN).

### Cross-cutting / tech debt
- [ ] Directional setbacks for non-axis-aligned plots and `north_deg ≠ 0`.
- [ ] Opening-on-wall validation for non-axis-aligned room polygons (today
      assumes axis-aligned bounding box).
- [ ] First-person massing block image (FR5 second half) for image
      conditioning.
- [ ] Logging: structured per-call records under `state/logs/` with cost.

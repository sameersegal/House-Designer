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

### Build step 4 — Prompt builder + panorama renderer
- `src/goa_house/agents/prompt_builder.py` — deterministic builder.
  Sections `[STYLE] [ROOM FACTS] [REQUIREMENTS] [CAMERA] [OUTPUT SPEC]`.
  Wall labels resolve to 8-point compass via `wall_compass_direction`
  (uses `plot.north_deg`); camera yaw → `initial_facing`. Requirements
  filtered to approved + scope `room.id` or `global`. `STYLE_VERSION`
  constant feeds the idempotency hash.
- `src/goa_house/render/massing.py::render_first_person_block` —
  4096×2048 equirectangular massing block. Floor/ceiling polylines
  sampled along walls, vertical lines at room corners, openings as
  outlined rectangles (orange door / blue window), seam-wrap. Used as
  image-conditioning input to gpt-image-2.
- `src/goa_house/render/panorama.py` — `render_room_panorama`:
  - `OpenAIImagePanoramaClient` wraps `client.images.edit(model="gpt-image-2", ...)`;
    pluggable `PanoramaClient` Protocol for tests.
  - `compute_input_hash(prompt, ref_bytes, style_version)` →
    `state/panos/{room_id}.manifest.json` `{version, input_hash}`.
    Same hash + existing latest pointer → `skipped=True`.
  - Versioned outputs `state/panos/{room_id}.v{n}.jpg`; on success
    `{room_id}.jpg` points at the latest. `_next_version` defends
    against missing manifest with on-disk versions.
  - `validate_panorama` checks 2:1 aspect and seam continuity (mean
    per-channel diff column-0 vs column-W-1, default threshold 25).
    On failure the versioned file is kept for inspection but the
    latest pointer + manifest are not updated.
  - `RenderSession(cap, warn_threshold)` — per-session pano cap;
    raises `CostCapExceeded`; warn-log at threshold.
  - LLM call logging to `state/logs/{ts}_{room_id}_{hash8}.json`.
- CLI: `goa-house render-room <room_id>` and `render-all`. Both write
  the prompt + first-person block, then call `render_room_panorama`
  unless `--prompt-only` is set. Flags: `--cap`, `--seam-threshold`,
  `--log-dir`, `--requirements`.

### Tests (54 green)
- State invariants and validation codes (18 cases).
- Tour builder hotspot math, sample-fixture round trip (8 cases).
- Placeholder pano aspect, top-down render, full CLI build pipeline (4).
- Prompt builder: section presence, wall→compass with `north_deg`
  rotations, room-local camera, requirement filtering by status/scope,
  determinism, unknown-room error (10 cases).
- Panorama: hash sensitivity, session cap, seam ok/fail, aspect fail,
  first-person aspect, idempotency skip, hash-change → new version,
  post-validation failure keeps versioned file but not latest pointer
  (13 cases).
- CLI `render-room --prompt-only` end-to-end (1 case).

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
- [ ] Real cost accounting in `state/logs/` (currently logs prompt, hash,
      image byte count, validation issues — no token/$ figure since the
      images.edit response doesn't surface usage at call site).
- [ ] Verify `gpt-image-2` model id + `images.edit` payload shape against
      the live OpenAI SDK once credentials are wired (only the fake
      client is exercised in tests).

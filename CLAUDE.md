# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Local-first iterative house designer for a Goa plot. A Pydantic `House` model is the single source of truth; CLI subcommands fan that state out into placeholder panoramas, top-down massing PNGs, and a Pannellum tour config that a FastAPI app serves. Multiple designs live side-by-side under `designs/<name>/` and the viewer's header dropdown switches between them. An LLM-grounded "Requirement Extractor" agent (prompt drafted, wiring not yet implemented) is intended to translate natural-language prompts into reviewable diffs against the state.

## Common commands

```bash
# install dev deps (editable) — Python >=3.11
pip install -e ".[dev]"

# serve the viewer at http://127.0.0.1:8000 (loads designs/goa-sample by default)
uvicorn goa_house.api:app --reload

# initialize a fresh house.json from the empty-plot seed
goa-house init --plot fixtures/plot.json --out designs/<name>/house.json

# rebuild placeholder panos + per-floor and per-room top-downs for a design
goa-house build-tour                                       # default: designs/goa-sample/
goa-house build-tour --house designs/goa-two-floor/house.json \
    --panos-dir designs/goa-two-floor/panos \
    --massing-dir designs/goa-two-floor/massing
goa-house build-tour --no-panos                            # skip placeholder pano regen

# real panoramas via gpt-image-2 (needs OPENAI_API_KEY; defaults: low quality, 2048x1024)
goa-house render-room living_room                          # one room
goa-house render-all                                       # every room of the default design
goa-house render-room living_room --quality medium --force # bypass cache

# validate a house.json (exits non-zero on hard issues)
goa-house validate --house designs/goa-sample/house.json

# bake every committed design into a static GitHub Pages bundle (no server)
goa-house build-site --out-dir _site
# then preview locally with: python -m http.server --directory _site 8080

# tests
pytest                                       # full suite
pytest tests/test_state.py                   # one file
pytest tests/test_state.py::test_room_outside_plot_fails   # one test
pytest -k opening                            # by name pattern

# lint
ruff check .
```

`goa-house` is the console script defined in `pyproject.toml`; it dispatches to `goa_house.cli:main`. Default paths point at `designs/goa-sample/`; override per call with `--house`, `--panos-dir`, `--massing-dir`.

## Architecture

### Data model is the source of truth — `src/goa_house/state.py`

`House` (Pydantic) owns `Plot` (boundary + `north_deg` + `Setbacks`), a list of `Room` (snake_case id, polygon, `floor`, openings, `Camera`), and a `Style` block (Indo-Portuguese defaults: lime plaster, Mangalore tile, etc.). Geometry checks use Shapely.

Cross-cutting invariants enforced by validators — keep them when extending:
- Room ids are snake_case (`^[a-z][a-z0-9_]*$`) and unique within a House.
- `Opening.type` is one of `door | window | stairs`. Stairs require `to_room`; windows forbid it; doors *to another room* set `to_room`, but doors *without* `to_room` are exterior entries (no graph edge, silently skipped by the tour builder). The `CONNECTING_OPENINGS = ("door", "stairs")` constant centralises that grouping.
- `validate_house()` runs *semantic* checks beyond Pydantic: room-inside-plot, setback envelope, **per-floor** room overlap (rooms at the same xy on different floors don't trigger `room_overlap`), opening-on-wall (axis-aligned bbox walls N/S/E/W), `door_target_missing` / `stairs_target_missing`, `door_crosses_floors` (doors must stay on a single floor), `stairs_same_floor` (stairs must connect different floors), and door-or-stairs graph connectivity (every room reachable from `rooms[0]`). Hard issues block CLI commands.
- `buildable_area()` has a fast path for axis-aligned rectangles with `north_deg == 0`; rotated/non-rectangular plots fall back to a `Polygon.buffer(-max(setback))` inset, which is approximate. Setbacks are 2D and apply identically to every floor today.

Persistence: `save_house()` writes `house.json` AND a sibling `house.vN.json` snapshot (auto-incremented) for history. `Requirement` records are appended one-per-line to a JSONL log via `append_requirement()`; `Requirement.next_id()` produces `req_0001`-style ids.

### Pipeline: house.json → artifacts → viewer

`cli.py::_emit_artifacts()` orchestrates the build:
1. `render.placeholder.render_all_placeholders()` — one 2048×1024 equirectangular JPEG per room. Procedural: hashed wall color, painted compass markers, painted door / window / stairs rectangles (yellow / blue / purple). Used until real panoramas exist; also serves as the *reference image* fed to `gpt-image-2` edits.
2. `render.massing.render_topdown(house, out_path, floor=, highlight_room_id=)` — Matplotlib top-down PNG. Written as `topdown.png` (overview, floor 0), `topdown-floor{N}.png` per floor when there's more than one, and `<room_id>/topdown.png` highlighting that room scoped to its floor.
3. `tour.pannellum.build_tour()` — produces the Pannellum scenes dict. Door + stairs become scene-link hotspots via `door_hotspot_angles()` (same machinery for both). Stairs hotspots add `text="Go up/down to X"` and `cssClass="goa-stairs goa-stairs-{up,down}"` for future styling. `northOffset` per scene is `wrap_180(camera.yaw_deg − plot.north_deg)`. The API builds tour.json on demand — no static file.

`render/placeholder.py` and `tour/pannellum.py` share `opening_center()` and `wrap_180()` — the placeholder pano draws door / window / stairs markers using the same yaw math the tour uses for hotspots, so they stay aligned. If you change one, change both or hoist the geometry helpers somewhere shared.

### Designs folder convention — `designs/<name>/`

Each subdirectory is one self-contained house design: `house.json` + `panos/<room_id>.jpg` + `massing/topdown.png` + `massing/<room_id>/topdown.png` (+ `massing/topdown-floor{N}.png` for multi-floor) (+ optional `requirements.jsonl`). `goa-sample/` (single floor, real `gpt-image-2` panos) and `goa-two-floor/` (8 rooms, paired stairs hotspots, placeholder panos) are the committed examples. The `_safe_segment` check in [api.py](src/goa_house/api.py) plus `Path.resolve().relative_to()` guards against path traversal in the design name and any nested filename.

A design may include `designs/<name>/requirements.jsonl` — one approved-status `Requirement` per line. `goa-house render-room` / `render-all` automatically loads that file (via `_design_requirements()` in `cli.py`) and forwards the records to `render_panorama()`, where the prompt builder filters them by `scope in (room.id, "global")` and emits them in the `[REQUIREMENTS]` section. This is how design-time intent (zone descriptions inside an open-plan room, shared palette across the whole house, etc.) is fed into the panorama prompts without baking it into `_ROOM_CHARACTER`.

### Viewer — `src/goa_house/api.py` + `web/`

FastAPI app (`goa_house.api:app`) exposes:
- `GET /designs.json` → `{"designs": [...]}` listing every subdirectory of `designs/` that contains a `house.json`. (The viewer fetches this via the relative URL `designs.json`.)
- `GET /designs/{name}/house.json` — pass-through.
- `GET /designs/{name}/tour.json` — built on the fly with `panorama_url` set to the relative `designs/{name}/panos/{rid}.jpg?v={mtime}` so the same `tour.json` works in both the FastAPI dev server and the static GitHub Pages build.
- `GET /designs/{name}/panos/{file:path}` and `.../massing/{file:path}` — static via `FileResponse`, with segment validation + resolved-path containment.
- `GET /static/...` mounts `web/` for the index + JS.

`web/index.html` + `web/app.js` load Pannellum from CDN, populate a header `<select>` with the design names (selection mirrored to `?design=<name>` for shareable URLs), destroy + rebuild the viewer when the selection changes, and group the room list by floor with `Ground floor` / `First floor` headings when the design has more than one floor. All viewer-side URLs are relative (`designs.json`, `designs/<name>/...`, `static/app.js`) so the same HTML/JS bundle works under FastAPI at `/` and under GitHub Pages at `/<repo>/`.

### Static deploy — `goa-house build-site` + `.github/workflows/deploy-pages.yml`

`goa-house build-site --out-dir _site` materialises a fully static, read-only viewer bundle:

```
_site/
  index.html                            # copied verbatim from web/
  static/app.js                         # copied verbatim from web/
  designs.json                          # the manifest the viewer fetches at startup
  designs/<name>/house.json
  designs/<name>/tour.json              # built statically (relative pano URLs + mtime ?v=)
  designs/<name>/panos/<rid>.jpg        # .hash / .massing.png debug sidecars are filtered out
  designs/<name>/massing/...
```

The companion `deploy-pages.yml` workflow runs on every push to `main`, installs the package, runs `goa-house build-site`, and uploads `_site/` via `actions/upload-pages-artifact` + `actions/deploy-pages`. GitHub Pages must be enabled for the repo with the source set to "GitHub Actions" (Settings → Pages). The deployed site is read-only — there are no mutation endpoints to begin with, so there's nothing to gate; the static bundle just doesn't run any Python.

### Panorama renderer — `src/goa_house/render/panorama.py`

`render_panorama()` calls `client.images.edit(model="gpt-image-2", image=<reference>, prompt=<built>, size=..., quality=...)`, decodes the base64 PNG, and re-saves as JPEG to `<panos_dir>/{room_id}.jpg`. Defaults: `quality="low"`, `size="2048x1024"`, model `gpt-image-2` — overridable per call or via `GOA_HOUSE_IMAGE_{MODEL,QUALITY,SIZE,FIDELITY}` env vars.

`gpt-image-2` rejects `input_fidelity` (the cookbook page is out of date) — the renderer only forwards it when explicitly set via env var or call argument.

The reference image is the existing equirectangular placeholder pano (`render_placeholder_pano`) — it already encodes wall + door + window + stairs geometry at correct yaw/pitch, so the model's job is to texture, not lay out. A `.massing.png` sidecar is written next to the JPEG for debugging.

Idempotency: a `<room>.hash` sidecar holds `sha256(prompt + reference_bytes + model + quality + size + fidelity)`; same inputs skip the API call. Pass `force=True` (or `--force` on the CLI) to bypass. JSONL call log (one record per call, success or failure, with token usage) lands in `state/logs/image_calls.jsonl`.

### Prompt builder — `src/goa_house/agents/prompt_builder.py`

Deterministic, no LLM. `build_panorama_prompt(house, room, requirements, output_size)` emits five sections `[STYLE] [ROOM FACTS] [REQUIREMENTS] [CAMERA] [OUTPUT SPEC]` in that order. Wall labels (N/S/E/W) are mapped to real-world compass octants via `(_WALL_BEARING[wall] + plot.north_deg) % 360` — `north_deg` is the bearing of plot +y from true north, matching the convention in `tour/pannellum.py`. The `[REQUIREMENTS]` section filters to `status == "approved"` and `scope in (room.id, "global")`; the `[OUTPUT SPEC]` repeats the seam-continuity and "preserve geometry from reference" constraints on every call (per the cookbook's "repeat the preserve list" guidance).

### Extractor agent (not yet wired) — `src/goa_house/agents/extractor.py`

Holds the system prompt + input shape for a Requirement Extractor that proposes `RequirementDiff` objects (or a clarification) for human review. `extract_diffs()` raises `NotImplementedError` — LLM wiring is a future step (the extractor must produce *diffs*, never mutate state directly).

## State directory

`state/` is gitignored runtime scratch. Today only `state/logs/image_calls.jsonl` lands there (gpt-image-2 call records). The directory is reserved for future "working copy" use (e.g. an editable house.json that hasn't been promoted to a committed design yet). Anything *committed* belongs under `designs/<name>/` or `fixtures/plot.json`.

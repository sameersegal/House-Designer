# Goa House Designer

Local-first iterative designer for a Goa house. A `house.json` per design describes plot, rooms, openings, and per-room camera. From that file the tool renders 360° panoramas (procedural placeholders or real `gpt-image-2` outputs), top-down massing plans, and a [Pannellum](https://pannellum.org/) tour config that a small FastAPI app serves so you can walk through the house in a browser. Multiple designs sit side by side under `designs/` and the viewer's header dropdown switches between them.

The longer-term plan is an LLM "Requirement Extractor" that turns natural-language prompts ("master bedroom faces NE, 4×5 m") into reviewable diffs against the state. The prompt is drafted in [src/goa_house/agents/extractor.py](src/goa_house/agents/extractor.py); LLM wiring is not yet implemented.

## Status

Early MVP. Two committed designs ([designs/goa-sample/](designs/goa-sample/) — single floor, real `gpt-image-2` panos at `quality=low`; [designs/goa-two-floor/](designs/goa-two-floor/) — 8 rooms across two floors with paired stairs hotspots, placeholder panos). Data model, validation, top-down rendering, Pannellum tour pipeline, and image-conditioned panorama renderer are all working end to end.

## Requirements

- Python ≥ 3.11
- `OPENAI_API_KEY` in the environment for real panorama renders (placeholders work without it)

## Quickstart

```bash
pip install -e ".[dev]"

# serve the viewer at http://127.0.0.1:8000 — opens with goa-sample by default,
# header dropdown switches to goa-two-floor (or any other folder under designs/)
uvicorn goa_house.api:app --reload
```

Open the URL and click rooms in the left sidebar (or click door / stairs hotspots in the panorama) to walk between them. The right panel shows room dimensions, openings, camera, and the design's Indo-Portuguese style block.

## Rendering panoramas

Real panoramas are produced by [gpt-image-2](https://platform.openai.com/docs/guides/image-generation) edits, conditioned on the placeholder pano (which encodes wall + door + window + stairs geometry at the right yaw/pitch). Defaults: `quality=low`, `size=2048x1024` for fast iteration.

```bash
# render one room (against the default goa-sample design)
goa-house render-room living_room

# render every room
goa-house render-all

# bypass cache / change quality / target a different design
goa-house render-room living_room --quality medium --force
goa-house render-room master_bedroom --house designs/goa-two-floor/house.json \
    --panos-dir designs/goa-two-floor/panos
```

Each call writes `<room>.jpg` plus a `<room>.hash` sidecar with `sha256(prompt + reference_bytes + model + quality + size + fidelity)`; identical inputs skip the API call. Pass `--force` to bypass. Per-call records (timing, token usage, errors) land in `state/logs/image_calls.jsonl`.

## Working from a blank plot

```bash
mkdir designs/my-design
cp designs/goa-sample/house.json designs/my-design/house.json
# edit designs/my-design/house.json by hand, then:

goa-house validate --house designs/my-design/house.json
goa-house build-tour --house designs/my-design/house.json \
    --panos-dir designs/my-design/panos \
    --massing-dir designs/my-design/massing
```

`build-tour` writes placeholder panos + per-room and per-floor top-down PNGs. Refresh the viewer and the new design appears in the dropdown. Replace the placeholder panos with real ones via `render-all` when ready.

For a wholly fresh start: `goa-house init --plot fixtures/plot.json --out designs/my-design/house.json` initialises an empty house from the plot seed.

## Layout

```
src/goa_house/
  state.py                # Pydantic models + semantic validators (source of truth)
  cli.py                  # `goa-house` subcommands
  api.py                  # FastAPI app: /designs, /designs/{name}/{house,tour}.json, static panos+massing
  agents/
    extractor.py          # Requirement Extractor prompt (LLM wiring pending)
    prompt_builder.py     # deterministic [STYLE]/[ROOM FACTS]/... prompt for gpt-image-2
  render/
    placeholder.py        # procedural 2048×1024 equirectangular JPEGs per room
    panorama.py           # gpt-image-2 image-conditioned renderer
    massing.py            # matplotlib top-down PNGs (per floor / per room)
  tour/pannellum.py       # tour.json builder + opening/yaw geometry
designs/                  # one folder per design (committed)
  <name>/house.json + panos/ + massing/
fixtures/plot.json        # empty-plot seed for `goa-house init`
state/                    # runtime scratch (logs, future working copy) — gitignored
web/                      # static viewer (index.html + app.js)
tests/                    # pytest
```

## Tests

```bash
pytest
```

## License

Not specified.

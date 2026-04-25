# Designs

Each subdirectory here is one self-contained house design — a JSON layout plus the rendered artifacts that go with it. The viewer's design dropdown lists every subdirectory that contains a `house.json`.

## Layout

```
designs/
  <design-name>/
    house.json                     # the source of truth (Plot + Rooms + Style)
    panos/
      <room_id>.jpg                # equirectangular panorama per room (2:1, ~2048×1024)
    massing/
      topdown.png                  # whole-plot top-down plan
      <room_id>/topdown.png        # same plan with that room highlighted
```

The viewer fetches `/designs/<name>/house.json`, `/designs/<name>/tour.json` (built on the fly from `house.json`), and serves the `panos/` and `massing/` files statically.

## Adding a new design

```bash
# 1. seed the folder with a hand-authored house.json (or copy from an existing design)
mkdir designs/my-design
cp designs/goa-sample/house.json designs/my-design/house.json
# edit designs/my-design/house.json

# 2. validate the layout (fails on overlap, setback violations, broken door graph, ...)
python -m goa_house.cli validate --house designs/my-design/house.json

# 3. render placeholder panos + top-down PNGs
python -m goa_house.cli build-tour \
    --house designs/my-design/house.json \
    --panos-dir designs/my-design/panos \
    --massing-dir designs/my-design/massing
```

Restart the server (or refresh the page if it was loaded after the new directory was created) and the new design appears in the dropdown.

## Naming

Use lowercase-kebab-case (`goa-sample`, `goa-two-floor`). The name is used as a URL segment and shown verbatim in the dropdown.

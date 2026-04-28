from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image

from goa_house.cli import main as cli_main
from goa_house.render.massing import render_topdown
from goa_house.render.placeholder import render_all_placeholders
from goa_house.state import load_house

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_HOUSE = REPO_ROOT / "designs" / "goa-sample" / "house.json"
TWO_FLOOR_HOUSE = REPO_ROOT / "designs" / "goa-two-floor" / "house.json"


def test_render_all_placeholders(tmp_path: Path):
    house = load_house(SAMPLE_HOUSE)
    panos_dir = tmp_path / "panos"
    paths = render_all_placeholders(house, panos_dir)
    assert {p.stem for p in paths} == {r.id for r in house.rooms}
    for p in paths:
        with Image.open(p) as img:
            w, h = img.size
            assert w == 2 * h


def test_render_topdown(tmp_path: Path):
    house = load_house(SAMPLE_HOUSE)
    out = render_topdown(house, tmp_path / "topdown.png")
    assert out.exists()
    assert out.stat().st_size > 2000


def test_cli_build_tour_end_to_end(tmp_path: Path):
    design_dir = tmp_path / "designs" / "test-design"
    design_dir.mkdir(parents=True)
    house_path = design_dir / "house.json"
    panos_dir = design_dir / "panos"
    massing_dir = design_dir / "massing"
    shutil.copyfile(SAMPLE_HOUSE, house_path)

    rc = cli_main([
        "build-tour",
        "--house", str(house_path),
        "--panos-dir", str(panos_dir),
        "--massing-dir", str(massing_dir),
    ])
    assert rc == 0
    assert (massing_dir / "topdown.png").exists()
    for room_id in ("living_room", "kitchen", "master_bedroom"):
        assert (panos_dir / f"{room_id}.jpg").exists()
        assert (massing_dir / room_id / "topdown.png").exists()


def test_cli_validate_ok(tmp_path: Path):
    rc = cli_main(["validate", "--house", str(SAMPLE_HOUSE)])
    assert rc == 0


def test_two_floor_design_validates_and_emits_per_floor_topdowns(tmp_path: Path):
    design_dir = tmp_path / "designs" / "two-floor"
    design_dir.mkdir(parents=True)
    house_path = design_dir / "house.json"
    panos_dir = design_dir / "panos"
    massing_dir = design_dir / "massing"
    shutil.copyfile(TWO_FLOOR_HOUSE, house_path)

    rc = cli_main([
        "build-tour",
        "--house", str(house_path),
        "--panos-dir", str(panos_dir),
        "--massing-dir", str(massing_dir),
    ])
    assert rc == 0
    assert (massing_dir / "topdown.png").exists()
    assert (massing_dir / "topdown-floor0.png").exists()
    assert (massing_dir / "topdown-floor1.png").exists()
    for room_id in ("living_room", "stairwell_g", "master_bedroom", "landing"):
        assert (massing_dir / room_id / "topdown.png").exists()
        assert (panos_dir / f"{room_id}.jpg").exists()


def test_cli_validate_two_floor_design():
    rc = cli_main(["validate", "--house", str(TWO_FLOOR_HOUSE)])
    assert rc == 0


def test_cli_build_site_produces_static_bundle(tmp_path: Path):
    designs_dir = tmp_path / "designs"
    designs_dir.mkdir()
    sample_dst = designs_dir / "goa-sample"
    sample_dst.mkdir()
    shutil.copyfile(SAMPLE_HOUSE, sample_dst / "house.json")
    panos_src = SAMPLE_HOUSE.parent / "panos"
    massing_src = SAMPLE_HOUSE.parent / "massing"
    if panos_src.exists():
        shutil.copytree(panos_src, sample_dst / "panos")
    if massing_src.exists():
        shutil.copytree(massing_src, sample_dst / "massing")

    out_dir = tmp_path / "_site"
    web_dir = REPO_ROOT / "web"

    rc = cli_main([
        "build-site",
        "--out-dir", str(out_dir),
        "--designs-dir", str(designs_dir),
        "--web-dir", str(web_dir),
    ])
    assert rc == 0
    assert (out_dir / "index.html").exists()
    assert (out_dir / "static" / "app.js").exists()
    manifest = json.loads((out_dir / "designs.json").read_text(encoding="utf-8"))
    assert manifest == {"designs": ["goa-sample"]}

    tour_path = out_dir / "designs" / "goa-sample" / "tour.json"
    tour = json.loads(tour_path.read_text(encoding="utf-8"))
    assert tour["default"]["firstScene"] == "living_room"
    for scene in tour["scenes"].values():
        assert scene["panorama"].startswith("designs/goa-sample/panos/")

    # Pano JPGs are bundled; debug sidecars (.hash, .massing.png) are not.
    panos_out = out_dir / "designs" / "goa-sample" / "panos"
    assert any(p.suffix == ".jpg" for p in panos_out.iterdir())
    assert not any(p.suffix == ".hash" for p in panos_out.iterdir())
    assert not any(p.name.endswith(".massing.png") for p in panos_out.iterdir())

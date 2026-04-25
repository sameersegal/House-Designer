from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

from goa_house.cli import main as cli_main
from goa_house.render.massing import render_topdown
from goa_house.render.placeholder import render_all_placeholders
from goa_house.state import load_house

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_HOUSE = REPO_ROOT / "designs" / "goa-sample" / "house.json"


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

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from goa_house.cli import main as cli_main
from goa_house.render.massing import render_topdown
from goa_house.render.placeholder import render_all_placeholders
from goa_house.state import load_house
from goa_house.tour.pannellum import build_tour

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FIXTURE = REPO_ROOT / "fixtures" / "house.sample.json"


def test_render_all_placeholders(tmp_path: Path):
    house = load_house(SAMPLE_FIXTURE)
    panos_dir = tmp_path / "panos"
    paths = render_all_placeholders(house, panos_dir)
    assert {p.stem for p in paths} == {r.id for r in house.rooms}
    for p in paths:
        with Image.open(p) as img:
            w, h = img.size
            assert w == 2 * h


def test_render_topdown(tmp_path: Path):
    house = load_house(SAMPLE_FIXTURE)
    out = render_topdown(house, tmp_path / "topdown.png")
    assert out.exists()
    assert out.stat().st_size > 2000


def test_cli_build_sample_end_to_end(tmp_path: Path, monkeypatch):
    state_dir = tmp_path / "state"
    panos_dir = state_dir / "panos"
    massing_dir = state_dir / "massing"
    house_out = state_dir / "house.json"
    web_dir = REPO_ROOT / "web"
    web_dir.mkdir(exist_ok=True)

    rc = cli_main([
        "build-sample",
        "--fixture", str(SAMPLE_FIXTURE),
        "--house-out", str(house_out),
        "--panos-dir", str(panos_dir),
        "--massing-dir", str(massing_dir),
    ])
    assert rc == 0
    assert house_out.exists()
    assert (massing_dir / "topdown.png").exists()
    for room_id in ("living_room", "kitchen", "master_bedroom"):
        assert (panos_dir / f"{room_id}.jpg").exists()
        assert (massing_dir / room_id / "topdown.png").exists()
    tour_path = web_dir / "tour.json"
    tour = json.loads(tour_path.read_text())
    assert tour["default"]["firstScene"] == "living_room"


def test_cli_validate_ok(tmp_path: Path):
    rc = cli_main(["validate", "--house", str(SAMPLE_FIXTURE)])
    assert rc == 0

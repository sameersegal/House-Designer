from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goa_house.api import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_HOUSE = REPO_ROOT / "designs" / "goa-sample" / "house.json"
SAMPLE_PANOS = REPO_ROOT / "designs" / "goa-sample" / "panos"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    designs_dir = tmp_path / "designs"
    sample = designs_dir / "goa-sample"
    sample.mkdir(parents=True)
    shutil.copyfile(SAMPLE_HOUSE, sample / "house.json")
    panos_dir = sample / "panos"
    panos_dir.mkdir()
    for jpg in SAMPLE_PANOS.glob("*.jpg"):
        shutil.copyfile(jpg, panos_dir / jpg.name)
    massing_dir = sample / "massing" / "living_room"
    massing_dir.mkdir(parents=True)
    (massing_dir / "topdown.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal stub
    web_dir = REPO_ROOT / "web"
    return TestClient(create_app(web_dir=web_dir, designs_dir=designs_dir))


def test_list_designs(client: TestClient):
    r = client.get("/designs.json")
    assert r.status_code == 200
    assert r.json() == {"designs": ["goa-sample"]}


def test_house_json(client: TestClient):
    r = client.get("/designs/goa-sample/house.json")
    assert r.status_code == 200
    body = r.json()
    assert body["plot"]["setbacks"]["front"] == 3.0
    assert {room["id"] for room in body["rooms"]} == {"living_room", "kitchen", "master_bedroom"}


def test_tour_json_uses_design_scoped_pano_urls(client: TestClient):
    r = client.get("/designs/goa-sample/tour.json")
    assert r.status_code == 200
    tour = r.json()
    assert tour["default"]["firstScene"] == "living_room"
    for scene_id, scene in tour["scenes"].items():
        assert scene["panorama"].startswith(f"designs/goa-sample/panos/{scene_id}.jpg")


def test_static_pano_served(client: TestClient):
    r = client.get("/designs/goa-sample/panos/living_room.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 1000


def test_static_massing_served(client: TestClient):
    r = client.get("/designs/goa-sample/massing/living_room/topdown.png")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


def test_missing_design_returns_404(client: TestClient):
    r = client.get("/designs/no-such-design/house.json")
    assert r.status_code == 404


def test_path_traversal_rejected(client: TestClient):
    r = client.get("/designs/..%2Fpwn/house.json")
    assert r.status_code in (400, 404)
    r = client.get("/designs/goa-sample/panos/..%2F..%2Fhouse.json")
    assert r.status_code in (400, 404)


def test_index_served(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "Goa House Designer" in r.text
    assert "design-select" in r.text


def test_empty_designs_dir(tmp_path: Path):
    web_dir = REPO_ROOT / "web"
    designs_dir = tmp_path / "designs"
    designs_dir.mkdir()
    client = TestClient(create_app(web_dir=web_dir, designs_dir=designs_dir))
    assert client.get("/designs.json").json() == {"designs": []}


def test_real_designs_dir_lists_committed_designs():
    designs_dir = REPO_ROOT / "designs"
    web_dir = REPO_ROOT / "web"
    client = TestClient(create_app(web_dir=web_dir, designs_dir=designs_dir))
    listed = client.get("/designs.json").json()["designs"]
    assert "goa-sample" in listed
    assert "goa-two-floor" in listed

    tour = client.get("/designs/goa-two-floor/tour.json").json()
    assert "stairwell_g" in tour["scenes"]
    assert "landing" in tour["scenes"]
    stairs_hotspot = next(
        hs
        for hs in tour["scenes"]["stairwell_g"]["hotSpots"]
        if "cssClass" in hs and "goa-stairs" in hs["cssClass"]
    )
    assert stairs_hotspot["sceneId"] == "landing"

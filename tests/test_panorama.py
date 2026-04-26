from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from goa_house.render.massing import render_first_person_block
from goa_house.render.panorama import (
    CostCapExceeded,
    OpenAIImagePanoramaClient,
    RenderSession,
    compute_input_hash,
    render_room_panorama,
    validate_panorama,
)
from goa_house.state import load_house

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FIXTURE = REPO_ROOT / "fixtures" / "house.sample.json"


class FakeClient:
    def __init__(self, image_bytes_factory):
        self.calls = 0
        self._factory = image_bytes_factory

    def generate(self, prompt: str, ref_bytes: bytes) -> bytes:
        self.calls += 1
        return self._factory(prompt, ref_bytes)


def _good_pano_bytes() -> bytes:
    img = Image.new("RGB", (4096, 2048), (180, 200, 220))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _bad_seam_pano_bytes() -> bytes:
    img = Image.new("RGB", (4096, 2048), (180, 200, 220))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 1, 2048], fill=(0, 0, 0))
    draw.rectangle([4095, 0, 4096, 2048], fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def test_compute_input_hash_changes_with_inputs():
    h1 = compute_input_hash("p1", b"ref")
    h2 = compute_input_hash("p2", b"ref")
    h3 = compute_input_hash("p1", b"ref2")
    h4 = compute_input_hash("p1", b"ref", style_version=99)
    assert len({h1, h2, h3, h4}) == 4


def test_compute_input_hash_stable_for_same_inputs():
    assert compute_input_hash("p", b"ref") == compute_input_hash("p", b"ref")


def test_render_session_caps():
    s = RenderSession(cap=2, warn_threshold=10)
    s.attempt()
    s.attempt()
    with pytest.raises(CostCapExceeded):
        s.attempt()


def test_validate_panorama_seam_ok(tmp_path: Path):
    p = tmp_path / "p.jpg"
    p.write_bytes(_good_pano_bytes())
    assert validate_panorama(p) == []


def test_validate_panorama_seam_fail(tmp_path: Path):
    p = tmp_path / "p.jpg"
    p.write_bytes(_bad_seam_pano_bytes())
    issues = validate_panorama(p)
    assert any("seam" in i for i in issues)


def test_validate_panorama_aspect_fail(tmp_path: Path):
    img = Image.new("RGB", (1000, 1000))
    p = tmp_path / "p.jpg"
    img.save(p, "JPEG")
    issues = validate_panorama(p)
    assert any("aspect" in i for i in issues)


def test_render_first_person_block_aspect(tmp_path: Path):
    house = load_house(SAMPLE_FIXTURE)
    out = tmp_path / "fp.png"
    render_first_person_block(house, house.rooms[0], out)
    with Image.open(out) as img:
        w, h = img.size
        assert w == 2 * h
        assert (w, h) == (4096, 2048)


def test_render_room_panorama_writes_versioned_and_latest(tmp_path: Path):
    house = load_house(SAMPLE_FIXTURE)
    massing_path = tmp_path / "massing.png"
    render_first_person_block(house, house.room_by_id("living_room"), massing_path)
    panos_dir = tmp_path / "panos"
    log_dir = tmp_path / "logs"

    client = FakeClient(lambda p, r: _good_pano_bytes())
    result = render_room_panorama(
        house,
        "living_room",
        massing_path,
        panos_dir,
        client=client,
        log_dir=log_dir,
    )
    assert result.skipped is False
    assert result.version == 1
    assert result.issues == []
    assert (panos_dir / "living_room.jpg").exists()
    assert (panos_dir / "living_room.v1.jpg").exists()
    assert (panos_dir / "living_room.manifest.json").exists()
    assert any(log_dir.iterdir())


def test_render_room_panorama_idempotent(tmp_path: Path):
    house = load_house(SAMPLE_FIXTURE)
    massing_path = tmp_path / "massing.png"
    render_first_person_block(house, house.room_by_id("living_room"), massing_path)
    panos_dir = tmp_path / "panos"
    client = FakeClient(lambda p, r: _good_pano_bytes())

    r1 = render_room_panorama(house, "living_room", massing_path, panos_dir, client=client)
    r2 = render_room_panorama(house, "living_room", massing_path, panos_dir, client=client)

    assert client.calls == 1
    assert r1.skipped is False
    assert r2.skipped is True
    assert r1.input_hash == r2.input_hash
    assert r1.version == r2.version == 1


def test_render_room_panorama_changed_input_makes_new_version(tmp_path: Path):
    house = load_house(SAMPLE_FIXTURE)
    massing_path = tmp_path / "massing.png"
    render_first_person_block(house, house.room_by_id("living_room"), massing_path)
    panos_dir = tmp_path / "panos"
    client = FakeClient(lambda p, r: _good_pano_bytes())

    r1 = render_room_panorama(house, "living_room", massing_path, panos_dir, client=client)
    # Change the massing image bytes -> different input hash.
    massing_path.write_bytes(massing_path.read_bytes() + b"\x00\x01\x02")
    r2 = render_room_panorama(house, "living_room", massing_path, panos_dir, client=client)

    assert client.calls == 2
    assert r1.version == 1
    assert r2.version == 2
    assert (panos_dir / "living_room.v2.jpg").exists()


def test_render_room_panorama_post_validation_failure_keeps_versioned(tmp_path: Path):
    house = load_house(SAMPLE_FIXTURE)
    massing_path = tmp_path / "massing.png"
    render_first_person_block(house, house.room_by_id("living_room"), massing_path)
    panos_dir = tmp_path / "panos"
    client = FakeClient(lambda p, r: _bad_seam_pano_bytes())

    result = render_room_panorama(
        house,
        "living_room",
        massing_path,
        panos_dir,
        client=client,
        seam_threshold=10,
    )
    assert result.issues
    assert any("seam" in i for i in result.issues)
    assert result.versioned_path is not None and result.versioned_path.exists()
    # latest pointer + manifest should NOT have been promoted
    assert not (panos_dir / "living_room.jpg").exists()
    assert not (panos_dir / "living_room.manifest.json").exists()


def test_render_room_panorama_session_cap(tmp_path: Path):
    house = load_house(SAMPLE_FIXTURE)
    massing_path = tmp_path / "massing.png"
    render_first_person_block(house, house.room_by_id("living_room"), massing_path)
    panos_dir = tmp_path / "panos"
    client = FakeClient(lambda p, r: _good_pano_bytes())
    session = RenderSession(cap=0, warn_threshold=99)
    with pytest.raises(CostCapExceeded):
        render_room_panorama(
            house,
            "living_room",
            massing_path,
            panos_dir,
            client=client,
            session=session,
        )


def test_openai_client_no_real_call_without_credentials():
    # Sanity check: just ensure construction doesn't hit the network.
    c = OpenAIImagePanoramaClient()
    assert c.model == "gpt-image-2"
    assert c.size == "4096x2048"

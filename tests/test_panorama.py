from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from goa_house.render.panorama import ImageGenError, render_panorama
from goa_house.state import (
    Camera,
    House,
    Opening,
    Plot,
    Room,
    Setbacks,
)

PLOT = Plot(
    boundary=[(0, 0), (20, 0), (20, 15), (0, 15)],
    north_deg=0.0,
    setbacks=Setbacks(front=3.0, rear=3.0, side=2.0),
)


def _house() -> House:
    room = Room(
        id="living_room",
        name="Living Room",
        polygon=[(2, 3), (10, 3), (10, 9), (2, 9)],
        ceiling_height_m=3.2,
        openings=[
            Opening(type="window", wall="S", position_m=3.0, width_m=1.8, height_m=1.4),
            Opening(type="door", wall="E", position_m=1.5, width_m=0.9, to_room="kitchen"),
        ],
        camera=Camera(x=6.0, y=6.0, z=1.6, yaw_deg=0.0),
    )
    return House(plot=PLOT, rooms=[room])


def _stub_b64(size=(256, 128), color=(80, 60, 40)) -> str:
    img = Image.new("RGB", size, color)
    buf = BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _StubImages:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def edit(self, **kwargs):
        self.calls.append({k: v for k, v in kwargs.items() if k != "image"})
        kwargs["image"].read()  # exhaust the file like the real SDK would
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=_stub_b64())],
            usage=SimpleNamespace(
                model_dump=lambda: {"input_tokens": 100, "output_tokens": 200},
            ),
        )


class _StubClient:
    def __init__(self) -> None:
        self.images = _StubImages()


def test_render_panorama_writes_jpeg_and_logs(tmp_path: Path):
    house = _house()
    client = _StubClient()
    out = tmp_path / "panos" / "living_room.jpg"
    log_dir = tmp_path / "logs"

    result = render_panorama(
        house,
        house.rooms[0],
        out,
        client=client,
        size="2048x1024",
        quality="low",
        log_dir=log_dir,
    )

    assert result == out
    assert out.exists()
    with Image.open(out) as img:
        assert img.format == "JPEG"
    assert len(client.images.calls) == 1
    call = client.images.calls[0]
    assert call["model"] == "gpt-image-2"
    assert call["quality"] == "low"
    assert call["size"] == "2048x1024"
    assert "input_fidelity" not in call  # gpt-image-2 rejects this; only forwarded when explicitly set
    assert "[OUTPUT SPEC]" in call["prompt"]

    log_lines = (log_dir / "image_calls.jsonl").read_text().splitlines()
    assert len(log_lines) == 1
    record = json.loads(log_lines[0])
    assert record["ok"] is True
    assert record["room_id"] == "living_room"
    assert record["usage"] == {"input_tokens": 100, "output_tokens": 200}


def test_render_panorama_skips_when_cached(tmp_path: Path):
    house = _house()
    client = _StubClient()
    out = tmp_path / "panos" / "living_room.jpg"

    render_panorama(house, house.rooms[0], out, client=client, size="2048x1024")
    render_panorama(house, house.rooms[0], out, client=client, size="2048x1024")

    assert len(client.images.calls) == 1


def test_render_panorama_force_bypasses_cache(tmp_path: Path):
    house = _house()
    client = _StubClient()
    out = tmp_path / "panos" / "living_room.jpg"

    render_panorama(house, house.rooms[0], out, client=client, size="2048x1024")
    render_panorama(
        house, house.rooms[0], out, client=client, size="2048x1024", force=True
    )

    assert len(client.images.calls) == 2


def test_render_panorama_invalidates_cache_when_quality_changes(tmp_path: Path):
    house = _house()
    client = _StubClient()
    out = tmp_path / "panos" / "living_room.jpg"

    render_panorama(house, house.rooms[0], out, client=client, quality="low")
    render_panorama(house, house.rooms[0], out, client=client, quality="medium")

    assert len(client.images.calls) == 2


def test_render_panorama_wraps_sdk_errors(tmp_path: Path):
    house = _house()

    class _Boom:
        class images:
            @staticmethod
            def edit(**_):
                raise RuntimeError("rate limited")

    log_dir = tmp_path / "logs"
    out = tmp_path / "panos" / "living_room.jpg"
    with pytest.raises(ImageGenError):
        render_panorama(house, house.rooms[0], out, client=_Boom(), log_dir=log_dir)
    log = (log_dir / "image_calls.jsonl").read_text().splitlines()
    assert len(log) == 1
    assert json.loads(log[0])["ok"] is False

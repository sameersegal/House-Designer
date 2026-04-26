from __future__ import annotations

from pathlib import Path

import pytest

from goa_house.agents.prompt_builder import (
    build_room_prompt,
    wall_compass_direction,
    yaw_compass_direction,
)
from goa_house.state import (
    Camera,
    House,
    Opening,
    Plot,
    Requirement,
    Room,
    Setbacks,
    load_house,
    utcnow_iso,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FIXTURE = REPO_ROOT / "fixtures" / "house.sample.json"


def test_wall_compass_direction_no_rotation():
    assert wall_compass_direction("N", 0.0) == "N"
    assert wall_compass_direction("E", 0.0) == "E"
    assert wall_compass_direction("S", 0.0) == "S"
    assert wall_compass_direction("W", 0.0) == "W"


def test_wall_compass_direction_with_north_offset():
    # plot rotated so +y points 45 deg east of true north -> wall N points NE
    assert wall_compass_direction("N", 45.0) == "NE"
    assert wall_compass_direction("E", 45.0) == "SE"
    assert wall_compass_direction("S", 45.0) == "SW"
    assert wall_compass_direction("W", 45.0) == "NW"


def test_yaw_compass_direction():
    assert yaw_compass_direction(0.0, 0.0) == "N"
    assert yaw_compass_direction(90.0, 0.0) == "E"
    assert yaw_compass_direction(180.0, 0.0) == "S"
    assert yaw_compass_direction(-90.0, 0.0) == "W"


def test_build_prompt_has_required_sections():
    house = load_house(SAMPLE_FIXTURE)
    prompt = build_room_prompt(house, "living_room")
    for token in ("[STYLE]", "[ROOM FACTS]", "[REQUIREMENTS]", "[CAMERA]", "[OUTPUT SPEC]"):
        assert token in prompt
    assert "Indo-Portuguese" in prompt
    assert "lime plaster" in prompt
    assert "living_room" in prompt
    assert "kitchen" in prompt  # via door target
    assert "4096x2048" in prompt


def test_build_prompt_includes_relevant_requirements_only():
    house = load_house(SAMPLE_FIXTURE)
    reqs = [
        Requirement(
            id="req_0001",
            ts=utcnow_iso(),
            scope="living_room",
            type="material",
            statement="Living room walls finished in lime wash",
            status="approved",
        ),
        Requirement(
            id="req_0002",
            ts=utcnow_iso(),
            scope="kitchen",
            type="feature",
            statement="Kitchen has azulejo backsplash",
            status="approved",
        ),
        Requirement(
            id="req_0003",
            ts=utcnow_iso(),
            scope="living_room",
            type="material",
            statement="rejected requirement should be skipped",
            status="rejected",
        ),
        Requirement(
            id="req_0004",
            ts=utcnow_iso(),
            scope="global",
            type="constraint",
            statement="Windows must allow cross ventilation",
            status="approved",
        ),
    ]
    prompt = build_room_prompt(house, "living_room", reqs)
    assert "req_0001" in prompt
    assert "req_0004" in prompt
    assert "req_0002" not in prompt
    assert "req_0003" not in prompt


def test_build_prompt_no_requirements_says_none():
    house = load_house(SAMPLE_FIXTURE)
    prompt = build_room_prompt(house, "living_room")
    assert "[REQUIREMENTS]\n(none)" in prompt


def test_build_prompt_unknown_room_raises():
    house = load_house(SAMPLE_FIXTURE)
    with pytest.raises(ValueError):
        build_room_prompt(house, "ghost_room")


def test_build_prompt_deterministic():
    house = load_house(SAMPLE_FIXTURE)
    p1 = build_room_prompt(house, "kitchen")
    p2 = build_room_prompt(house, "kitchen")
    assert p1 == p2


def test_build_prompt_camera_position_room_local():
    plot = Plot(
        boundary=[(0, 0), (20, 0), (20, 15), (0, 15)],
        north_deg=0.0,
        setbacks=Setbacks(front=3.0, rear=3.0, side=2.0),
    )
    room = Room(
        id="kitchen",
        name="Kitchen",
        polygon=[(10, 5), (15, 5), (15, 10), (10, 10)],
        camera=Camera(x=12.0, y=7.0, z=1.6, yaw_deg=90.0),
        openings=[
            Opening(type="window", wall="N", position_m=1.0, width_m=1.5, height_m=1.4),
        ],
    )
    house = House(plot=plot, rooms=[room])
    prompt = build_room_prompt(house, "kitchen")
    # camera at (12,7) within room (10..15, 5..10) -> room-local (2, 2)
    assert "x=2.00 m, y=2.00 m" in prompt
    assert "initial_facing: E" in prompt
    assert "window on N wall" in prompt


def test_build_prompt_with_north_offset_resolves_compass():
    plot = Plot(
        boundary=[(0, 0), (10, 0), (10, 10), (0, 10)],
        north_deg=90.0,  # plot's +y axis points east
        setbacks=Setbacks(front=2.0, rear=2.0, side=2.0),
    )
    room = Room(
        id="bedroom",
        name="Bedroom",
        polygon=[(3, 3), (7, 3), (7, 7), (3, 7)],
        camera=Camera(x=5, y=5, z=1.6, yaw_deg=0.0),
        openings=[Opening(type="window", wall="N", position_m=1.0, width_m=1.5)],
    )
    house = House(plot=plot, rooms=[room])
    prompt = build_room_prompt(house, "bedroom")
    # wall N with north_deg=90 points east
    assert "window on E wall" in prompt

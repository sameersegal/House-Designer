from __future__ import annotations

from pathlib import Path

import pytest

from goa_house.state import (
    Camera,
    House,
    Opening,
    Plot,
    Room,
    Setbacks,
    load_house,
)
from goa_house.tour.pannellum import (
    build_tour,
    hotspot_angles,
    opening_center,
    wrap_180,
)

PLOT = Plot(
    boundary=[(0, 0), (20, 0), (20, 15), (0, 15)],
    north_deg=0.0,
    setbacks=Setbacks(front=3.0, rear=3.0, side=2.0),
)


def _two_room_house() -> House:
    living = Room(
        id="living_room",
        name="Living Room",
        polygon=[(2, 3), (10, 3), (10, 9), (2, 9)],
        camera=Camera(x=6.0, y=6.0, z=1.6, yaw_deg=0.0),
        openings=[
            Opening(type="door", wall="E", position_m=2.55, width_m=0.9, to_room="kitchen"),
        ],
    )
    kitchen = Room(
        id="kitchen",
        name="Kitchen",
        polygon=[(10, 3), (16, 3), (16, 7), (10, 7)],
        camera=Camera(x=13.0, y=5.0, z=1.6, yaw_deg=0.0),
        openings=[
            Opening(type="door", wall="W", position_m=1.55, width_m=0.9, to_room="living_room"),
        ],
    )
    return House(plot=PLOT, rooms=[living, kitchen])


def test_wrap_180_edges():
    assert wrap_180(0) == 0
    assert wrap_180(180) == -180
    assert wrap_180(-180) == -180
    assert wrap_180(270) == -90
    assert wrap_180(-270) == 90


def test_opening_center_each_wall():
    room = Room(
        id="r",
        name="R",
        polygon=[(0, 0), (4, 0), (4, 3), (0, 3)],
        camera=Camera(x=2, y=1.5),
        openings=[],
    )
    cases = {
        "N": (Opening(type="window", wall="N", position_m=1.0, width_m=1.0), (1.5, 3.0)),
        "S": (Opening(type="window", wall="S", position_m=1.0, width_m=1.0), (1.5, 0.0)),
        "E": (Opening(type="window", wall="E", position_m=0.5, width_m=1.0), (4.0, 1.0)),
        "W": (Opening(type="window", wall="W", position_m=0.5, width_m=1.0), (0.0, 1.0)),
    }
    for _wall, (opening, expected) in cases.items():
        assert opening_center(room, opening) == expected


def test_door_on_east_wall_yaw_is_ninety_when_camera_faces_north():
    room = Room(
        id="r",
        name="R",
        polygon=[(0, 0), (4, 0), (4, 4), (0, 4)],
        camera=Camera(x=2, y=2, z=1.6, yaw_deg=0),
        openings=[],
    )
    door = Opening(type="door", wall="E", position_m=1.5, width_m=1.0, to_room="x")
    yaw, pitch = hotspot_angles(room, door)
    assert yaw == pytest.approx(90.0)
    assert pitch < 0


def test_door_yaw_respects_camera_yaw():
    room = Room(
        id="r",
        name="R",
        polygon=[(0, 0), (4, 0), (4, 4), (0, 4)],
        camera=Camera(x=2, y=2, z=1.6, yaw_deg=90.0),
        openings=[],
    )
    door = Opening(type="door", wall="E", position_m=1.5, width_m=1.0, to_room="x")
    yaw, _ = hotspot_angles(room, door)
    assert yaw == pytest.approx(0.0)


def test_build_tour_scene_per_room_and_door_hotspot():
    house = _two_room_house()
    tour = build_tour(house)

    assert tour["default"]["firstScene"] == "living_room"
    assert set(tour["scenes"].keys()) == {"living_room", "kitchen"}

    living = tour["scenes"]["living_room"]
    assert living["type"] == "equirectangular"
    assert living["panorama"] == "/panos/living_room.jpg"
    assert len(living["hotSpots"]) == 1
    hs = living["hotSpots"][0]
    assert hs["sceneId"] == "kitchen"
    assert hs["type"] == "scene"
    assert hs["yaw"] == pytest.approx(90.0, abs=0.1)


def test_build_tour_skips_non_door_and_dangling_doors():
    room = Room(
        id="r",
        name="R",
        polygon=[(2, 3), (8, 3), (8, 9), (2, 9)],
        camera=Camera(x=5, y=6),
        openings=[
            Opening(type="window", wall="N", position_m=1.0, width_m=1.0),
            Opening(type="door", wall="E", position_m=1.0, width_m=0.9, to_room="ghost"),
        ],
    )
    house = House(plot=PLOT, rooms=[room])
    tour = build_tour(house)
    assert tour["scenes"]["r"]["hotSpots"] == []


def test_build_tour_custom_pano_url():
    house = _two_room_house()
    tour = build_tour(house, panorama_url=lambda rid: f"/custom/{rid}.png")
    assert tour["scenes"]["living_room"]["panorama"] == "/custom/living_room.png"


def test_sample_fixture_builds_clean_tour():
    house_path = (
        Path(__file__).resolve().parent.parent / "designs" / "goa-sample" / "house.json"
    )
    house = load_house(house_path)
    tour = build_tour(house)
    assert set(tour["scenes"].keys()) == {"living_room", "kitchen", "master_bedroom"}
    for scene in tour["scenes"].values():
        for hs in scene["hotSpots"]:
            assert hs["sceneId"] in tour["scenes"]
            assert -180 < hs["yaw"] <= 180


def test_stairs_hotspot_marks_direction_and_css():
    g_stair = Room(
        id="stairwell_g",
        name="Stairwell (Ground)",
        polygon=[(2, 9), (8, 9), (8, 12), (2, 12)],
        floor=0,
        camera=Camera(x=5, y=10.5),
        openings=[Opening(type="stairs", wall="N", position_m=2.0, width_m=1.5, to_room="landing")],
    )
    u_landing = Room(
        id="landing",
        name="Landing",
        polygon=[(2, 9), (8, 9), (8, 12), (2, 12)],
        floor=1,
        camera=Camera(x=5, y=10.5),
        openings=[Opening(type="stairs", wall="N", position_m=2.0, width_m=1.5, to_room="stairwell_g")],
    )
    house = House(plot=PLOT, rooms=[g_stair, u_landing])
    tour = build_tour(house)

    going_up = tour["scenes"]["stairwell_g"]["hotSpots"][0]
    assert going_up["sceneId"] == "landing"
    assert going_up["text"] == "Go up to Landing"
    assert "goa-stairs-up" in going_up["cssClass"]

    going_down = tour["scenes"]["landing"]["hotSpots"][0]
    assert going_down["sceneId"] == "stairwell_g"
    assert going_down["text"] == "Go down to Stairwell (Ground)"
    assert "goa-stairs-down" in going_down["cssClass"]


def test_stairs_up_aims_higher_than_stairs_down():
    g_stair = Room(
        id="stairwell_g",
        name="Stairwell (Ground)",
        polygon=[(2, 9), (8, 9), (8, 12), (2, 12)],
        floor=0,
        camera=Camera(x=5, y=10.5),
        openings=[Opening(type="stairs", wall="N", position_m=2.0, width_m=1.5, to_room="landing")],
    )
    u_landing = Room(
        id="landing",
        name="Landing",
        polygon=[(2, 9), (8, 9), (8, 12), (2, 12)],
        floor=1,
        camera=Camera(x=5, y=10.5),
        openings=[Opening(type="stairs", wall="N", position_m=2.0, width_m=1.5, to_room="stairwell_g")],
    )
    house = House(plot=PLOT, rooms=[g_stair, u_landing])
    tour = build_tour(house)
    up_pitch = tour["scenes"]["stairwell_g"]["hotSpots"][0]["pitch"]
    down_pitch = tour["scenes"]["landing"]["hotSpots"][0]["pitch"]
    # Both pitches negative (target below eye), but DOWN dives much further.
    assert up_pitch > down_pitch
    assert down_pitch < -30


def test_two_floor_design_builds_clean_tour():
    house_path = (
        Path(__file__).resolve().parent.parent / "designs" / "goa-two-floor" / "house.json"
    )
    house = load_house(house_path)
    tour = build_tour(house)
    expected = {
        "living_room",
        "kitchen",
        "dining",
        "stairwell_g",
        "master_bedroom",
        "bedroom_2",
        "bedroom_3",
        "landing",
    }
    assert set(tour["scenes"].keys()) == expected
    stair_targets = {
        hs["sceneId"]
        for scene in tour["scenes"].values()
        for hs in scene["hotSpots"]
        if "cssClass" in hs and "goa-stairs" in hs["cssClass"]
    }
    assert stair_targets == {"stairwell_g", "landing"}

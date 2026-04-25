from __future__ import annotations

from goa_house.agents.prompt_builder import (
    bearing_to_compass,
    build_panorama_prompt,
    wall_to_compass,
)
from goa_house.state import (
    Camera,
    House,
    Opening,
    Plot,
    Requirement,
    Room,
    Setbacks,
)

PLOT = Plot(
    boundary=[(0, 0), (20, 0), (20, 15), (0, 15)],
    north_deg=0.0,
    setbacks=Setbacks(front=3.0, rear=3.0, side=2.0),
)


def _living_room() -> Room:
    return Room(
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


def test_sections_appear_in_order():
    house = House(plot=PLOT, rooms=[_living_room()])
    prompt = build_panorama_prompt(house, house.rooms[0])
    tags = (
        "[STYLE]",
        "[ROOM FACTS]",
        "[ROOM CHARACTER]",
        "[REQUIREMENTS]",
        "[CAMERA]",
        "[OUTPUT SPEC]",
    )
    for tag in tags:
        assert tag in prompt
    indexes = [prompt.index(tag) for tag in tags]
    assert indexes == sorted(indexes)


def test_room_character_section_uses_room_type_furnishings():
    house = House(plot=PLOT, rooms=[_living_room()])
    prompt = build_panorama_prompt(house, house.rooms[0])
    assert "[ROOM CHARACTER]" in prompt
    # living-room cue should mention planter chairs / sala vocabulary
    lower = prompt.lower()
    assert "planter chair" in lower or "settee" in lower or "sala" in lower


def test_room_character_falls_back_for_unknown_room_type():
    odd = Room(
        id="oddball",
        name="Oddball",
        polygon=[(2, 3), (10, 3), (10, 9), (2, 9)],
        ceiling_height_m=3.0,
        openings=[Opening(type="window", wall="S", position_m=3.0, width_m=1.5, height_m=1.4)],
        camera=Camera(x=6.0, y=6.0, z=1.6, yaw_deg=0.0),
    )
    house = House(plot=PLOT, rooms=[odd])
    prompt = build_panorama_prompt(house, odd)
    assert "[ROOM CHARACTER]" in prompt
    assert "Indo-Portuguese" in prompt


def test_stair_direction_note_emitted_for_stairs_opening():
    upstairs = Room(
        id="landing",
        name="Landing",
        polygon=[(2, 9), (10, 9), (10, 12), (2, 12)],
        floor=1,
        ceiling_height_m=3.0,
        openings=[Opening(type="stairs", wall="N", position_m=3.0, width_m=1.5, to_room="stairwell_g")],
        camera=Camera(x=5.0, y=10.5, z=1.6, yaw_deg=0.0),
    )
    downstairs = Room(
        id="stairwell_g",
        name="Stairwell Ground",
        polygon=[(2, 9), (10, 9), (10, 12), (2, 12)],
        floor=0,
        ceiling_height_m=3.2,
        openings=[Opening(type="stairs", wall="N", position_m=3.0, width_m=1.5, to_room="landing")],
        camera=Camera(x=5.0, y=10.5, z=1.6, yaw_deg=0.0),
    )
    house = House(plot=PLOT, rooms=[downstairs, upstairs])
    up_prompt = build_panorama_prompt(house, downstairs)
    down_prompt = build_panorama_prompt(house, upstairs)
    assert "Stairs go UP" in up_prompt
    assert "Stairs go DOWN" in down_prompt


def test_room_facts_describe_stairs_opening_with_direction():
    upstairs = Room(
        id="landing",
        name="Landing",
        polygon=[(2, 9), (10, 9), (10, 12), (2, 12)],
        floor=1,
        ceiling_height_m=3.0,
        openings=[Opening(type="stairs", wall="N", position_m=3.0, width_m=1.5, to_room="stairwell_g")],
        camera=Camera(x=5.0, y=10.5, z=1.6, yaw_deg=0.0),
    )
    downstairs = Room(
        id="stairwell_g",
        name="Stairwell Ground",
        polygon=[(2, 9), (10, 9), (10, 12), (2, 12)],
        floor=0,
        ceiling_height_m=3.2,
        openings=[Opening(type="stairs", wall="N", position_m=3.0, width_m=1.5, to_room="landing")],
        camera=Camera(x=5.0, y=10.5, z=1.6, yaw_deg=0.0),
    )
    house = House(plot=PLOT, rooms=[downstairs, upstairs])
    up_prompt = build_panorama_prompt(house, downstairs)
    down_prompt = build_panorama_prompt(house, upstairs)
    # Facts section must NOT mislabel stairs as a window.
    assert "stairs on N wall" in up_prompt
    assert "stairs on N wall" in down_prompt
    assert "leads up to Landing" in up_prompt
    assert "leads down to Stairwell Ground" in down_prompt


def test_room_facts_describe_openings_with_compass_and_dimensions():
    house = House(plot=PLOT, rooms=[_living_room()])
    prompt = build_panorama_prompt(house, house.rooms[0])
    assert "8.0 m x 6.0 m" in prompt
    assert "ceiling height: 3.2 m".lower() in prompt.lower()
    assert "window on S wall" in prompt
    assert "door on E wall" in prompt
    assert "leads to kitchen" in prompt


def test_north_offset_rotates_wall_compass():
    rotated = Plot(
        boundary=PLOT.boundary,
        north_deg=90.0,
        setbacks=PLOT.setbacks,
    )
    house = House(plot=rotated, rooms=[_living_room()])
    prompt = build_panorama_prompt(house, house.rooms[0])
    assert "window on W wall" in prompt
    assert "door on S wall" in prompt


def test_only_scoped_approved_requirements_included():
    room = _living_room()
    house = House(plot=PLOT, rooms=[room])
    reqs = [
        Requirement(
            id="req_0001",
            ts="2026-04-25T00:00:00+00:00",
            scope=room.id,
            type="material",
            statement="Floor is polished oxide red.",
            status="approved",
        ),
        Requirement(
            id="req_0002",
            ts="2026-04-25T00:00:00+00:00",
            scope="kitchen",
            type="feature",
            statement="Add an island.",
            status="approved",
        ),
        Requirement(
            id="req_0003",
            ts="2026-04-25T00:00:00+00:00",
            scope=room.id,
            type="feature",
            statement="Add a bay window.",
            status="proposed",
        ),
        Requirement(
            id="req_0004",
            ts="2026-04-25T00:00:00+00:00",
            scope="global",
            type="constraint",
            statement="Use lime plaster on every interior wall.",
            status="approved",
        ),
    ]
    prompt = build_panorama_prompt(house, room, reqs)
    assert "polished oxide red" in prompt
    assert "lime plaster" in prompt.lower()
    assert "Add an island" not in prompt
    assert "bay window" not in prompt


def test_output_spec_carries_size_and_seam_constraint():
    house = House(plot=PLOT, rooms=[_living_room()])
    prompt = build_panorama_prompt(house, house.rooms[0], output_size=(2048, 1024))
    assert "2048x1024" in prompt
    assert "2:1" in prompt
    assert "seam" in prompt.lower()


def test_output_spec_forbids_painted_text_on_walls():
    house = House(plot=PLOT, rooms=[_living_room()])
    prompt = build_panorama_prompt(house, house.rooms[0])
    lower = prompt.lower()
    assert "do not paint" in lower
    assert "signage" in lower or "text" in lower


def test_compass_helpers():
    assert bearing_to_compass(0) == "N"
    assert bearing_to_compass(45) == "NE"
    assert bearing_to_compass(359.9) == "N"
    assert wall_to_compass("N", 0.0) == "N"
    assert wall_to_compass("N", 90.0) == "E"
    assert wall_to_compass("E", 90.0) == "S"

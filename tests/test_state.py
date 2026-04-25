from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from goa_house.state import (
    Camera,
    House,
    Opening,
    Plot,
    Requirement,
    Room,
    Setbacks,
    append_requirement,
    load_house,
    load_requirements,
    save_house,
    utcnow_iso,
    validate_house,
)


PLOT = Plot(
    boundary=[(0, 0), (20, 0), (20, 15), (0, 15)],
    north_deg=0.0,
    setbacks=Setbacks(front=3.0, rear=3.0, side=2.0),
)


def _room(
    id: str = "master_bedroom",
    polygon=None,
    openings=None,
    camera=None,
) -> Room:
    return Room(
        id=id,
        name=id.replace("_", " ").title(),
        polygon=polygon or [(4, 4), (9, 4), (9, 9), (4, 9)],
        openings=openings or [],
        camera=camera or Camera(x=6.5, y=6.5, z=1.6, yaw_deg=0.0),
    )


def _empty_house() -> House:
    return House(plot=PLOT, rooms=[])


def test_empty_house_validates_clean():
    assert validate_house(_empty_house()) == []


def test_room_inside_plot_and_setbacks_passes():
    house = House(plot=PLOT, rooms=[_room()])
    assert validate_house(house) == []


def test_room_outside_plot_fails():
    room = _room(polygon=[(21, 0), (25, 0), (25, 5), (21, 5)])
    house = House(plot=PLOT, rooms=[room])
    issues = validate_house(house)
    codes = {i.code for i in issues}
    assert "room_outside_plot" in codes


def test_room_violates_setback_fails():
    room = _room(polygon=[(0, 0), (3, 0), (3, 3), (0, 3)])
    house = House(plot=PLOT, rooms=[room])
    codes = {i.code for i in validate_house(house)}
    assert "room_violates_setback" in codes


def test_overlapping_rooms_fails():
    a = _room(id="master_bedroom", polygon=[(4, 4), (9, 4), (9, 9), (4, 9)])
    b = _room(id="second_bedroom", polygon=[(6, 6), (11, 6), (11, 11), (6, 11)])
    house = House(plot=PLOT, rooms=[a, b])
    codes = {i.code for i in validate_house(house)}
    assert "room_overlap" in codes


def test_opening_off_wall_fails():
    opening = Opening(type="window", wall="N", position_m=4.5, width_m=1.5)
    room = _room(openings=[opening])
    house = House(plot=PLOT, rooms=[room])
    codes = {i.code for i in validate_house(house)}
    assert "opening_off_wall" in codes


def test_opening_on_wall_passes():
    opening = Opening(type="window", wall="N", position_m=1.0, width_m=1.5)
    room = _room(openings=[opening])
    house = House(plot=PLOT, rooms=[room])
    assert validate_house(house) == []


def test_door_requires_to_room():
    with pytest.raises(ValidationError):
        Opening(type="door", wall="E", position_m=1.0, width_m=0.9)


def test_window_rejects_to_room():
    with pytest.raises(ValidationError):
        Opening(type="window", wall="N", position_m=1.0, width_m=1.5, to_room="kitchen")


def test_duplicate_room_ids_rejected():
    a = _room(id="master_bedroom", polygon=[(4, 4), (6, 4), (6, 6), (4, 6)])
    b = _room(id="master_bedroom", polygon=[(7, 4), (9, 4), (9, 6), (7, 6)])
    with pytest.raises(ValidationError):
        House(plot=PLOT, rooms=[a, b])


def test_room_id_must_be_snake_case():
    with pytest.raises(ValidationError):
        _room(id="MasterBedroom")


def test_unreachable_room_fails():
    a = _room(id="master_bedroom", polygon=[(4, 4), (8, 4), (8, 8), (4, 8)])
    b = _room(
        id="second_bedroom",
        polygon=[(10, 4), (14, 4), (14, 8), (10, 8)],
        camera=Camera(x=12, y=6),
    )
    house = House(plot=PLOT, rooms=[a, b])
    codes = {i.code for i in validate_house(house)}
    assert "room_unreachable" in codes


def test_door_connected_rooms_reachable():
    a = _room(
        id="master_bedroom",
        polygon=[(4, 4), (8, 4), (8, 8), (4, 8)],
        openings=[Opening(type="door", wall="E", position_m=1.0, width_m=0.9, to_room="corridor")],
    )
    b = _room(
        id="corridor",
        polygon=[(8, 4), (12, 4), (12, 8), (8, 8)],
        camera=Camera(x=10, y=6),
        openings=[Opening(type="door", wall="W", position_m=1.0, width_m=0.9, to_room="master_bedroom")],
    )
    house = House(plot=PLOT, rooms=[a, b])
    assert validate_house(house) == []


def test_door_target_missing_fails():
    a = _room(
        id="master_bedroom",
        polygon=[(4, 4), (8, 4), (8, 8), (4, 8)],
        openings=[Opening(type="door", wall="E", position_m=1.0, width_m=0.9, to_room="ghost_room")],
    )
    house = House(plot=PLOT, rooms=[a])
    codes = {i.code for i in validate_house(house)}
    assert "door_target_missing" in codes


def test_plot_fixture_loads(tmp_path: Path):
    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "plot.json"
    data = json.loads(fixture.read_text())
    plot = Plot.model_validate(data)
    assert plot.setbacks.front == 3.0
    house = House(plot=plot)
    assert validate_house(house) == []


def test_save_house_writes_snapshots(tmp_path: Path):
    house = _empty_house()
    base = tmp_path / "house.json"
    snap1 = save_house(house, base)
    snap2 = save_house(house, base)
    assert base.exists()
    assert snap1.name == "house.v1.json"
    assert snap2.name == "house.v2.json"
    loaded = load_house(base)
    assert loaded.plot.setbacks.front == 3.0


def test_requirements_log_roundtrip(tmp_path: Path):
    path = tmp_path / "requirements.jsonl"
    r1 = Requirement(
        id="req_0001",
        ts=utcnow_iso(),
        scope="master_bedroom",
        type="orientation",
        statement="Master bedroom faces NE",
        source_prompt="Master bedroom NE for morning light",
        status="approved",
    )
    r2 = Requirement(
        id=Requirement.next_id([r1]),
        ts=utcnow_iso(),
        scope="master_bedroom",
        type="dimension",
        statement="Master bedroom is 4x5m",
        source_prompt="4x5m master bedroom",
        status="approved",
    )
    append_requirement(r1, path)
    append_requirement(r2, path)
    loaded = load_requirements(path)
    assert [r.id for r in loaded] == ["req_0001", "req_0002"]
    assert Requirement.next_id(loaded) == "req_0003"


def test_load_requirements_missing_file(tmp_path: Path):
    assert load_requirements(tmp_path / "nope.jsonl") == []

from __future__ import annotations

import pytest

from goa_house.diffs import (
    AddOpeningMutation,
    AddRoomMutation,
    DiffApplyError,
    ProposedRequirement,
    RemoveOpeningMutation,
    RemoveRoomMutation,
    RequirementDiff,
    UpdateRoomMutation,
    affected_room_ids,
    apply_diffs,
    validate_projection,
)
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


def _living() -> Room:
    return Room(
        id="living_room",
        name="Living Room",
        polygon=[(3, 3), (10, 3), (10, 9), (3, 9)],
        camera=Camera(x=6.5, y=6.0, z=1.6, yaw_deg=0.0),
    )


def _master() -> Room:
    return Room(
        id="master_bedroom",
        name="Master Bedroom",
        polygon=[(10, 3), (15, 3), (15, 8), (10, 8)],
        openings=[
            Opening(type="door", wall="W", position_m=1.0, width_m=0.9, to_room="living_room"),
            Opening(type="window", wall="N", position_m=1.0, width_m=1.5),
        ],
        camera=Camera(x=12.5, y=5.5, z=1.6, yaw_deg=180.0),
    )


def _two_room_house() -> House:
    living = _living()
    living.openings.append(
        Opening(type="door", wall="E", position_m=1.0, width_m=0.9, to_room="master_bedroom")
    )
    return House(plot=PLOT, rooms=[living, _master()])


def _diff(mutation, scope: str = "global", rtype: str = "feature") -> RequirementDiff:
    return RequirementDiff(
        proposed=ProposedRequirement(scope=scope, type=rtype, statement="test"),
        source_span="test",
        mutation=mutation,
    )


def test_apply_add_room_appends_and_validates():
    house = House(plot=PLOT, rooms=[_living()])
    diff = _diff(AddRoomMutation(room=_master()))
    # Living room had no door to master yet; add one as a second mutation
    door_diff = _diff(
        AddOpeningMutation(
            room_id="living_room",
            opening=Opening(
                type="door", wall="E", position_m=1.0, width_m=0.9, to_room="master_bedroom"
            ),
        )
    )
    projected = apply_diffs(house, [diff, door_diff])
    assert {r.id for r in projected.rooms} == {"living_room", "master_bedroom"}
    # original house untouched
    assert {r.id for r in house.rooms} == {"living_room"}


def test_apply_add_room_duplicate_id_raises():
    house = _two_room_house()
    with pytest.raises(DiffApplyError, match="already exists"):
        apply_diffs(house, [_diff(AddRoomMutation(room=_master()))])


def test_apply_update_room_polygon():
    house = _two_room_house()
    diff = _diff(
        UpdateRoomMutation(room_id="master_bedroom", polygon=[(10, 3), (16, 3), (16, 8), (10, 8)])
    )
    projected = apply_diffs(house, [diff])
    master = projected.room_by_id("master_bedroom")
    assert master is not None
    xs = [p[0] for p in master.polygon]
    assert max(xs) == 16


def test_apply_update_unknown_room_raises():
    house = _two_room_house()
    with pytest.raises(DiffApplyError, match="not found"):
        apply_diffs(house, [_diff(UpdateRoomMutation(room_id="ghost", name="Ghost"))])


def test_apply_remove_room():
    house = _two_room_house()
    # also remove the living_room door pointing at master, otherwise validation fails
    projected = apply_diffs(
        house,
        [
            _diff(RemoveOpeningMutation(room_id="living_room", opening_index=0)),
            _diff(RemoveRoomMutation(room_id="master_bedroom")),
        ],
    )
    assert [r.id for r in projected.rooms] == ["living_room"]


def test_apply_remove_opening_out_of_range_raises():
    house = _two_room_house()
    with pytest.raises(DiffApplyError, match="no opening at index"):
        apply_diffs(house, [_diff(RemoveOpeningMutation(room_id="master_bedroom", opening_index=99))])


def test_apply_diffs_does_not_mutate_input():
    house = _two_room_house()
    before = house.model_dump(mode="json")
    apply_diffs(
        house,
        [_diff(UpdateRoomMutation(room_id="master_bedroom", name="Renamed"))],
    )
    assert house.model_dump(mode="json") == before


def test_apply_diff_with_no_mutation_is_passthrough():
    house = _two_room_house()
    projected = apply_diffs(
        house,
        [
            RequirementDiff(
                proposed=ProposedRequirement(
                    scope="global", type="material", statement="Use Mangalore tiles."
                ),
                source_span="Mangalore tiles",
            )
        ],
    )
    assert projected.model_dump(mode="json") == house.model_dump(mode="json")


def test_validate_projection_surfaces_setback_violation():
    house = House(plot=PLOT, rooms=[_living()])
    bad = Room(
        id="garage",
        name="Garage",
        polygon=[(0, 0), (4, 0), (4, 4), (0, 4)],  # crosses 2m side + 3m front setback
        openings=[Opening(type="door", wall="N", position_m=1.0, width_m=0.9, to_room="living_room")],
        camera=Camera(x=2, y=2, z=1.6, yaw_deg=0.0),
    )
    issues = validate_projection(
        house,
        [
            _diff(AddRoomMutation(room=bad)),
            _diff(
                AddOpeningMutation(
                    room_id="living_room",
                    opening=Opening(
                        type="door", wall="S", position_m=1.0, width_m=0.9, to_room="garage"
                    ),
                )
            ),
        ],
    )
    codes = {i.code for i in issues}
    assert "room_violates_setback" in codes


def test_validate_projection_clean_for_valid_diffs():
    house = House(plot=PLOT, rooms=[_living()])
    diffs = [
        _diff(AddRoomMutation(room=_master())),
        _diff(
            AddOpeningMutation(
                room_id="living_room",
                opening=Opening(
                    type="door", wall="E", position_m=1.0, width_m=0.9, to_room="master_bedroom"
                ),
            )
        ),
    ]
    assert validate_projection(house, diffs) == []


def test_affected_room_ids_unions_explicit_and_mutation_targets():
    diffs = [
        RequirementDiff(
            proposed=ProposedRequirement(scope="kitchen", type="adjacency", statement="x"),
            affected_rooms=["kitchen", "living_room"],
            source_span="x",
            mutation=AddOpeningMutation(
                room_id="kitchen",
                opening=Opening(type="door", wall="W", position_m=1, width_m=0.9, to_room="living_room"),
            ),
        ),
        RequirementDiff(
            proposed=ProposedRequirement(scope="master_bedroom", type="dimension", statement="y"),
            source_span="y",
            mutation=UpdateRoomMutation(room_id="master_bedroom", name="Master"),
        ),
    ]
    assert affected_room_ids(diffs) == {"kitchen", "living_room", "master_bedroom"}

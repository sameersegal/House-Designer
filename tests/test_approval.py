from __future__ import annotations

from pathlib import Path

import pytest

from goa_house.approval import ApprovalError, approve_diffs, reject_diffs
from goa_house.diffs import (
    AddOpeningMutation,
    AddRoomMutation,
    ProposedRequirement,
    RequirementDiff,
)
from goa_house.state import (
    Camera,
    House,
    Opening,
    Plot,
    Room,
    Setbacks,
    load_house,
    load_requirements,
    save_house,
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
        openings=[Opening(type="window", wall="N", position_m=2.0, width_m=1.5)],
        camera=Camera(x=6.5, y=6.0, z=1.6, yaw_deg=0.0),
    )


def _master_room() -> Room:
    return Room(
        id="master_bedroom",
        name="Master Bedroom",
        polygon=[(14, 7), (18, 7), (18, 12), (14, 12)],
        openings=[
            Opening(type="window", wall="N", position_m=1.0, width_m=1.2),
            Opening(type="door", wall="W", position_m=1.0, width_m=0.9, to_room="living_room"),
        ],
        camera=Camera(x=16.0, y=9.5, z=1.6, yaw_deg=0.0),
    )


@pytest.fixture
def design_dir(tmp_path: Path) -> Path:
    d = tmp_path / "designs" / "test"
    d.mkdir(parents=True)
    save_house(House(plot=PLOT, rooms=[_living()]), d / "house.json")
    return d


def _add_master_diffs() -> list[RequirementDiff]:
    return [
        RequirementDiff(
            proposed=ProposedRequirement(
                scope="master_bedroom",
                type="feature",
                statement="Add a master bedroom in the NE corner.",
            ),
            affected_rooms=["master_bedroom"],
            source_span="master bedroom in the NE corner",
            mutation=AddRoomMutation(room=_master_room()),
        ),
        RequirementDiff(
            proposed=ProposedRequirement(
                scope="living_room",
                type="adjacency",
                statement="Living room connects to the master bedroom on its E wall.",
            ),
            affected_rooms=["living_room"],
            source_span="master bedroom",
            mutation=AddOpeningMutation(
                room_id="living_room",
                opening=Opening(
                    type="door", wall="E", position_m=2.0, width_m=0.9, to_room="master_bedroom"
                ),
            ),
        ),
    ]


def test_approve_mutates_house_and_logs_requirements(design_dir: Path):
    result = approve_diffs(_add_master_diffs(), "Add a 4x5m master bedroom NE.", design_dir)

    house = load_house(design_dir / "house.json")
    assert {r.id for r in house.rooms} == {"living_room", "master_bedroom"}
    assert len(result["applied"]) == 2
    assert result["applied"] == ["req_0001", "req_0002"]
    assert set(result["affected_rooms"]) == {"living_room", "master_bedroom"}
    assert result["snapshot"].startswith("house.v")

    reqs = load_requirements(design_dir / "requirements.jsonl")
    assert [r.id for r in reqs] == ["req_0001", "req_0002"]
    assert all(r.status == "approved" for r in reqs)
    assert reqs[0].source_prompt == "Add a 4x5m master bedroom NE."


def test_approve_writes_snapshot(design_dir: Path):
    approve_diffs(_add_master_diffs(), "x", design_dir)
    snaps = sorted(design_dir.glob("house.v*.json"))
    assert len(snaps) >= 1


def test_approve_renders_artifacts_for_affected_rooms(design_dir: Path):
    approve_diffs(_add_master_diffs(), "x", design_dir)
    panos = design_dir / "panos"
    massing = design_dir / "massing"
    assert (panos / "master_bedroom.jpg").exists()
    assert (panos / "living_room.jpg").exists()  # affected via add_opening
    assert (massing / "topdown.png").exists()
    assert (massing / "master_bedroom" / "topdown.png").exists()


def test_approve_blocks_on_hard_validation(design_dir: Path):
    bad = Room(
        id="garage",
        name="Garage",
        polygon=[(0, 0), (4, 0), (4, 4), (0, 4)],  # crosses setback
        openings=[Opening(type="door", wall="N", position_m=1.0, width_m=0.9, to_room="living_room")],
        camera=Camera(x=2, y=2, z=1.6, yaw_deg=0.0),
    )
    diffs = [
        RequirementDiff(
            proposed=ProposedRequirement(scope="garage", type="feature", statement="Add garage."),
            affected_rooms=["garage"],
            source_span="garage",
            mutation=AddRoomMutation(room=bad),
        ),
        RequirementDiff(
            proposed=ProposedRequirement(scope="living_room", type="adjacency", statement="Door"),
            affected_rooms=["living_room"],
            source_span="garage",
            mutation=AddOpeningMutation(
                room_id="living_room",
                opening=Opening(type="door", wall="S", position_m=1.0, width_m=0.9, to_room="garage"),
            ),
        ),
    ]
    with pytest.raises(ApprovalError) as ei:
        approve_diffs(diffs, "x", design_dir)
    codes = {i.code for i in ei.value.issues}
    assert "room_violates_setback" in codes
    # nothing should have been written
    assert not (design_dir / "requirements.jsonl").exists()
    house = load_house(design_dir / "house.json")
    assert {r.id for r in house.rooms} == {"living_room"}


def test_approve_marks_superseded(design_dir: Path):
    # First approval: creates req_0001
    initial = [
        RequirementDiff(
            proposed=ProposedRequirement(
                scope="global", type="material", statement="Walls are lime plaster."
            ),
            source_span="lime plaster",
        )
    ]
    approve_diffs(initial, "Walls in lime plaster.", design_dir)

    # Second approval: conflicts with req_0001
    superseding = [
        RequirementDiff(
            proposed=ProposedRequirement(
                scope="global", type="material", statement="Walls are exposed laterite."
            ),
            conflicts_with=["req_0001"],
            source_span="laterite",
        )
    ]
    result = approve_diffs(superseding, "Switch to laterite walls.", design_dir)

    reqs = load_requirements(design_dir / "requirements.jsonl")
    by_id = {r.id: r for r in reqs}
    assert by_id["req_0001"].status == "superseded"
    assert by_id["req_0002"].status == "approved"
    assert by_id["req_0002"].supersedes == "req_0001"
    assert result["superseded"] == ["req_0001"]


def test_reject_appends_record_without_mutation(design_dir: Path):
    diffs = _add_master_diffs()
    result = reject_diffs(diffs, "x", "too expensive", design_dir)

    house = load_house(design_dir / "house.json")
    assert {r.id for r in house.rooms} == {"living_room"}  # untouched
    reqs = load_requirements(design_dir / "requirements.jsonl")
    assert [r.status for r in reqs] == ["rejected", "rejected"]
    assert all(r.rejection_reason == "too expensive" for r in reqs)
    assert result["rejected"] == ["req_0001", "req_0002"]


def test_reject_accepts_none_reason(design_dir: Path):
    diffs = [
        RequirementDiff(
            proposed=ProposedRequirement(scope="global", type="material", statement="x"),
            source_span="x",
        )
    ]
    reject_diffs(diffs, "x", None, design_dir)
    reqs = load_requirements(design_dir / "requirements.jsonl")
    assert reqs[0].rejection_reason is None


def test_approve_requirement_only_diff_skips_rerender(tmp_path: Path):
    d = tmp_path / "designs" / "test"
    d.mkdir(parents=True)
    save_house(House(plot=PLOT, rooms=[_living()]), d / "house.json")
    diffs = [
        RequirementDiff(
            proposed=ProposedRequirement(
                scope="global", type="material", statement="Use Mangalore tiles."
            ),
            source_span="Mangalore tiles",
        )
    ]
    result = approve_diffs(diffs, "x", d)
    assert result["affected_rooms"] == []
    # No artifacts should have been written
    assert not (d / "panos").exists()
    assert not (d / "massing").exists()

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field

from goa_house.state import (
    Camera,
    House,
    Opening,
    Point,
    RequirementType,
    Room,
    ValidationIssue,
    validate_house,
)


class ProposedRequirement(BaseModel):
    scope: str
    type: RequirementType
    statement: str = Field(max_length=160)


class AddRoomMutation(BaseModel):
    op: Literal["add_room"] = "add_room"
    room: Room


class UpdateRoomMutation(BaseModel):
    op: Literal["update_room"] = "update_room"
    room_id: str
    name: Optional[str] = None
    polygon: Optional[list[Point]] = None
    floor: Optional[int] = None
    camera: Optional[Camera] = None
    ceiling_height_m: Optional[float] = Field(default=None, gt=0)


class RemoveRoomMutation(BaseModel):
    op: Literal["remove_room"] = "remove_room"
    room_id: str


class AddOpeningMutation(BaseModel):
    op: Literal["add_opening"] = "add_opening"
    room_id: str
    opening: Opening


class RemoveOpeningMutation(BaseModel):
    op: Literal["remove_opening"] = "remove_opening"
    room_id: str
    opening_index: int = Field(ge=0)


Mutation = Annotated[
    Union[
        AddRoomMutation,
        UpdateRoomMutation,
        RemoveRoomMutation,
        AddOpeningMutation,
        RemoveOpeningMutation,
    ],
    Field(discriminator="op"),
]


class RequirementDiff(BaseModel):
    proposed: ProposedRequirement
    affected_rooms: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    suggested_resolution: Optional[str] = None
    source_span: str
    mutation: Optional[Mutation] = None


class ExtractorResult(BaseModel):
    kind: Literal["diffs", "clarification"]
    diffs: list[RequirementDiff] = Field(default_factory=list)
    question: Optional[str] = None


class DiffApplyError(ValueError):
    """Raised when a mutation cannot be applied to the current House."""


def apply_diffs(house: House, diffs: list[RequirementDiff]) -> House:
    """Return a new House with each diff's mutation applied in order.

    Pure function; does not write to disk and does not run semantic validation.
    Pydantic field-level validation still runs because we rebuild the House.
    """
    payload = house.model_dump(mode="json")
    rooms: list[dict] = payload["rooms"]

    for diff in diffs:
        m = diff.mutation
        if m is None:
            continue
        if isinstance(m, AddRoomMutation):
            if any(r["id"] == m.room.id for r in rooms):
                raise DiffApplyError(f"room {m.room.id} already exists")
            rooms.append(m.room.model_dump(mode="json"))
        elif isinstance(m, UpdateRoomMutation):
            target = _find_room_dict(rooms, m.room_id)
            if m.name is not None:
                target["name"] = m.name
            if m.polygon is not None:
                target["polygon"] = [list(p) for p in m.polygon]
            if m.floor is not None:
                target["floor"] = m.floor
            if m.camera is not None:
                target["camera"] = m.camera.model_dump(mode="json")
            if m.ceiling_height_m is not None:
                target["ceiling_height_m"] = m.ceiling_height_m
        elif isinstance(m, RemoveRoomMutation):
            idx = _find_room_index(rooms, m.room_id)
            rooms.pop(idx)
        elif isinstance(m, AddOpeningMutation):
            target = _find_room_dict(rooms, m.room_id)
            target.setdefault("openings", []).append(m.opening.model_dump(mode="json"))
        elif isinstance(m, RemoveOpeningMutation):
            target = _find_room_dict(rooms, m.room_id)
            openings = target.get("openings", [])
            if m.opening_index >= len(openings):
                raise DiffApplyError(
                    f"room {m.room_id} has no opening at index {m.opening_index}"
                )
            openings.pop(m.opening_index)

    return House.model_validate(payload)


def validate_projection(house: House, diffs: list[RequirementDiff]) -> list[ValidationIssue]:
    """Apply diffs to a copy of `house` and run full semantic validation.

    Raises DiffApplyError if a mutation references a missing room or duplicates
    an id; surfaces Pydantic ValidationError unchanged for malformed payloads.
    """
    projected = apply_diffs(house, diffs)
    return validate_house(projected)


def affected_room_ids(diffs: list[RequirementDiff]) -> set[str]:
    """Union of `affected_rooms` plus any room ids touched by mutations."""
    out: set[str] = set()
    for d in diffs:
        out.update(d.affected_rooms)
        m = d.mutation
        if m is None:
            continue
        if isinstance(m, AddRoomMutation):
            out.add(m.room.id)
        else:
            out.add(m.room_id)
    return out


def _find_room_dict(rooms: list[dict], room_id: str) -> dict:
    for r in rooms:
        if r["id"] == room_id:
            return r
    raise DiffApplyError(f"room {room_id} not found")


def _find_room_index(rooms: list[dict], room_id: str) -> int:
    for i, r in enumerate(rooms):
        if r["id"] == room_id:
            return i
    raise DiffApplyError(f"room {room_id} not found")

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator
from shapely.geometry import LineString, Polygon

Point = tuple[float, float]

Wall = Literal["N", "S", "E", "W"]
OpeningType = Literal["door", "window"]
RequirementType = Literal[
    "orientation",
    "dimension",
    "adjacency",
    "material",
    "feature",
    "constraint",
]
RequirementStatus = Literal["proposed", "approved", "rejected", "superseded"]

_ROOM_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SNAPSHOT_RE = re.compile(r"\.v(\d+)\.json$")
_REQ_ID_RE = re.compile(r"^req_(\d+)$")


class Setbacks(BaseModel):
    front: float = Field(ge=0)
    rear: float = Field(ge=0)
    side: float = Field(ge=0)


class Plot(BaseModel):
    boundary: list[Point] = Field(min_length=3)
    north_deg: float = 0.0
    setbacks: Setbacks

    @field_validator("boundary")
    @classmethod
    def _validate_boundary(cls, v: list[Point]) -> list[Point]:
        for p in v:
            if len(p) != 2:
                raise ValueError("boundary points must be (x, y) pairs")
        poly = Polygon(v)
        if not poly.is_valid:
            raise ValueError("boundary polygon is not valid")
        if poly.area <= 0:
            raise ValueError("boundary polygon has zero area")
        return v


class Opening(BaseModel):
    type: OpeningType
    wall: Wall
    position_m: float = Field(ge=0)
    width_m: float = Field(gt=0)
    height_m: Optional[float] = Field(default=None, gt=0)
    to_room: Optional[str] = None

    @model_validator(mode="after")
    def _cross_checks(self) -> "Opening":
        if self.type == "door" and not self.to_room:
            raise ValueError("door openings must reference a to_room")
        if self.type == "window" and self.to_room is not None:
            raise ValueError("window openings must not reference to_room")
        return self


class Camera(BaseModel):
    x: float
    y: float
    z: float = 1.6
    yaw_deg: float = 0.0


class Room(BaseModel):
    id: str
    name: str
    polygon: list[Point] = Field(min_length=3)
    floor: int = 0
    ceiling_height_m: float = Field(gt=0, default=3.0)
    openings: list[Opening] = Field(default_factory=list)
    camera: Camera

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _ROOM_ID_RE.match(v):
            raise ValueError("room id must be snake_case starting with a letter")
        return v

    @field_validator("polygon")
    @classmethod
    def _validate_polygon(cls, v: list[Point]) -> list[Point]:
        for p in v:
            if len(p) != 2:
                raise ValueError("polygon points must be (x, y) pairs")
        return v

    def shapely(self) -> Polygon:
        return Polygon(self.polygon)


class Style(BaseModel):
    period: str = "Indo-Portuguese"
    materials: list[str] = Field(
        default_factory=lambda: [
            "lime plaster",
            "Mangalore tile",
            "rosewood",
            "oyster shell windows",
            "azulejo",
        ]
    )
    lighting: str = "late afternoon, warm"
    mood_tokens: list[str] = Field(
        default_factory=lambda: ["coastal humid", "tropical shade", "cross-ventilated"]
    )


class House(BaseModel):
    version: int = 1
    plot: Plot
    rooms: list[Room] = Field(default_factory=list)
    style: Style = Field(default_factory=Style)

    @model_validator(mode="after")
    def _unique_room_ids(self) -> "House":
        ids = [r.id for r in self.rooms]
        if len(ids) != len(set(ids)):
            raise ValueError("room ids must be unique")
        return self

    def room_by_id(self, room_id: str) -> Optional[Room]:
        return next((r for r in self.rooms if r.id == room_id), None)

    def plot_polygon(self) -> Polygon:
        return Polygon(self.plot.boundary)

    def buildable_area(self) -> Polygon:
        inset = max(
            self.plot.setbacks.front,
            self.plot.setbacks.rear,
            self.plot.setbacks.side,
        )
        return self.plot_polygon().buffer(-inset, join_style=2)


class Requirement(BaseModel):
    id: str
    ts: str
    scope: str
    type: RequirementType
    statement: str
    source_prompt: str = ""
    status: RequirementStatus = "proposed"
    supersedes: Optional[str] = None
    conflicts_with: list[str] = Field(default_factory=list)

    @staticmethod
    def next_id(existing: list["Requirement"]) -> str:
        nums: list[int] = []
        for r in existing:
            m = _REQ_ID_RE.match(r.id)
            if m:
                nums.append(int(m.group(1)))
        return f"req_{(max(nums, default=0) + 1):04d}"


class ValidationIssue(BaseModel):
    severity: Literal["hard", "soft"]
    code: str
    message: str
    subject: Optional[str] = None


def validate_house(house: House) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    plot_poly = house.plot_polygon()
    if not plot_poly.is_valid or plot_poly.area <= 0:
        issues.append(
            ValidationIssue(
                severity="hard",
                code="plot_invalid",
                message="plot polygon is not valid",
            )
        )
        return issues

    buildable = house.buildable_area()

    for room in house.rooms:
        rpoly = room.shapely()
        if not rpoly.is_valid or rpoly.area <= 0:
            issues.append(
                ValidationIssue(
                    severity="hard",
                    code="room_invalid",
                    message=f"room {room.id} polygon is not valid",
                    subject=room.id,
                )
            )
            continue
        if not plot_poly.covers(rpoly):
            issues.append(
                ValidationIssue(
                    severity="hard",
                    code="room_outside_plot",
                    message=f"room {room.id} lies outside plot",
                    subject=room.id,
                )
            )
        elif not buildable.is_empty and not buildable.covers(rpoly):
            issues.append(
                ValidationIssue(
                    severity="hard",
                    code="room_violates_setback",
                    message=f"room {room.id} violates setback envelope",
                    subject=room.id,
                )
            )

    for i in range(len(house.rooms)):
        for j in range(i + 1, len(house.rooms)):
            a, b = house.rooms[i], house.rooms[j]
            inter = a.shapely().intersection(b.shapely())
            if inter.area > 1e-6:
                issues.append(
                    ValidationIssue(
                        severity="hard",
                        code="room_overlap",
                        message=f"rooms {a.id} and {b.id} overlap",
                        subject=a.id,
                    )
                )

    _check_openings(house, issues)
    _check_connectivity(house, issues)
    return issues


def _check_openings(house: House, issues: list[ValidationIssue]) -> None:
    for room in house.rooms:
        rpoly = room.shapely()
        minx, miny, maxx, maxy = rpoly.bounds
        walls: dict[str, LineString] = {
            "N": LineString([(minx, maxy), (maxx, maxy)]),
            "S": LineString([(minx, miny), (maxx, miny)]),
            "E": LineString([(maxx, miny), (maxx, maxy)]),
            "W": LineString([(minx, miny), (minx, maxy)]),
        }
        door_targets = {o.to_room for o in room.openings if o.type == "door"}
        for target in door_targets:
            if target and house.room_by_id(target) is None:
                issues.append(
                    ValidationIssue(
                        severity="hard",
                        code="door_target_missing",
                        message=f"room {room.id} door references unknown room {target}",
                        subject=room.id,
                    )
                )
        for o in room.openings:
            length = walls[o.wall].length
            if o.position_m + o.width_m > length + 1e-6:
                issues.append(
                    ValidationIssue(
                        severity="hard",
                        code="opening_off_wall",
                        message=(
                            f"room {room.id} opening on wall {o.wall} at "
                            f"{o.position_m}m+{o.width_m}m exceeds wall length {length:.2f}m"
                        ),
                        subject=room.id,
                    )
                )


def _check_connectivity(house: House, issues: list[ValidationIssue]) -> None:
    if len(house.rooms) < 2:
        return
    graph: dict[str, set[str]] = {r.id: set() for r in house.rooms}
    for r in house.rooms:
        for o in r.openings:
            if o.type != "door" or not o.to_room or o.to_room not in graph:
                continue
            graph[r.id].add(o.to_room)
            graph[o.to_room].add(r.id)
    seen: set[str] = set()
    stack = [house.rooms[0].id]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(graph[cur] - seen)
    for rid in (r.id for r in house.rooms):
        if rid not in seen:
            issues.append(
                ValidationIssue(
                    severity="hard",
                    code="room_unreachable",
                    message=f"room {rid} not reachable via doors",
                    subject=rid,
                )
            )


def load_house(path: Path | str) -> House:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return House.model_validate(data)


def save_house(house: House, path: Path | str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = house.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return _write_snapshot(house, path)


def _write_snapshot(house: House, base: Path) -> Path:
    nums: list[int] = []
    for s in base.parent.glob(f"{base.stem}.v*.json"):
        m = _SNAPSHOT_RE.search(s.name)
        if m:
            nums.append(int(m.group(1)))
    n = max(nums, default=0) + 1
    snap_path = base.parent / f"{base.stem}.v{n}.json"
    snap_path.write_text(
        json.dumps(house.model_dump(mode="json"), indent=2), encoding="utf-8"
    )
    return snap_path


def load_requirements(path: Path | str) -> list[Requirement]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[Requirement] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(Requirement.model_validate_json(line))
    return out


def append_requirement(req: Requirement, path: Path | str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(req.model_dump_json() + "\n")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

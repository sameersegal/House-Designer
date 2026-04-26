from __future__ import annotations

from typing import Iterable

from goa_house.state import House, Requirement, Room

STYLE_VERSION = 1

OUTPUT_SPEC = (
    "Output: equirectangular panorama, 4096x2048 pixels, 360x180 degree coverage. "
    "The leftmost and rightmost image columns must be visually continuous (seam wraps). "
    "Photoreal interior, daylight, no text, no people, no signs, no UI overlays, "
    "no compass markers, no watermarks."
)

COMPASS_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def wall_compass_direction(wall: str, north_deg: float) -> str:
    """Compass direction the outward normal of a plot-local wall points toward.

    Wall labels are plot-local: N=+y, E=+x, S=-y, W=-x. `north_deg` is the
    bearing of the plot's +y axis east of true north.
    """
    base = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}[wall]
    return _bearing_to_compass8(base + north_deg)


def yaw_compass_direction(yaw_deg: float, north_deg: float = 0.0) -> str:
    return _bearing_to_compass8(yaw_deg + north_deg)


def build_room_prompt(
    house: House,
    room_id: str,
    requirements: Iterable[Requirement] = (),
) -> str:
    room = house.room_by_id(room_id)
    if room is None:
        raise ValueError(f"unknown room: {room_id}")

    sections = [
        _style_section(house),
        _room_facts_section(house, room),
        _requirements_section(room, requirements),
        _camera_section(house, room),
        _output_spec_section(),
    ]
    return "\n\n".join(sections) + "\n"


def _style_section(house: House) -> str:
    s = house.style
    materials = ", ".join(s.materials) if s.materials else "(none)"
    moods = ", ".join(s.mood_tokens) if s.mood_tokens else "(none)"
    return "\n".join(
        [
            "[STYLE]",
            f"period: {s.period}",
            f"materials: {materials}",
            f"lighting: {s.lighting}",
            f"mood: {moods}",
        ]
    )


def _room_facts_section(house: House, room: Room) -> str:
    width, depth = _room_dimensions(room)
    lines = [
        "[ROOM FACTS]",
        f"id: {room.id}",
        f"name: {room.name}",
        f"footprint: {width:.2f} m x {depth:.2f} m (plot-local x by y)",
        f"ceiling_height: {room.ceiling_height_m:.2f} m",
        f"floor_index: {room.floor}",
    ]
    if room.openings:
        lines.append("openings:")
        for o in room.openings:
            compass = wall_compass_direction(o.wall, house.plot.north_deg)
            if o.type == "door":
                lines.append(
                    f"  - door on {compass} wall ({o.wall}), "
                    f"{o.width_m:.2f} m wide, leads to {o.to_room}"
                )
            else:
                height_str = (
                    f"{o.height_m:.2f} m tall" if o.height_m is not None else "default height"
                )
                lines.append(
                    f"  - window on {compass} wall ({o.wall}), "
                    f"{o.width_m:.2f} m wide, {height_str}"
                )
    else:
        lines.append("openings: none")
    return "\n".join(lines)


def _requirements_section(room: Room, requirements: Iterable[Requirement]) -> str:
    relevant = [
        r
        for r in requirements
        if r.status == "approved" and r.scope in (room.id, "global")
    ]
    if not relevant:
        return "[REQUIREMENTS]\n(none)"
    relevant = sorted(relevant, key=lambda r: r.id)
    lines = ["[REQUIREMENTS]"]
    for r in relevant:
        lines.append(f"- {r.id} [{r.scope}/{r.type}] {r.statement}")
    return "\n".join(lines)


def _camera_section(house: House, room: Room) -> str:
    cam = room.camera
    facing = yaw_compass_direction(cam.yaw_deg, house.plot.north_deg)
    minx, miny, _, _ = _room_bounds(room)
    rel_x = cam.x - minx
    rel_y = cam.y - miny
    return "\n".join(
        [
            "[CAMERA]",
            f"position_room_local: x={rel_x:.2f} m, y={rel_y:.2f} m",
            f"eye_height: {cam.z:.2f} m",
            f"initial_facing: {facing} (yaw {cam.yaw_deg:.1f} deg)",
        ]
    )


def _output_spec_section() -> str:
    return "[OUTPUT SPEC]\n" + OUTPUT_SPEC


def _bearing_to_compass8(bearing_deg: float) -> str:
    b = bearing_deg % 360.0
    idx = int((b + 22.5) % 360.0 // 45.0)
    return COMPASS_8[idx]


def _room_dimensions(room: Room) -> tuple[float, float]:
    minx, miny, maxx, maxy = _room_bounds(room)
    return (maxx - minx, maxy - miny)


def _room_bounds(room: Room) -> tuple[float, float, float, float]:
    xs = [p[0] for p in room.polygon]
    ys = [p[1] for p in room.polygon]
    return (min(xs), min(ys), max(xs), max(ys))

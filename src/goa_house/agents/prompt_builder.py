from __future__ import annotations

from goa_house.state import House, Requirement, Room, Style

_OCTANTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
_WALL_BEARING = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}


def build_panorama_prompt(
    house: House,
    room: Room,
    requirements: list[Requirement] | None = None,
    output_size: tuple[int, int] = (2048, 1024),
) -> str:
    sections = [
        _style_section(house.style),
        _room_facts_section(house, room),
        _requirements_section(room.id, requirements or []),
        _camera_section(house, room),
        _output_section(output_size),
    ]
    return "\n\n".join(sections)


def _style_section(style: Style) -> str:
    materials = ", ".join(style.materials)
    moods = ", ".join(style.mood_tokens)
    return (
        "[STYLE]\n"
        f"Period: {style.period}.\n"
        f"Lighting: {style.lighting}.\n"
        f"Signature materials: {materials}.\n"
        f"Mood: {moods}."
    )


def _room_facts_section(house: House, room: Room) -> str:
    width, depth = _room_dims(room)
    lines = [
        "[ROOM FACTS]",
        f"Room id: {room.id}.",
        f"Room name: {room.name}.",
        f"Footprint: {width:.1f} m x {depth:.1f} m.",
        f"Ceiling height: {room.ceiling_height_m:.1f} m.",
        f"Floor: {room.floor}.",
    ]
    if room.openings:
        lines.append("Openings:")
        for o in room.openings:
            compass = wall_to_compass(o.wall, house.plot.north_deg)
            if o.type == "door":
                lines.append(
                    f"  - door on {compass} wall, {o.width_m:.1f} m wide,"
                    f" {o.position_m:.1f} m from corner, leads to {o.to_room}"
                )
            else:
                tall = f", {o.height_m:.1f} m tall" if o.height_m else ""
                lines.append(
                    f"  - window on {compass} wall, {o.width_m:.1f} m wide{tall},"
                    f" {o.position_m:.1f} m from corner"
                )
    else:
        lines.append("Openings: none defined.")
    return "\n".join(lines)


def _requirements_section(room_id: str, requirements: list[Requirement]) -> str:
    scoped = [
        r
        for r in requirements
        if r.scope in (room_id, "global") and r.status == "approved"
    ]
    if not scoped:
        return "[REQUIREMENTS]\nNo approved requirements scoped to this room."
    lines = ["[REQUIREMENTS]"]
    for r in scoped:
        lines.append(f"- ({r.type}) {r.statement}")
    return "\n".join(lines)


def _camera_section(house: House, room: Room) -> str:
    cam = room.camera
    facing = bearing_to_compass((cam.yaw_deg + house.plot.north_deg) % 360.0)
    return (
        "[CAMERA]\n"
        f"First-person eye height: {cam.z:.2f} m.\n"
        f"Camera position in room: ({cam.x:.1f} m, {cam.y:.1f} m).\n"
        f"Camera faces yaw {cam.yaw_deg:.0f}° in plot frame (compass {facing})."
    )


def _output_section(size: tuple[int, int]) -> str:
    w, h = size
    return (
        "[OUTPUT SPEC]\n"
        f"Render an equirectangular 360° panorama at {w}x{h} pixels (2:1 aspect).\n"
        "Preserve the wall, ceiling, floor, door, and window placement exactly as shown in the reference image; "
        "do not add, remove, relocate, or resize openings.\n"
        "Replace the painted markers in the reference with photorealistic Indo-Portuguese interior surfaces consistent with the [STYLE] block.\n"
        "Ensure the left and right edges seam continuously (column 0 must wrap to the rightmost column without a visible discontinuity)."
    )


def _room_dims(room: Room) -> tuple[float, float]:
    xs = [p[0] for p in room.polygon]
    ys = [p[1] for p in room.polygon]
    return (max(xs) - min(xs), max(ys) - min(ys))


def wall_to_compass(wall: str, north_deg: float) -> str:
    return bearing_to_compass((_WALL_BEARING[wall] + north_deg) % 360.0)


def bearing_to_compass(bearing: float) -> str:
    bearing = bearing % 360.0
    idx = int((bearing + 22.5) // 45) % 8
    return _OCTANTS[idx]

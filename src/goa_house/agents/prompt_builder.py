from __future__ import annotations

from goa_house.state import House, Requirement, Room, Style

_OCTANTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
_WALL_BEARING = {"N": 0.0, "E": 90.0, "S": 180.0, "W": 270.0}

_ROOM_CHARACTER: dict[str, str] = {
    "living_room": (
        "Goan sala / drawing room. Furnish with a low rosewood coffee table on a coir or jute rug, "
        "two cane-seat planter chairs, a carved rosewood settee with embroidered cushions, "
        "a console table with a brass lamp, framed Indo-Portuguese lithographs, and a slow ceiling fan."
    ),
    "drawing_room": (
        "Goan sala / drawing room. Furnish with a low rosewood coffee table on a coir or jute rug, "
        "two cane-seat planter chairs, a carved rosewood settee with embroidered cushions, "
        "a console table with a brass lamp, framed Indo-Portuguese lithographs, and a slow ceiling fan."
    ),
    "kitchen": (
        "Working Goan kitchen. Show a built-in masonry counter with a black-stone or laterite top "
        "running along one wall, an iron stove or chulha with a heavy aluminium pot, a stone sink "
        "below the window, copper and brass utensils on open rosewood shelves, terracotta jars on "
        "the floor, hanging ladles, and a small wooden stool. The space must read as a kitchen at a glance."
    ),
    "dining": (
        "Goan dining room. Center the room on a long rosewood dining table with a runner, "
        "surrounded by six to eight cane-back rosewood chairs. Hang a brass-and-glass pendant lamp "
        "directly over the table. Add a sideboard with porcelain plates and a brass urli against one wall. "
        "The dining table must be the dominant element."
    ),
    "dining_room": (
        "Goan dining room. Center the room on a long rosewood dining table with a runner, "
        "surrounded by six to eight cane-back rosewood chairs. Hang a brass-and-glass pendant lamp "
        "directly over the table. Add a sideboard with porcelain plates and a brass urli against one wall. "
        "The dining table must be the dominant element."
    ),
    "master_bedroom": (
        "Master bedroom. Center on a four-poster carved rosewood bed with a white cotton spread and "
        "a quilted bedrunner, two bedside tables with brass reading lamps, a tall rosewood almirah "
        "(wardrobe), and a small writing desk with a cane chair. Mosquito-net rail above the bed."
    ),
    "bedroom": (
        "Bedroom. Place a carved rosewood bed (queen size) with linen pillows and a light cotton spread, "
        "a single bedside table with a small lamp, and a rosewood almirah (wardrobe). Add a cane chair, "
        "a framed picture, and a woven rug. Keep the room calm and uncluttered."
    ),
    "stairwell": (
        "Stairwell. The stairs themselves must be the dominant element: a single straight or quarter-turn "
        "flight of polished rosewood treads with a turned-baluster railing and a hand-rail. "
        "No bedroom or living-room furniture; a small console with a vase or a wall sconce is enough."
    ),
    "stairs": (
        "Stairwell. The stairs themselves must be the dominant element: a single straight or quarter-turn "
        "flight of polished rosewood treads with a turned-baluster railing and a hand-rail. "
        "No bedroom or living-room furniture; a small console with a vase or a wall sconce is enough."
    ),
    "landing": (
        "Upper landing / corridor. Show the top of the stair flight emerging through a balustraded "
        "opening in the floor; a console table with a vase, an azulejo tile panel on the wall, "
        "and a sconce or hanging lantern. No bedroom furniture."
    ),
    "corridor": (
        "Goan upper corridor. A narrow upper-floor passageway running alongside a balustraded void "
        "open to a double-height living-dining room below; polished terracotta-tile floor, "
        "a rosewood console with a brass urli or vase, framed Indo-Portuguese lithographs on the wall, "
        "a hanging brass lantern, doorways visible to bedrooms along the run. The void / balustrade "
        "must read clearly — this is NOT a closed room and NOT a bedroom."
    ),
    "study": (
        "Study. A rosewood writing desk with a green-shade banker's lamp and a leather-bound book stack, "
        "a cane reading chair, a tall bookcase, and a framed map. Calm, lived-in."
    ),
    "veranda": (
        "Goan veranda / balcao. Built-in masonry seats along the parapet, low railing, "
        "potted palms, hanging lanterns. View opens to the garden."
    ),
    "balcao": (
        "Goan balcao / verandah. A covered open-air verandah with a low masonry parapet, "
        "wide arched openings to the garden framed by chamfered timber posts, an azulejo-tile dado below the parapet, "
        "built-in masonry seats along the parapet with embroidered cushions, hanging brass-and-glass lanterns, "
        "potted palms and a tulsi planter on the floor, a slow ceiling fan, glimpses of the mango-tree canopy "
        "through the open arches. The balcao must read unmistakably as a covered outdoor verandah at first glance — "
        "open arches, garden visible beyond, NOT an interior room."
    ),
    "utility": (
        "Goan utility / back-of-house service room. A working room: a stone or concrete laundry sink against one wall, "
        "a built-in masonry counter, hanging copper utensils, terracotta storage jars on the floor, "
        "a wooden rack with folded cotton linens, a single bare bulb or wall sconce, plain terracotta-tile floor, "
        "plain lime-plaster walls (NO decorative azulejo, NO carved rosewood furniture). "
        "Plain, functional, working. NOT a kitchen, NOT a living room."
    ),
    "powder": (
        "Goan powder room — a small half-bath / WC. Tight and intimate: a single white ceramic toilet, "
        "a small wall-hung basin with a brass tap, a small mirror with a simple wooden frame, an azulejo-tile dado "
        "on one wall, brass hooks for hand towels, terracotta-tile floor, a single brass wall sconce. "
        "NOT a full bathroom, NOT a bedroom — show the toilet and basin clearly."
    ),
}


def build_panorama_prompt(
    house: House,
    room: Room,
    requirements: list[Requirement] | None = None,
    output_size: tuple[int, int] = (2048, 1024),
) -> str:
    sections = [
        _style_section(house.style),
        _room_facts_section(house, room),
        _room_character_section(house, room),
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
            elif o.type == "stairs":
                target = house.room_by_id(o.to_room) if o.to_room else None
                direction = "up" if target and target.floor > room.floor else "down"
                target_name = target.name if target else (o.to_room or "?")
                lines.append(
                    f"  - stairs on {compass} wall, {o.width_m:.1f} m wide,"
                    f" {o.position_m:.1f} m from corner, leads {direction} to {target_name}"
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


def _room_character_section(house: House, room: Room) -> str:
    character = _classify_room(room.id)
    lines = ["[ROOM CHARACTER]", character]
    stair_note = _stair_direction_note(house, room)
    if stair_note:
        lines.append(stair_note)
    return "\n".join(lines)


def _classify_room(room_id: str) -> str:
    rid = room_id.lower()
    if rid in _ROOM_CHARACTER:
        return _ROOM_CHARACTER[rid]
    for key, body in _ROOM_CHARACTER.items():
        if key in rid:
            return body
    return (
        "Furnish appropriately for an Indo-Portuguese Goan house: rosewood furniture, "
        "woven jute rugs, a slow ceiling fan, framed lithographs, and a single brass or "
        "glass-shade lamp. Keep the room lived-in but uncluttered."
    )


def _stair_direction_note(house: House, room: Room) -> str:
    for o in room.openings:
        if o.type != "stairs" or not o.to_room:
            continue
        target = house.room_by_id(o.to_room)
        if target is None:
            continue
        compass = wall_to_compass(o.wall, house.plot.north_deg)
        if target.floor > room.floor:
            return (
                f"Stairs go UP to {target.name or o.to_room} on the {compass} wall. "
                f"The floor under the camera is a continuous, solid terracotta-tiled floor — "
                f"there is NO opening or railed void in the floor. "
                f"Render an ascending flight of polished rosewood stair treads and risers rising "
                f"from floor level upward and disappearing into the upper-floor opening, "
                f"with a turned-baluster handrail along its outer edge. "
                f"Do not draw a balustrade enclosing a hole in the floor; only the stair itself."
            )
        return (
            f"Stairs go DOWN to {target.name or o.to_room} on the {compass} wall. "
            f"This is an upper-floor landing: render a balustraded rectangular opening in the floor "
            f"with the top of a descending stair flight visible through it (turned-baluster railing "
            f"all around the void)."
        )
    return ""


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
        "The reference image is a schematic — the colored rectangles and tiny notches are diagrammatic. "
        "Replace them with photorealistic Indo-Portuguese interior surfaces consistent with the [STYLE] block.\n"
        "Furnish the room as described in [ROOM CHARACTER]; the room must read unmistakably as that room type at first glance, "
        "with the listed signature pieces clearly visible. An empty room is NOT acceptable.\n"
        "Walls, floors, doors, and ceilings must show only architectural materials (lime plaster, azulejo tile, terracotta, rosewood, glass). "
        "Do not paint room names, dimensions, compass letters, or any other text or signage onto any surface.\n"
        "Use a single consistent ceiling treatment throughout the entire 360° (either exposed rosewood beams with terracotta Mangalore-tile underside, OR smooth white lime-plaster vault — pick one).\n"
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

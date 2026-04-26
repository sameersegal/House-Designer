from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from PIL import Image, ImageDraw

from goa_house.state import House, Room
from goa_house.tour.pannellum import opening_center, wrap_180

PLOT_COLOR = "#f4efe6"
BUILDABLE_COLOR = "#e7ddc6"
ROOM_COLOR = "#b6c9d9"
HIGHLIGHT_COLOR = "#d97757"
DOOR_COLOR = "#8b5a2b"
WINDOW_COLOR = "#3b80c2"
NORTH_ARROW_COLOR = "#333"

FP_BLOCK_SIZE = (4096, 2048)
FP_WALL_FILL = (245, 243, 238)
FP_FLOOR_FILL = (210, 200, 185)
FP_CEILING_FILL = (235, 230, 220)
FP_LINE_COLOR = (40, 40, 40)
FP_DOOR_COLOR = (220, 130, 60)
FP_WINDOW_COLOR = (60, 130, 220)
FP_DOOR_TOP_M = 2.1
FP_DOOR_BOT_M = 0.0
FP_WINDOW_CENTER_Z_M = 1.2
FP_WINDOW_DEFAULT_HEIGHT_M = 1.2


def render_topdown(
    house: House,
    out_path: Path,
    highlight_room_id: Optional[str] = None,
    show_cameras: bool = True,
    figsize: tuple[float, float] = (8.0, 6.0),
    dpi: int = 140,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)

    _draw_plot(ax, house)
    _draw_buildable(ax, house)
    for room in house.rooms:
        _draw_room(ax, room, highlighted=(room.id == highlight_room_id))
        if show_cameras:
            _draw_camera(ax, room)
    _draw_north_arrow(ax, house)

    minx, miny, maxx, maxy = _bounds_with_padding(house, pad=1.5)
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("equal")
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    title = "Plot & Layout" if highlight_room_id is None else f"{highlight_room_id}"
    ax.set_title(title)
    ax.grid(True, linestyle=":", alpha=0.35)

    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


def _draw_plot(ax, house: House) -> None:
    ax.add_patch(MplPolygon(house.plot.boundary, closed=True, facecolor=PLOT_COLOR, edgecolor="#444", linewidth=1.5))


def _draw_buildable(ax, house: House) -> None:
    buildable = house.buildable_area()
    if buildable.is_empty:
        return
    geoms = [buildable] if buildable.geom_type == "Polygon" else list(buildable.geoms)
    for g in geoms:
        coords = list(g.exterior.coords)
        ax.add_patch(MplPolygon(coords, closed=True, facecolor=BUILDABLE_COLOR, edgecolor="#888", linewidth=0.8, linestyle="--"))


def _draw_room(ax, room: Room, highlighted: bool) -> None:
    face = HIGHLIGHT_COLOR if highlighted else ROOM_COLOR
    ax.add_patch(MplPolygon(room.polygon, closed=True, facecolor=face, edgecolor="#333", linewidth=1.3, alpha=0.85))
    cx, cy = _centroid(room)
    ax.text(cx, cy, room.name, ha="center", va="center", fontsize=9, color="#222")
    for opening in room.openings:
        ox, oy = opening_center(room, opening)
        color = DOOR_COLOR if opening.type == "door" else WINDOW_COLOR
        marker = "s" if opening.type == "door" else "o"
        ax.plot(ox, oy, marker=marker, color=color, markersize=6, markeredgecolor="#000", markeredgewidth=0.5)


def _draw_camera(ax, room: Room) -> None:
    cam = room.camera
    ax.plot(cam.x, cam.y, marker="*", color="#222", markersize=9)
    yaw_rad = math.radians(cam.yaw_deg)
    dx = math.sin(yaw_rad) * 0.8
    dy = math.cos(yaw_rad) * 0.8
    ax.arrow(cam.x, cam.y, dx, dy, head_width=0.25, head_length=0.25, fc="#222", ec="#222", length_includes_head=True)


def _draw_north_arrow(ax, house: House) -> None:
    minx, miny, maxx, maxy = _bounds_with_padding(house, pad=1.5)
    margin = 0.9
    ax_x = maxx - margin
    ax_y = maxy - margin
    length = 1.0
    rad = math.radians(house.plot.north_deg)
    dx = math.sin(rad) * length
    dy = math.cos(rad) * length
    ax.annotate(
        "N",
        xy=(ax_x + dx, ax_y + dy),
        xytext=(ax_x, ax_y),
        ha="center",
        va="center",
        fontsize=11,
        color=NORTH_ARROW_COLOR,
        arrowprops={"arrowstyle": "->", "color": NORTH_ARROW_COLOR, "lw": 1.6},
    )


def _centroid(room: Room) -> tuple[float, float]:
    xs = [p[0] for p in room.polygon]
    ys = [p[1] for p in room.polygon]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _bounds_with_padding(house: House, pad: float) -> tuple[float, float, float, float]:
    xs = [p[0] for p in house.plot.boundary]
    ys = [p[1] for p in house.plot.boundary]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def render_first_person_block(
    house: House,
    room: Room,
    out_path: Path,
    size: tuple[int, int] = FP_BLOCK_SIZE,
    samples_per_edge: int = 48,
) -> Path:
    """Render a textureless equirectangular massing block from the room camera.

    Walls are flat fill; floor/ceiling contours and corner verticals are drawn
    geometrically; doors and windows are outlined rectangles. Used as the
    image-conditioning input to gpt-image-2.
    """
    w, h = size
    img = Image.new("RGB", size, FP_WALL_FILL)
    draw = ImageDraw.Draw(img)

    cam = room.camera
    ceiling = room.ceiling_height_m

    floor_pts = _project_polygon(room, cam, ceiling, samples_per_edge, target_z=0.0, size=size)
    ceil_pts = _project_polygon(room, cam, ceiling, samples_per_edge, target_z=ceiling, size=size)

    _fill_band(draw, ceil_pts, size, above=True, fill=FP_CEILING_FILL)
    _fill_band(draw, floor_pts, size, above=False, fill=FP_FLOOR_FILL)

    _draw_wrapped_polyline(draw, floor_pts, w, color=FP_LINE_COLOR, line_width=4)
    _draw_wrapped_polyline(draw, ceil_pts, w, color=FP_LINE_COLOR, line_width=4)

    for vx, vy in room.polygon:
        dx, dy = vx - cam.x, vy - cam.y
        horiz = math.hypot(dx, dy)
        if horiz < 1e-3:
            continue
        col = _yaw_column(dx, dy, cam.yaw_deg, w)
        floor_row = _pitch_row(0.0 - cam.z, horiz, h)
        ceil_row = _pitch_row(ceiling - cam.z, horiz, h)
        draw.line([(col, ceil_row), (col, floor_row)], fill=FP_LINE_COLOR, width=3)

    for opening in room.openings:
        _draw_opening_block(draw, room, opening, size)

    _seam_wrap(img)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def _project_polygon(
    room: Room,
    cam,
    ceiling: float,
    samples_per_edge: int,
    target_z: float,
    size: tuple[int, int],
) -> list[tuple[int, int]]:
    w, h = size
    poly = room.polygon
    n = len(poly)
    out: list[tuple[int, int]] = []
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        for s in range(samples_per_edge + 1):
            t = s / samples_per_edge
            x = a[0] + (b[0] - a[0]) * t
            y = a[1] + (b[1] - a[1]) * t
            dx, dy = x - cam.x, y - cam.y
            horiz = math.hypot(dx, dy)
            if horiz < 1e-3:
                continue
            col = _yaw_column(dx, dy, cam.yaw_deg, w)
            row = _pitch_row(target_z - cam.z, horiz, h)
            out.append((col, row))
    return out


def _yaw_column(dx: float, dy: float, cam_yaw_deg: float, width: int) -> int:
    bearing = math.degrees(math.atan2(dx, dy))
    yaw = wrap_180(bearing - cam_yaw_deg)
    normalized = (yaw + 180.0) % 360.0
    return int(normalized / 360.0 * width)


def _pitch_row(dz: float, horiz: float, height: int) -> int:
    pitch = math.degrees(math.atan2(dz, horiz)) if horiz > 0 else 0.0
    return int((1.0 - (pitch + 90.0) / 180.0) * height)


def _draw_wrapped_polyline(draw, pts, image_width, color, line_width: int = 2) -> None:
    if len(pts) < 2:
        return
    for seg in _split_at_seam(pts, image_width):
        if len(seg) >= 2:
            draw.line(seg, fill=color, width=line_width)


def _draw_opening_block(draw, room: Room, opening, size: tuple[int, int]) -> None:
    w, h = size
    cam = room.camera
    cx, cy = opening_center(room, opening)
    dx, dy = cx - cam.x, cy - cam.y
    horiz = math.hypot(dx, dy)
    if horiz < 1e-3:
        return
    bearing = math.degrees(math.atan2(dx, dy))
    yaw = wrap_180(bearing - cam.yaw_deg)
    half_w_deg = math.degrees(math.atan2(opening.width_m / 2.0, horiz))
    if opening.type == "door":
        top_z = FP_DOOR_TOP_M
        bot_z = FP_DOOR_BOT_M
        color = FP_DOOR_COLOR
    else:
        cz = FP_WINDOW_CENTER_Z_M
        half_h = (opening.height_m or FP_WINDOW_DEFAULT_HEIGHT_M) / 2.0
        top_z = cz + half_h
        bot_z = cz - half_h
        color = FP_WINDOW_COLOR
    col_l = _yaw_column_from_yaw(yaw - half_w_deg, w)
    col_r = _yaw_column_from_yaw(yaw + half_w_deg, w)
    row_top = _pitch_row(top_z - cam.z, horiz, h)
    row_bot = _pitch_row(bot_z - cam.z, horiz, h)
    if col_l < col_r:
        draw.rectangle([col_l, row_top, col_r, row_bot], outline=color, width=6)
    else:
        draw.rectangle([col_l, row_top, w, row_bot], outline=color, width=6)
        draw.rectangle([0, row_top, col_r, row_bot], outline=color, width=6)


def _yaw_column_from_yaw(yaw_deg: float, width: int) -> int:
    normalized = (yaw_deg + 180.0) % 360.0
    return int(normalized / 360.0 * width)


def _fill_band(draw, pts, size, above: bool, fill) -> None:
    if not pts:
        return
    width, height = size
    rows_by_col: dict[int, int] = {}
    for col, row in pts:
        col = max(0, min(width - 1, col))
        row = max(0, min(height - 1, row))
        if above:
            rows_by_col[col] = min(row, rows_by_col.get(col, row))
        else:
            rows_by_col[col] = max(row, rows_by_col.get(col, row))
    for col in range(width):
        row = rows_by_col.get(col)
        if row is None:
            continue
        if above:
            draw.line([(col, 0), (col, row)], fill=fill, width=1)
        else:
            draw.line([(col, row), (col, height - 1)], fill=fill, width=1)


def _split_at_seam(pts, width):
    threshold = width // 2
    segments: list[list[tuple[int, int]]] = []
    current: list[tuple[int, int]] = []
    for p in pts:
        if not current:
            current.append(p)
            continue
        prev = current[-1]
        if abs(p[0] - prev[0]) > threshold:
            segments.append(current)
            current = [p]
        else:
            current.append(p)
    if current:
        segments.append(current)
    return segments


def _seam_wrap(img: Image.Image) -> None:
    left = img.crop((0, 0, 1, img.height))
    img.paste(left, (img.width - 1, 0))

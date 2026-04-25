from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon

from goa_house.state import House, Room
from goa_house.tour.pannellum import opening_center

PLOT_COLOR = "#f4efe6"
BUILDABLE_COLOR = "#e7ddc6"
ROOM_COLOR = "#b6c9d9"
HIGHLIGHT_COLOR = "#d97757"
DOOR_COLOR = "#8b5a2b"
WINDOW_COLOR = "#3b80c2"
NORTH_ARROW_COLOR = "#333"


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

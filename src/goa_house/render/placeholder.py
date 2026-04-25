from __future__ import annotations

import colorsys
import hashlib
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Polygon

from goa_house.state import House, Room
from goa_house.tour.pannellum import opening_center, wrap_180

PANO_SIZE = (2048, 1024)


def render_placeholder_pano(
    house: House,
    room: Room,
    out_path: Path,
    size: tuple[int, int] = PANO_SIZE,
) -> Path:
    w, h = size
    img = Image.new("RGB", size, (20, 20, 25))
    draw = ImageDraw.Draw(img)

    floor_rgb = (70, 50, 35)
    ceiling_rgb = (230, 220, 200)
    wall_rgb = _color_from_id(room.id)

    floor_top = int(h * 2 / 3)
    ceiling_bot = int(h / 3)
    draw.rectangle([0, 0, w, ceiling_bot], fill=ceiling_rgb)
    draw.rectangle([0, ceiling_bot, w, floor_top], fill=wall_rgb)
    draw.rectangle([0, floor_top, w, h], fill=floor_rgb)

    draw.line([(0, ceiling_bot), (w, ceiling_bot)], fill=(0, 0, 0), width=3)
    draw.line([(0, floor_top), (w, floor_top)], fill=(0, 0, 0), width=3)

    font = _load_font(48)
    small_font = _load_font(28)

    _draw_compass_markers(draw, w, h, room, font, small_font)
    _draw_openings(draw, w, h, room, small_font)
    _draw_room_label(draw, w, h, room, font, small_font)
    _draw_seam(img)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=88)
    return out_path


def render_all_placeholders(house: House, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return [render_placeholder_pano(house, r, out_dir / f"{r.id}.jpg") for r in house.rooms]


def _yaw_to_column(yaw_deg: float, width: int) -> int:
    normalized = (yaw_deg + 180.0) % 360.0
    return int(normalized / 360.0 * width)


def _pitch_to_row(pitch_deg: float, height: int) -> int:
    return int((1.0 - (pitch_deg + 90.0) / 180.0) * height)


def _draw_compass_markers(draw, w, h, room, font, small_font) -> None:
    cam_yaw = room.camera.yaw_deg
    for letter, bearing in [("N", 0.0), ("E", 90.0), ("S", 180.0), ("W", 270.0)]:
        yaw = wrap_180(bearing - cam_yaw)
        col = _yaw_to_column(yaw, w)
        row = int(h * 0.45)
        _centered_text(draw, (col, row), letter, font, fill=(255, 255, 255))
        draw.line([(col, row + 40), (col, row + 70)], fill=(255, 255, 255), width=2)


def _draw_openings(draw, w, h, room: Room, font) -> None:
    cam = room.camera
    for opening in room.openings:
        cx, cy = opening_center(room, opening)
        dx, dy = cx - cam.x, cy - cam.y
        horiz = math.hypot(dx, dy)
        if horiz < 1e-6:
            continue
        bearing_deg = math.degrees(math.atan2(dx, dy))
        yaw = wrap_180(bearing_deg - cam.yaw_deg)
        half_width_rad = math.atan2(opening.width_m / 2.0, horiz)
        half_width_deg = math.degrees(half_width_rad)

        if opening.type == "door":
            top_pitch = math.degrees(math.atan2(2.1 - cam.z, horiz))
            bot_pitch = math.degrees(math.atan2(0.0 - cam.z, horiz))
            color = (255, 210, 120)
            label = f"DOOR -> {opening.to_room}"
        elif opening.type == "stairs":
            top_pitch = math.degrees(math.atan2(2.1 - cam.z, horiz))
            bot_pitch = math.degrees(math.atan2(0.0 - cam.z, horiz))
            color = (200, 180, 255)
            label = f"STAIRS -> {opening.to_room}"
        else:
            window_center_z = 1.2
            window_half_h = (opening.height_m or 1.2) / 2.0
            top_pitch = math.degrees(math.atan2(window_center_z + window_half_h - cam.z, horiz))
            bot_pitch = math.degrees(math.atan2(window_center_z - window_half_h - cam.z, horiz))
            color = (180, 220, 255)
            label = "WINDOW"

        col_left = _yaw_to_column(yaw - half_width_deg, w)
        col_right = _yaw_to_column(yaw + half_width_deg, w)
        row_top = _pitch_to_row(top_pitch, h)
        row_bot = _pitch_to_row(bot_pitch, h)
        if col_left < col_right:
            draw.rectangle([col_left, row_top, col_right, row_bot], outline=color, width=4)
        else:
            draw.rectangle([col_left, row_top, w, row_bot], outline=color, width=4)
            draw.rectangle([0, row_top, col_right, row_bot], outline=color, width=4)

        label_col = _yaw_to_column(yaw, w)
        _centered_text(draw, (label_col, row_bot + 18), label, font, fill=color)


def _draw_room_label(draw, w, h, room, font, small_font) -> None:
    _centered_text(draw, (w // 2, h // 2 + 120), room.name.upper(), font, fill=(255, 255, 255))
    dims = _room_dims_m(room)
    subtitle = f"{dims[0]:.1f} x {dims[1]:.1f} m   ceiling {room.ceiling_height_m:.1f} m"
    _centered_text(draw, (w // 2, h // 2 + 170), subtitle, small_font, fill=(230, 230, 230))
    _centered_text(draw, (w // 2, h // 2 + 205), "placeholder panorama", small_font, fill=(200, 200, 200))


def _draw_seam(img: Image.Image) -> None:
    left = img.crop((0, 0, 1, img.height))
    img.paste(left, (img.width - 1, 0))


def _room_dims_m(room: Room) -> tuple[float, float]:
    minx, miny, maxx, maxy = Polygon(room.polygon).bounds
    return (maxx - minx, maxy - miny)


def _color_from_id(room_id: str) -> tuple[int, int, int]:
    h = hashlib.md5(room_id.encode()).digest()
    hue = h[0] / 255.0
    sat = 0.28 + (h[1] / 255.0) * 0.12
    val = 0.55 + (h[2] / 255.0) * 0.10
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return (int(r * 255), int(g * 255), int(b * 255))


def _centered_text(draw, xy, text, font, fill) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((xy[0] - tw // 2, xy[1] - th // 2), text, font=font, fill=fill)


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()

from __future__ import annotations

import colorsys
import hashlib
import math
from pathlib import Path

from PIL import Image, ImageDraw

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

    _draw_compass_ticks(draw, w, h, room)
    _draw_openings(draw, w, h, room)
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


def _draw_compass_ticks(draw, w, h, room) -> None:
    """Tiny tick marks at N/E/S/W bearings on the wall band — no letters.

    Letters get painted onto walls by image-edit models that try to "preserve"
    the reference. Ticks alone give the renderer enough orientation cues
    without polluting the surfaces.
    """
    cam_yaw = room.camera.yaw_deg
    floor_top = int(h * 2 / 3)
    ceiling_bot = int(h / 3)
    for bearing in (0.0, 90.0, 180.0, 270.0):
        yaw = wrap_180(bearing - cam_yaw)
        col = _yaw_to_column(yaw, w)
        # 6px notch on the cornice and a matching one on the skirting
        draw.rectangle([col - 3, ceiling_bot - 6, col + 3, ceiling_bot], fill=(0, 0, 0))
        draw.rectangle([col - 3, floor_top, col + 3, floor_top + 6], fill=(0, 0, 0))


def _draw_openings(draw, w, h, room: Room) -> None:
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
        elif opening.type == "stairs":
            top_pitch = math.degrees(math.atan2(2.1 - cam.z, horiz))
            bot_pitch = math.degrees(math.atan2(0.0 - cam.z, horiz))
            color = (200, 180, 255)
        else:
            window_center_z = 1.2
            window_half_h = (opening.height_m or 1.2) / 2.0
            top_pitch = math.degrees(math.atan2(window_center_z + window_half_h - cam.z, horiz))
            bot_pitch = math.degrees(math.atan2(window_center_z - window_half_h - cam.z, horiz))
            color = (180, 220, 255)

        col_left = _yaw_to_column(yaw - half_width_deg, w)
        col_right = _yaw_to_column(yaw + half_width_deg, w)
        row_top = _pitch_to_row(top_pitch, h)
        row_bot = _pitch_to_row(bot_pitch, h)
        if col_left < col_right:
            draw.rectangle([col_left, row_top, col_right, row_bot], outline=color, width=4)
        else:
            draw.rectangle([col_left, row_top, w, row_bot], outline=color, width=4)
            draw.rectangle([0, row_top, col_right, row_bot], outline=color, width=4)


def _draw_seam(img: Image.Image) -> None:
    left = img.crop((0, 0, 1, img.height))
    img.paste(left, (img.width - 1, 0))


def _color_from_id(room_id: str) -> tuple[int, int, int]:
    h = hashlib.md5(room_id.encode()).digest()
    hue = h[0] / 255.0
    sat = 0.28 + (h[1] / 255.0) * 0.12
    val = 0.55 + (h[2] / 255.0) * 0.10
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return (int(r * 255), int(g * 255), int(b * 255))

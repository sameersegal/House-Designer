from __future__ import annotations

import math
from typing import Callable, Optional

from shapely.geometry import Polygon

from goa_house.state import CONNECTING_OPENINGS, House, Opening, Room

DEFAULT_PANO_URL = lambda rid: f"/panos/{rid}.jpg"
DOOR_CENTER_Z_M = 1.0
STAIRS_UP_TARGET_Z_M = 1.5  # mid-flight when looking up an ascending stair
STAIRS_DOWN_TARGET_Z_M = 0.0  # floor-level void when looking down


def build_tour(
    house: House,
    panorama_url: Callable[[str], str] = DEFAULT_PANO_URL,
) -> dict:
    if not house.rooms:
        return {"default": {"firstScene": None, "autoLoad": False, "compass": True}, "scenes": {}}

    scenes: dict[str, dict] = {}
    for room in house.rooms:
        hotspots = []
        for opening in room.openings:
            if opening.type not in CONNECTING_OPENINGS or not opening.to_room:
                continue
            target = house.room_by_id(opening.to_room)
            if target is None:
                continue
            if opening.type == "stairs":
                direction_up = target.floor > room.floor
                target_z = STAIRS_UP_TARGET_Z_M if direction_up else STAIRS_DOWN_TARGET_Z_M
            else:
                target_z = DOOR_CENTER_Z_M
            yaw, pitch = hotspot_angles(room, opening, target_z=target_z)
            hotspot: dict = {
                "pitch": round(pitch, 2),
                "yaw": round(yaw, 2),
                "type": "scene",
                "sceneId": target.id,
            }
            if opening.type == "stairs":
                direction = "up" if target.floor > room.floor else "down"
                hotspot["text"] = f"Go {direction} to {target.name}"
                hotspot["cssClass"] = f"goa-stairs goa-stairs-{direction}"
            else:
                hotspot["text"] = f"Go to {target.name}"
            hotspots.append(hotspot)
        scenes[room.id] = {
            "title": room.name,
            "type": "equirectangular",
            "panorama": panorama_url(room.id),
            "hfov": 110,
            "pitch": 0,
            "yaw": 0,
            "northOffset": round(_north_offset_deg(house, room), 2),
            "hotSpots": hotspots,
        }

    return {
        "default": {
            "firstScene": house.rooms[0].id,
            "autoLoad": True,
            "compass": True,
            "sceneFadeDuration": 600,
            "showControls": True,
        },
        "scenes": scenes,
    }


def hotspot_angles(
    room: Room,
    opening: Opening,
    target_z: float = DOOR_CENTER_Z_M,
) -> tuple[float, float]:
    cx, cy = opening_center(room, opening)
    cam = room.camera
    dx, dy = cx - cam.x, cy - cam.y
    bearing_deg = math.degrees(math.atan2(dx, dy))
    yaw = wrap_180(bearing_deg - cam.yaw_deg)
    horiz = math.hypot(dx, dy)
    pitch = math.degrees(math.atan2(target_z - cam.z, horiz)) if horiz > 0 else 0.0
    return yaw, pitch


def opening_center(room: Room, opening: Opening) -> tuple[float, float]:
    minx, miny, maxx, maxy = Polygon(room.polygon).bounds
    mid = opening.position_m + opening.width_m / 2.0
    if opening.wall == "N":
        return (minx + mid, maxy)
    if opening.wall == "S":
        return (minx + mid, miny)
    if opening.wall == "E":
        return (maxx, miny + mid)
    return (minx, miny + mid)


def _north_offset_deg(house: House, room: Room) -> float:
    return wrap_180(room.camera.yaw_deg - house.plot.north_deg)


def wrap_180(deg: float) -> float:
    x = (deg + 180.0) % 360.0 - 180.0
    return -180.0 if x == 180.0 else x

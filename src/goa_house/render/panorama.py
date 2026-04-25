from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from goa_house.agents.prompt_builder import build_panorama_prompt
from goa_house.render.placeholder import render_placeholder_pano
from goa_house.state import House, Requirement, Room

DEFAULT_MODEL = os.getenv("GOA_HOUSE_IMAGE_MODEL", "gpt-image-2")
DEFAULT_QUALITY = os.getenv("GOA_HOUSE_IMAGE_QUALITY", "low")
DEFAULT_SIZE = os.getenv("GOA_HOUSE_IMAGE_SIZE", "2048x1024")
DEFAULT_INPUT_FIDELITY = os.getenv("GOA_HOUSE_IMAGE_FIDELITY")  # gpt-image-2 rejects this; leave unset by default


class ImageGenError(RuntimeError):
    pass


def render_panorama(
    house: House,
    room: Room,
    out_path: Path,
    requirements: Optional[list[Requirement]] = None,
    *,
    model: str = DEFAULT_MODEL,
    quality: str = DEFAULT_QUALITY,
    size: str = DEFAULT_SIZE,
    input_fidelity: Optional[str] = DEFAULT_INPUT_FIDELITY,
    force: bool = False,
    log_dir: Optional[Path] = None,
    client: Any = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = _parse_size(size)

    massing_path = out_path.with_name(f"{room.id}.massing.png")
    render_placeholder_pano(house, room, massing_path, size=(width, height))

    prompt = build_panorama_prompt(house, room, requirements, output_size=(width, height))
    massing_bytes = massing_path.read_bytes()
    cache_key = _cache_key(prompt, massing_bytes, model, quality, size, input_fidelity or "")

    hash_path = out_path.with_suffix(".hash")
    if (
        not force
        and out_path.exists()
        and hash_path.exists()
        and hash_path.read_text(encoding="utf-8").strip() == cache_key
    ):
        return out_path

    client = client or _default_client()
    start = time.time()
    edit_kwargs: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
    }
    if input_fidelity:
        edit_kwargs["input_fidelity"] = input_fidelity
    try:
        with massing_path.open("rb") as f:
            result = client.images.edit(image=f, **edit_kwargs)
    except Exception as exc:
        _log_call(
            log_dir,
            {
                "ts": _now_iso(),
                "room_id": room.id,
                "ok": False,
                "elapsed_s": round(time.time() - start, 2),
                "model": model,
                "quality": quality,
                "size": size,
                "error": repr(exc),
            },
        )
        raise ImageGenError(f"image generation failed for {room.id}: {exc}") from exc

    b64 = _extract_b64(result)
    if not b64:
        raise ImageGenError(f"image generation returned no b64 payload for {room.id}")
    img = Image.open(BytesIO(base64.b64decode(b64))).convert("RGB")
    img.save(out_path, "JPEG", quality=88)
    hash_path.write_text(cache_key, encoding="utf-8")

    _log_call(
        log_dir,
        {
            "ts": _now_iso(),
            "room_id": room.id,
            "ok": True,
            "elapsed_s": round(time.time() - start, 2),
            "model": model,
            "quality": quality,
            "size": size,
            "out_path": str(out_path),
            "usage": _usage_dict(result),
        },
    )
    return out_path


def render_all_panoramas(
    house: House,
    out_dir: Path,
    requirements: Optional[list[Requirement]] = None,
    **kwargs: Any,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        render_panorama(house, r, out_dir / f"{r.id}.jpg", requirements, **kwargs)
        for r in house.rooms
    ]


def _default_client() -> Any:
    from openai import OpenAI

    return OpenAI()


def _parse_size(size: str) -> tuple[int, int]:
    w, h = size.lower().split("x")
    return int(w), int(h)


def _cache_key(
    prompt: str,
    ref_bytes: bytes,
    model: str,
    quality: str,
    size: str,
    fidelity: str,
) -> str:
    h = hashlib.sha256()
    h.update(prompt.encode("utf-8"))
    h.update(b"\x00")
    h.update(ref_bytes)
    h.update(b"\x00")
    h.update(f"{model}|{quality}|{size}|{fidelity}".encode("utf-8"))
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_call(log_dir: Optional[Path], payload: dict) -> None:
    if log_dir is None:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "image_calls.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def _extract_b64(result: Any) -> Optional[str]:
    data = getattr(result, "data", None) or []
    if not data:
        return None
    first = data[0]
    return getattr(first, "b64_json", None) or (first.get("b64_json") if isinstance(first, dict) else None)


def _usage_dict(result: Any) -> dict:
    usage = getattr(result, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "model_dump"):
        return usage.model_dump()
    if hasattr(usage, "to_dict"):
        return usage.to_dict()
    if isinstance(usage, dict):
        return usage
    return {}

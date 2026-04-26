from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Protocol

from PIL import Image

from goa_house.agents.prompt_builder import STYLE_VERSION, build_room_prompt
from goa_house.state import House, Requirement

PANO_SIZE = (4096, 2048)
DEFAULT_SEAM_THRESHOLD = 25
DEFAULT_PER_SESSION_CAP = 12
DEFAULT_WARN_THRESHOLD = 8
DEFAULT_MODEL = "gpt-image-2"

LOGGER = logging.getLogger("goa_house.panorama")

_VERSION_RE = re.compile(r"\.v(\d+)\.jpg$")


class PanoramaClient(Protocol):
    def generate(self, prompt: str, reference_image_bytes: bytes) -> bytes: ...


class CostCapExceeded(RuntimeError):
    pass


@dataclass
class RenderSession:
    cap: int = DEFAULT_PER_SESSION_CAP
    warn_threshold: int = DEFAULT_WARN_THRESHOLD
    count: int = 0

    def attempt(self) -> None:
        if self.count >= self.cap:
            raise CostCapExceeded(
                f"per-session render cap reached ({self.count}/{self.cap})"
            )
        self.count += 1
        if self.count == self.warn_threshold:
            LOGGER.warning(
                "panorama render count %d approaching cap %d",
                self.count,
                self.cap,
            )


@dataclass
class RenderResult:
    room_id: str
    output_path: Optional[Path]
    versioned_path: Optional[Path]
    version: int
    skipped: bool
    input_hash: str
    issues: list[str] = field(default_factory=list)


def compute_input_hash(
    prompt: str,
    reference_image_bytes: bytes,
    style_version: int = STYLE_VERSION,
) -> str:
    h = hashlib.sha256()
    h.update(f"style_version={style_version}\n".encode("utf-8"))
    h.update(b"prompt:\n")
    h.update(prompt.encode("utf-8"))
    h.update(b"\nref_sha256:")
    h.update(hashlib.sha256(reference_image_bytes).digest())
    return h.hexdigest()


def load_manifest(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, version: int, input_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": version, "input_hash": input_hash}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def validate_panorama(
    image_path: Path,
    *,
    seam_threshold: int = DEFAULT_SEAM_THRESHOLD,
) -> list[str]:
    issues: list[str] = []
    with Image.open(image_path) as img:
        w, h = img.size
        if w != 2 * h:
            issues.append(f"aspect_ratio: expected 2:1, got {w}x{h}")
        rgb = img.convert("RGB")
        left = rgb.crop((0, 0, 1, h)).tobytes()
        right = rgb.crop((w - 1, 0, w, h)).tobytes()
    diff_total = sum(abs(a - b) for a, b in zip(left, right))
    sample_count = max(h, 1)
    mean_diff = diff_total / (sample_count * 3)
    if mean_diff > seam_threshold:
        issues.append(
            f"seam_discontinuity: mean per-channel pixel diff {mean_diff:.2f} > {seam_threshold}"
        )
    return issues


def render_room_panorama(
    house: House,
    room_id: str,
    massing_image_path: Path,
    panos_dir: Path,
    *,
    requirements: Optional[Iterable[Requirement]] = None,
    client: Optional[PanoramaClient] = None,
    session: Optional[RenderSession] = None,
    log_dir: Optional[Path] = None,
    seam_threshold: int = DEFAULT_SEAM_THRESHOLD,
) -> RenderResult:
    room = house.room_by_id(room_id)
    if room is None:
        raise ValueError(f"unknown room: {room_id}")
    if not massing_image_path.exists():
        raise FileNotFoundError(f"massing image not found: {massing_image_path}")

    prompt = build_room_prompt(house, room_id, requirements or [])
    ref_bytes = massing_image_path.read_bytes()
    input_hash = compute_input_hash(prompt, ref_bytes)

    panos_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = panos_dir / f"{room_id}.manifest.json"
    latest_path = panos_dir / f"{room_id}.jpg"

    existing = load_manifest(manifest_path)
    if existing and existing.get("input_hash") == input_hash and latest_path.exists():
        version = int(existing["version"])
        return RenderResult(
            room_id=room_id,
            output_path=latest_path,
            versioned_path=panos_dir / f"{room_id}.v{version}.jpg",
            version=version,
            skipped=True,
            input_hash=input_hash,
        )

    session = session or RenderSession()
    session.attempt()

    if client is None:
        client = OpenAIImagePanoramaClient()
    image_bytes = client.generate(prompt, ref_bytes)

    manifest_version = int(existing["version"]) if existing else 0
    next_version = _next_version(panos_dir, room_id, manifest_version)
    versioned_path = panos_dir / f"{room_id}.v{next_version}.jpg"
    versioned_path.write_bytes(image_bytes)

    issues = validate_panorama(versioned_path, seam_threshold=seam_threshold)

    if log_dir is not None:
        _log_call(
            log_dir,
            room_id=room_id,
            prompt=prompt,
            input_hash=input_hash,
            image_byte_count=len(image_bytes),
            issues=issues,
            version=next_version,
        )

    if issues:
        return RenderResult(
            room_id=room_id,
            output_path=None,
            versioned_path=versioned_path,
            version=next_version,
            skipped=False,
            input_hash=input_hash,
            issues=issues,
        )

    shutil.copyfile(versioned_path, latest_path)
    save_manifest(manifest_path, next_version, input_hash)
    return RenderResult(
        room_id=room_id,
        output_path=latest_path,
        versioned_path=versioned_path,
        version=next_version,
        skipped=False,
        input_hash=input_hash,
    )


def _next_version(panos_dir: Path, room_id: str, manifest_version: int) -> int:
    nums = [manifest_version]
    for p in panos_dir.glob(f"{room_id}.v*.jpg"):
        m = _VERSION_RE.search(p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1


def _log_call(
    log_dir: Path,
    *,
    room_id: str,
    prompt: str,
    input_hash: str,
    image_byte_count: int,
    issues: list[str],
    version: int,
) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "ts": ts,
        "room_id": room_id,
        "version": version,
        "model": DEFAULT_MODEL,
        "input_hash": input_hash,
        "prompt": prompt,
        "image_bytes": image_byte_count,
        "issues": issues,
    }
    out = log_dir / f"{ts}_{room_id}_{input_hash[:8]}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


class OpenAIImagePanoramaClient:
    def __init__(self, model: str = DEFAULT_MODEL, size: str = "4096x2048"):
        self.model = model
        self.size = size

    def generate(self, prompt: str, reference_image_bytes: bytes) -> bytes:
        import base64

        from openai import OpenAI

        client = OpenAI()
        buf = io.BytesIO(reference_image_bytes)
        buf.name = "massing.png"
        result = client.images.edit(
            model=self.model,
            image=buf,
            prompt=prompt,
            size=self.size,
        )
        b64 = result.data[0].b64_json
        return base64.b64decode(b64)

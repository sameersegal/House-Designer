"""Approval / rejection flow for RequirementDiff lists.

`approve_diffs` validates a candidate projection, mutates `house.json`,
appends approved Requirement records, marks superseded prior requirements,
and re-renders artifacts for affected rooms. `reject_diffs` only appends
rejected Requirement records — never touches `house.json`.

Both helpers operate on a single design directory (`designs/<name>/`) and
are deliberately non-async so the FastAPI endpoint can call them inline;
selective re-render uses the cheap placeholder pano + matplotlib top-down
helpers, NOT gpt-image-2 (which the user invokes separately via the CLI).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from goa_house.diffs import (
    DiffApplyError,
    RequirementDiff,
    affected_room_ids,
    apply_diffs,
)
from goa_house.render.massing import render_topdown
from goa_house.render.placeholder import render_placeholder_pano
from goa_house.state import (
    House,
    Requirement,
    ValidationIssue,
    append_requirement,
    load_house,
    load_requirements,
    save_house,
    utcnow_iso,
    validate_house,
)


class ApprovalError(ValueError):
    """Raised when the projected state has hard validation issues."""

    def __init__(self, issues: list[ValidationIssue]):
        super().__init__("approval blocked by hard validation issues")
        self.issues = issues


def approve_diffs(
    diffs: list[RequirementDiff],
    user_prompt: str,
    design_dir: Path,
) -> dict:
    """Apply `diffs` to `<design_dir>/house.json` and append approved Requirements.

    Raises:
        ApprovalError: projection has hard `ValidationIssue`s.
        DiffApplyError: a mutation references a missing room or duplicates an id.

    On success: writes `house.json` + a `house.vN.json` snapshot, appends to
    `requirements.jsonl`, marks any superseded prior requirements, and
    re-renders the placeholder pano + top-down PNG for each affected room.
    """
    design_dir = Path(design_dir)
    house_path = design_dir / "house.json"
    requirements_path = design_dir / "requirements.jsonl"
    panos_dir = design_dir / "panos"
    massing_dir = design_dir / "massing"

    house = load_house(house_path)

    if not diffs:
        return {"applied": [], "superseded": [], "affected_rooms": [], "snapshot": None}

    projected = apply_diffs(house, diffs)
    issues = validate_house(projected)
    hard = [i for i in issues if i.severity == "hard"]
    if hard:
        raise ApprovalError(hard)

    snapshot_path = save_house(projected, house_path)

    existing_reqs = load_requirements(requirements_path)
    applied_ids: list[str] = []
    superseded_ids: list[str] = []
    for diff in diffs:
        req_id = Requirement.next_id(existing_reqs)
        supersedes = diff.conflicts_with[0] if diff.conflicts_with else None
        req = Requirement(
            id=req_id,
            ts=utcnow_iso(),
            scope=diff.proposed.scope,
            type=diff.proposed.type,
            statement=diff.proposed.statement,
            source_prompt=user_prompt,
            status="approved",
            supersedes=supersedes,
            conflicts_with=list(diff.conflicts_with),
        )
        append_requirement(req, requirements_path)
        existing_reqs.append(req)
        applied_ids.append(req_id)
        if supersedes:
            _mark_superseded(requirements_path, supersedes)
            superseded_ids.append(supersedes)

    affected = affected_room_ids(diffs)
    _rerender_affected(projected, affected, panos_dir, massing_dir)

    return {
        "applied": applied_ids,
        "superseded": superseded_ids,
        "affected_rooms": sorted(affected),
        "snapshot": snapshot_path.name,
    }


def reject_diffs(
    diffs: list[RequirementDiff],
    user_prompt: str,
    reason: Optional[str],
    design_dir: Path,
) -> dict:
    """Append rejected Requirement records; never touches `house.json`."""
    design_dir = Path(design_dir)
    requirements_path = design_dir / "requirements.jsonl"
    existing_reqs = load_requirements(requirements_path)

    rejected_ids: list[str] = []
    for diff in diffs:
        req_id = Requirement.next_id(existing_reqs)
        req = Requirement(
            id=req_id,
            ts=utcnow_iso(),
            scope=diff.proposed.scope,
            type=diff.proposed.type,
            statement=diff.proposed.statement,
            source_prompt=user_prompt,
            status="rejected",
            rejection_reason=reason,
            conflicts_with=list(diff.conflicts_with),
        )
        append_requirement(req, requirements_path)
        existing_reqs.append(req)
        rejected_ids.append(req_id)
    return {"rejected": rejected_ids}


def _mark_superseded(path: Path, target_id: str) -> None:
    """Rewrite `requirements.jsonl` flipping `target_id` to status='superseded'.

    Pure append-only logging would record a separate supersession event, but
    in-place rewrite keeps the file as a flat, deduplicated source-of-truth
    that the upcoming `goa-house replay` command can consume without folding
    status-change records.
    """
    reqs = load_requirements(path)
    if not reqs:
        return
    out_lines: list[str] = []
    for r in reqs:
        if r.id == target_id and r.status == "approved":
            r = r.model_copy(update={"status": "superseded"})
        out_lines.append(r.model_dump_json())
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def _rerender_affected(
    house: House,
    affected_ids: set[str],
    panos_dir: Path,
    massing_dir: Path,
) -> None:
    if not affected_ids:
        return
    panos_dir.mkdir(parents=True, exist_ok=True)
    massing_dir.mkdir(parents=True, exist_ok=True)
    affected_rooms = [r for r in house.rooms if r.id in affected_ids]

    for room in affected_rooms:
        render_placeholder_pano(house, room, panos_dir / f"{room.id}.jpg")

    floors = sorted({r.floor for r in house.rooms}) or [0]
    render_topdown(house, massing_dir / "topdown.png", floor=floors[0])
    if len(floors) > 1:
        for f in floors:
            render_topdown(house, massing_dir / f"topdown-floor{f}.png", floor=f)
    for room in affected_rooms:
        render_topdown(
            house,
            massing_dir / room.id / "topdown.png",
            highlight_room_id=room.id,
            floor=room.floor,
        )


__all__ = [
    "ApprovalError",
    "DiffApplyError",
    "approve_diffs",
    "reject_diffs",
]

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from goa_house.agents.prompt_builder import build_room_prompt
from goa_house.render.massing import render_first_person_block, render_topdown
from goa_house.render.panorama import (
    DEFAULT_PER_SESSION_CAP,
    DEFAULT_SEAM_THRESHOLD,
    RenderSession,
    render_room_panorama,
)
from goa_house.render.placeholder import render_all_placeholders
from goa_house.state import (
    House,
    Plot,
    load_house,
    load_requirements,
    save_house,
    validate_house,
)
from goa_house.tour.pannellum import build_tour

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_STATE_DIR = REPO_ROOT / "state"
DEFAULT_PANOS_DIR = DEFAULT_STATE_DIR / "panos"
DEFAULT_MASSING_DIR = DEFAULT_STATE_DIR / "massing"
DEFAULT_LOG_DIR = DEFAULT_STATE_DIR / "logs"
DEFAULT_HOUSE_PATH = DEFAULT_STATE_DIR / "house.json"
DEFAULT_REQUIREMENTS_PATH = DEFAULT_STATE_DIR / "requirements.jsonl"
DEFAULT_SAMPLE_FIXTURE = REPO_ROOT / "fixtures" / "house.sample.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="goa-house")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="Initialize state/house.json from a plot fixture")
    init.add_argument("--plot", type=Path, required=True)
    init.add_argument("--out", type=Path, default=DEFAULT_HOUSE_PATH)

    build = sub.add_parser("build-sample", help="Materialize the sample house, panoramas, top-down and tour.json")
    build.add_argument("--fixture", type=Path, default=DEFAULT_SAMPLE_FIXTURE)
    build.add_argument("--house-out", type=Path, default=DEFAULT_HOUSE_PATH)
    build.add_argument("--panos-dir", type=Path, default=DEFAULT_PANOS_DIR)
    build.add_argument("--massing-dir", type=Path, default=DEFAULT_MASSING_DIR)

    tour = sub.add_parser("build-tour", help="Rebuild panos, massing and tour config from state/house.json")
    tour.add_argument("--house", type=Path, default=DEFAULT_HOUSE_PATH)
    tour.add_argument("--panos-dir", type=Path, default=DEFAULT_PANOS_DIR)
    tour.add_argument("--massing-dir", type=Path, default=DEFAULT_MASSING_DIR)
    tour.add_argument("--no-panos", action="store_true", help="Skip regenerating placeholder panoramas")

    validate = sub.add_parser("validate", help="Validate a house.json file")
    validate.add_argument("--house", type=Path, default=DEFAULT_HOUSE_PATH)

    for name, help_text in (
        ("render-room", "Render an LLM panorama for a single room"),
        ("render-all", "Render LLM panoramas for every room in the house"),
    ):
        rp = sub.add_parser(name, help=help_text)
        if name == "render-room":
            rp.add_argument("room_id")
        rp.add_argument("--house", type=Path, default=DEFAULT_HOUSE_PATH)
        rp.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS_PATH)
        rp.add_argument("--panos-dir", type=Path, default=DEFAULT_PANOS_DIR)
        rp.add_argument("--massing-dir", type=Path, default=DEFAULT_MASSING_DIR)
        rp.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
        rp.add_argument("--cap", type=int, default=DEFAULT_PER_SESSION_CAP)
        rp.add_argument("--seam-threshold", type=int, default=DEFAULT_SEAM_THRESHOLD)
        rp.add_argument(
            "--prompt-only",
            action="store_true",
            help="Build prompt + first-person massing block only; skip the LLM call",
        )

    args = parser.parse_args(argv)

    if args.cmd == "init":
        return _cmd_init(args.plot, args.out)
    if args.cmd == "build-sample":
        return _cmd_build_sample(args.fixture, args.house_out, args.panos_dir, args.massing_dir)
    if args.cmd == "build-tour":
        return _cmd_build_tour(args.house, args.panos_dir, args.massing_dir, skip_panos=args.no_panos)
    if args.cmd == "validate":
        return _cmd_validate(args.house)
    if args.cmd == "render-room":
        return _cmd_render_rooms(
            house_path=args.house,
            room_ids=[args.room_id],
            requirements_path=args.requirements,
            panos_dir=args.panos_dir,
            massing_dir=args.massing_dir,
            log_dir=args.log_dir,
            cap=args.cap,
            seam_threshold=args.seam_threshold,
            prompt_only=args.prompt_only,
        )
    if args.cmd == "render-all":
        return _cmd_render_rooms(
            house_path=args.house,
            room_ids=None,
            requirements_path=args.requirements,
            panos_dir=args.panos_dir,
            massing_dir=args.massing_dir,
            log_dir=args.log_dir,
            cap=args.cap,
            seam_threshold=args.seam_threshold,
            prompt_only=args.prompt_only,
        )
    parser.error(f"unknown command {args.cmd}")
    return 2


def _cmd_init(plot_path: Path, out: Path) -> int:
    plot = Plot.model_validate(json.loads(plot_path.read_text(encoding="utf-8")))
    house = House(plot=plot)
    save_house(house, out)
    print(f"wrote {out}")
    return 0


def _cmd_build_sample(fixture: Path, house_out: Path, panos_dir: Path, massing_dir: Path) -> int:
    house_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture, house_out)
    house = load_house(house_out)
    issues = validate_house(house)
    hard = [i for i in issues if i.severity == "hard"]
    if hard:
        print("fixture failed validation:", file=sys.stderr)
        for i in hard:
            print(f"  [{i.code}] {i.message}", file=sys.stderr)
        return 1
    return _emit_artifacts(house, panos_dir, massing_dir, write_panos=True)


def _cmd_build_tour(house_path: Path, panos_dir: Path, massing_dir: Path, skip_panos: bool) -> int:
    house = load_house(house_path)
    issues = validate_house(house)
    hard = [i for i in issues if i.severity == "hard"]
    if hard:
        print("house failed validation:", file=sys.stderr)
        for i in hard:
            print(f"  [{i.code}] {i.message}", file=sys.stderr)
        return 1
    return _emit_artifacts(house, panos_dir, massing_dir, write_panos=not skip_panos)


def _cmd_validate(house_path: Path) -> int:
    house = load_house(house_path)
    issues = validate_house(house)
    if not issues:
        print("OK")
        return 0
    for i in issues:
        print(f"[{i.severity}] [{i.code}] {i.message}")
    return 1 if any(i.severity == "hard" for i in issues) else 0


def _emit_artifacts(house: House, panos_dir: Path, massing_dir: Path, write_panos: bool) -> int:
    web_dir = REPO_ROOT / "web"
    web_dir.mkdir(parents=True, exist_ok=True)

    if write_panos:
        paths = render_all_placeholders(house, panos_dir)
        print(f"wrote {len(paths)} placeholder panoramas -> {panos_dir}")

    massing_dir.mkdir(parents=True, exist_ok=True)
    top = render_topdown(house, massing_dir / "topdown.png")
    print(f"wrote {top}")
    for room in house.rooms:
        render_topdown(
            house,
            massing_dir / room.id / "topdown.png",
            highlight_room_id=room.id,
        )

    tour = build_tour(house)
    tour_path = web_dir / "tour.json"
    tour_path.write_text(json.dumps(tour, indent=2), encoding="utf-8")
    print(f"wrote {tour_path}")
    return 0


def _cmd_render_rooms(
    *,
    house_path: Path,
    room_ids: list[str] | None,
    requirements_path: Path,
    panos_dir: Path,
    massing_dir: Path,
    log_dir: Path,
    cap: int,
    seam_threshold: int,
    prompt_only: bool,
    client=None,
) -> int:
    house = load_house(house_path)
    issues = validate_house(house)
    hard = [i for i in issues if i.severity == "hard"]
    if hard:
        print("house failed validation:", file=sys.stderr)
        for i in hard:
            print(f"  [{i.code}] {i.message}", file=sys.stderr)
        return 1

    requirements = load_requirements(requirements_path) if requirements_path.exists() else []
    targets = room_ids if room_ids is not None else [r.id for r in house.rooms]
    missing = [rid for rid in targets if house.room_by_id(rid) is None]
    if missing:
        print(f"unknown room(s): {', '.join(missing)}", file=sys.stderr)
        return 2

    session = RenderSession(cap=cap)
    rc = 0
    for rid in targets:
        room = house.room_by_id(rid)
        block_path = massing_dir / rid / "first_person.png"
        render_first_person_block(house, room, block_path)
        prompt = build_room_prompt(house, rid, requirements)
        prompt_path = massing_dir / rid / "prompt.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        print(f"wrote {block_path} and {prompt_path}")

        if prompt_only:
            continue

        try:
            result = render_room_panorama(
                house,
                rid,
                block_path,
                panos_dir,
                requirements=requirements,
                client=client,
                session=session,
                log_dir=log_dir,
                seam_threshold=seam_threshold,
            )
        except Exception as exc:  # surface CostCapExceeded and any client error
            print(f"{rid}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        if result.skipped:
            print(f"{rid}: skipped (hash matches v{result.version})")
        elif result.issues:
            print(
                f"{rid}: rendered v{result.version} but post-validation failed: "
                f"{'; '.join(result.issues)}",
                file=sys.stderr,
            )
            print(f"  versioned file kept at {result.versioned_path}", file=sys.stderr)
            rc = 1
        else:
            print(f"{rid}: wrote {result.output_path} (v{result.version})")

    return rc


if __name__ == "__main__":
    sys.exit(main())

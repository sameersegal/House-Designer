from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from goa_house.render.massing import render_topdown
from goa_house.render.placeholder import render_all_placeholders
from goa_house.state import (
    House,
    Plot,
    Requirement,
    load_house,
    load_requirements,
    save_house,
    validate_house,
)
from goa_house.tour.pannellum import build_tour

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DESIGNS_DIR = REPO_ROOT / "designs"
DEFAULT_DESIGN_NAME = "goa-sample"
DEFAULT_DESIGN_DIR = DEFAULT_DESIGNS_DIR / DEFAULT_DESIGN_NAME
DEFAULT_HOUSE_PATH = DEFAULT_DESIGN_DIR / "house.json"
DEFAULT_PANOS_DIR = DEFAULT_DESIGN_DIR / "panos"
DEFAULT_MASSING_DIR = DEFAULT_DESIGN_DIR / "massing"
DEFAULT_WEB_DIR = REPO_ROOT / "web"
DEFAULT_SITE_OUT = REPO_ROOT / "_site"
DEFAULT_LOGS_DIR = REPO_ROOT / "state" / "logs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="goa-house")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="Initialize a fresh house.json from a plot fixture")
    init.add_argument("--plot", type=Path, required=True)
    init.add_argument("--out", type=Path, default=DEFAULT_HOUSE_PATH)

    tour = sub.add_parser(
        "build-tour",
        help="Rebuild placeholder panos and top-down PNGs for a design's house.json",
    )
    tour.add_argument("--house", type=Path, default=DEFAULT_HOUSE_PATH)
    tour.add_argument("--panos-dir", type=Path, default=DEFAULT_PANOS_DIR)
    tour.add_argument("--massing-dir", type=Path, default=DEFAULT_MASSING_DIR)
    tour.add_argument(
        "--no-panos",
        action="store_true",
        help="Skip placeholder pano regen (use when real panos are already in panos/)",
    )

    validate = sub.add_parser("validate", help="Validate a house.json file")
    validate.add_argument("--house", type=Path, default=DEFAULT_HOUSE_PATH)

    render_room = sub.add_parser(
        "render-room", help="Render a real panorama for one room via gpt-image-2"
    )
    render_room.add_argument("room_id")
    render_room.add_argument("--house", type=Path, default=DEFAULT_HOUSE_PATH)
    render_room.add_argument("--panos-dir", type=Path, default=DEFAULT_PANOS_DIR)
    render_room.add_argument("--quality", default=None, help="low|medium|high (default: low)")
    render_room.add_argument("--size", default=None, help="WxH, e.g. 2048x1024")
    render_room.add_argument("--force", action="store_true", help="ignore cache and re-render")

    render_all = sub.add_parser(
        "render-all", help="Render real panoramas for every room via gpt-image-2"
    )
    render_all.add_argument("--house", type=Path, default=DEFAULT_HOUSE_PATH)
    render_all.add_argument("--panos-dir", type=Path, default=DEFAULT_PANOS_DIR)
    render_all.add_argument("--quality", default=None)
    render_all.add_argument("--size", default=None)
    render_all.add_argument("--force", action="store_true")

    build_site = sub.add_parser(
        "build-site",
        help="Bake every design into a static GitHub Pages bundle (no server needed)",
    )
    build_site.add_argument("--out-dir", type=Path, default=DEFAULT_SITE_OUT)
    build_site.add_argument("--designs-dir", type=Path, default=DEFAULT_DESIGNS_DIR)
    build_site.add_argument("--web-dir", type=Path, default=DEFAULT_WEB_DIR)

    args = parser.parse_args(argv)

    if args.cmd == "init":
        return _cmd_init(args.plot, args.out)
    if args.cmd == "build-tour":
        return _cmd_build_tour(
            args.house, args.panos_dir, args.massing_dir, skip_panos=args.no_panos
        )
    if args.cmd == "validate":
        return _cmd_validate(args.house)
    if args.cmd == "render-room":
        return _cmd_render_room(
            args.house, args.panos_dir, args.room_id, args.quality, args.size, args.force
        )
    if args.cmd == "render-all":
        return _cmd_render_all(
            args.house, args.panos_dir, args.quality, args.size, args.force
        )
    if args.cmd == "build-site":
        return _cmd_build_site(args.out_dir, args.designs_dir, args.web_dir)
    parser.error(f"unknown command {args.cmd}")
    return 2


def _cmd_init(plot_path: Path, out: Path) -> int:
    plot = Plot.model_validate(json.loads(plot_path.read_text(encoding="utf-8")))
    house = House(plot=plot)
    save_house(house, out)
    print(f"wrote {out}")
    return 0


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


def _cmd_render_room(
    house_path: Path,
    panos_dir: Path,
    room_id: str,
    quality: str | None,
    size: str | None,
    force: bool,
) -> int:
    from goa_house.render.panorama import ImageGenError, render_panorama

    house = load_house(house_path)
    room = house.room_by_id(room_id)
    if room is None:
        print(f"unknown room: {room_id}", file=sys.stderr)
        return 1
    if not room.tourable:
        print(f"room is not tourable; skipping: {room_id}", file=sys.stderr)
        return 1
    requirements = _design_requirements(house_path)
    kwargs = _render_kwargs(quality, size, force)
    try:
        out = render_panorama(
            house, room, panos_dir / f"{room.id}.jpg", requirements, **kwargs
        )
    except ImageGenError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"wrote {out}")
    return 0


def _cmd_render_all(
    house_path: Path,
    panos_dir: Path,
    quality: str | None,
    size: str | None,
    force: bool,
) -> int:
    from goa_house.render.panorama import ImageGenError, render_panorama

    house = load_house(house_path)
    tourable = [r for r in house.rooms if r.tourable]
    if not tourable:
        print("no tourable rooms to render", file=sys.stderr)
        return 1
    requirements = _design_requirements(house_path)
    kwargs = _render_kwargs(quality, size, force)
    for room in tourable:
        try:
            out = render_panorama(
                house, room, panos_dir / f"{room.id}.jpg", requirements, **kwargs
            )
        except ImageGenError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"wrote {out}")
    return 0


def _design_requirements(house_path: Path) -> list[Requirement]:
    """Load `<design>/requirements.jsonl` next to the house file. Missing file → []."""
    return load_requirements(house_path.parent / "requirements.jsonl")


def _render_kwargs(quality: str | None, size: str | None, force: bool) -> dict:
    kwargs: dict = {"force": force, "log_dir": DEFAULT_LOGS_DIR}
    if quality:
        kwargs["quality"] = quality
    if size:
        kwargs["size"] = size
    return kwargs


def _emit_artifacts(house: House, panos_dir: Path, massing_dir: Path, write_panos: bool) -> int:
    if write_panos:
        paths = render_all_placeholders(house, panos_dir)
        print(f"wrote {len(paths)} placeholder panoramas -> {panos_dir}")

    massing_dir.mkdir(parents=True, exist_ok=True)
    floors = sorted({r.floor for r in house.rooms}) or [0]

    overview_floor = floors[0]
    overview = render_topdown(house, massing_dir / "topdown.png", floor=overview_floor)
    print(f"wrote {overview}")
    if len(floors) > 1:
        for f in floors:
            out = render_topdown(house, massing_dir / f"topdown-floor{f}.png", floor=f)
            print(f"wrote {out}")

    for room in house.rooms:
        render_topdown(
            house,
            massing_dir / room.id / "topdown.png",
            highlight_room_id=room.id,
            floor=room.floor,
        )
    return 0


def _cmd_build_site(out_dir: Path, designs_dir: Path, web_dir: Path) -> int:
    """Bake every design into a static bundle suitable for GitHub Pages.

    Produces, under ``out_dir``::

        index.html
        static/app.js
        designs.json                                 (the manifest the viewer fetches)
        designs/<name>/house.json
        designs/<name>/tour.json                     (built statically; relative pano URLs)
        designs/<name>/panos/<rid>.jpg
        designs/<name>/massing/...
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    static_dir = out_dir / "static"
    static_dir.mkdir()
    shutil.copy(web_dir / "index.html", out_dir / "index.html")
    for f in web_dir.iterdir():
        if f.is_file() and f.suffix in (".js", ".css") and f.name != "index.html":
            shutil.copy(f, static_dir / f.name)

    designs = sorted(
        p.name
        for p in designs_dir.iterdir()
        if p.is_dir() and (p / "house.json").exists()
    )
    (out_dir / "designs.json").write_text(
        json.dumps({"designs": designs}, indent=2), encoding="utf-8"
    )
    print(f"wrote {out_dir / 'designs.json'} ({len(designs)} designs)")

    ignore_artifacts = shutil.ignore_patterns("*.hash", "*.massing.png")
    for name in designs:
        src = designs_dir / name
        dst = out_dir / "designs" / name
        dst.mkdir(parents=True)

        shutil.copy(src / "house.json", dst / "house.json")

        house = load_house(src / "house.json")
        panos_src = src / "panos"

        def _pano_url(rid: str, _name: str = name, _panos: Path = panos_src) -> str:
            jpg = _panos / f"{rid}.jpg"
            v = int(jpg.stat().st_mtime) if jpg.exists() else 0
            return f"designs/{_name}/panos/{rid}.jpg?v={v}"

        tour = build_tour(house, panorama_url=_pano_url)
        (dst / "tour.json").write_text(json.dumps(tour, indent=2), encoding="utf-8")

        for sub in ("panos", "massing"):
            src_sub = src / sub
            if src_sub.exists():
                shutil.copytree(src_sub, dst / sub, ignore=ignore_artifacts)
        print(f"wrote {dst}/")

    return 0


if __name__ == "__main__":
    sys.exit(main())

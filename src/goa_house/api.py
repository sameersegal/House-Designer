from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from goa_house.state import load_house
from goa_house.tour.pannellum import build_tour

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = REPO_ROOT / "web"
DESIGNS_DIR = REPO_ROOT / "designs"


def create_app(
    web_dir: Path = WEB_DIR,
    designs_dir: Path = DESIGNS_DIR,
) -> FastAPI:
    app = FastAPI(title="Goa House Designer")

    designs_dir.mkdir(parents=True, exist_ok=True)

    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        path = web_dir / "index.html"
        if not path.exists():
            raise HTTPException(status_code=404, detail="index.html not found")
        return FileResponse(path)

    @app.get("/designs.json")
    def list_designs() -> JSONResponse:
        names = sorted(
            p.name for p in designs_dir.iterdir() if p.is_dir() and (p / "house.json").exists()
        )
        return JSONResponse({"designs": names})

    @app.get("/designs/{name}/house.json")
    def design_house(name: str) -> JSONResponse:
        house_path = _design_file(designs_dir, name, "house.json")
        return JSONResponse(json.loads(house_path.read_text(encoding="utf-8")))

    @app.get("/designs/{name}/tour.json")
    def design_tour(name: str) -> JSONResponse:
        house_path = _design_file(designs_dir, name, "house.json")
        house = load_house(house_path)
        panos_dir = house_path.parent / "panos"

        def _pano_url(rid: str) -> str:
            jpg = panos_dir / f"{rid}.jpg"
            v = int(jpg.stat().st_mtime) if jpg.exists() else 0
            return f"designs/{name}/panos/{rid}.jpg?v={v}"

        tour = build_tour(house, panorama_url=_pano_url)
        return JSONResponse(tour)

    @app.get("/designs/{name}/panos/{filename:path}")
    def design_pano(name: str, filename: str) -> FileResponse:
        return FileResponse(_design_file(designs_dir, name, "panos", filename))

    @app.get("/designs/{name}/massing/{filename:path}")
    def design_massing(name: str, filename: str) -> FileResponse:
        return FileResponse(_design_file(designs_dir, name, "massing", filename))

    return app


def _design_file(designs_dir: Path, name: str, *parts: str) -> Path:
    if not _safe_segment(name) or any(not _safe_segment(p) for p in parts[:-1]):
        raise HTTPException(status_code=400, detail="invalid path")
    candidate = (designs_dir / name).joinpath(*parts).resolve()
    root = (designs_dir / name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path traversal") from exc
    if not candidate.exists():
        raise HTTPException(status_code=404, detail=f"{name}/{'/'.join(parts)} not found")
    return candidate


def _safe_segment(s: str) -> bool:
    return bool(s) and "/" not in s and "\\" not in s and s not in (".", "..")


app = create_app()

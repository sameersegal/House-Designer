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
STATE_DIR = REPO_ROOT / "state"
HOUSE_PATH = STATE_DIR / "house.json"
PANOS_DIR = STATE_DIR / "panos"
MASSING_DIR = STATE_DIR / "massing"


def create_app(
    web_dir: Path = WEB_DIR,
    house_path: Path = HOUSE_PATH,
    panos_dir: Path = PANOS_DIR,
    massing_dir: Path = MASSING_DIR,
) -> FastAPI:
    app = FastAPI(title="Goa House Designer")

    panos_dir.mkdir(parents=True, exist_ok=True)
    massing_dir.mkdir(parents=True, exist_ok=True)

    app.mount("/panos", StaticFiles(directory=panos_dir), name="panos")
    app.mount("/massing", StaticFiles(directory=massing_dir), name="massing")
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.get("/")
    def index() -> FileResponse:
        path = web_dir / "index.html"
        if not path.exists():
            raise HTTPException(status_code=404, detail="index.html not found")
        return FileResponse(path)

    @app.get("/tour.json")
    def tour() -> JSONResponse:
        if not house_path.exists():
            raise HTTPException(status_code=404, detail=f"{house_path} not found")
        house = load_house(house_path)
        return JSONResponse(build_tour(house))

    @app.get("/house.json")
    def house() -> JSONResponse:
        if not house_path.exists():
            raise HTTPException(status_code=404, detail=f"{house_path} not found")
        return JSONResponse(json.loads(house_path.read_text(encoding="utf-8")))

    return app


app = create_app()

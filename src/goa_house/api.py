from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from goa_house.agents.extractor import ExtractorError, extract_diffs
from goa_house.approval import ApprovalError, approve_diffs, reject_diffs
from goa_house.diffs import DiffApplyError, RequirementDiff
from goa_house.state import load_house, load_requirements
from goa_house.tour.pannellum import build_tour

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = REPO_ROOT / "web"
DESIGNS_DIR = REPO_ROOT / "designs"


class PromptRequest(BaseModel):
    text: str


class ApproveRequest(BaseModel):
    diffs: list[RequirementDiff]
    user_prompt: str = ""


class RejectRequest(BaseModel):
    diffs: list[RequirementDiff]
    user_prompt: str = ""
    reason: Optional[str] = None


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

    @app.get("/designs")
    def list_designs() -> JSONResponse:
        names = sorted(
            p.name for p in designs_dir.iterdir() if p.is_dir() and (p / "house.json").exists()
        )
        return JSONResponse({"designs": names})

    @app.get("/designs/{name}/house.json")
    def design_house(name: str) -> JSONResponse:
        house_path = _design_file(designs_dir, name, "house.json")
        return JSONResponse(json.loads(house_path.read_text(encoding="utf-8")))

    @app.get("/designs/{name}/requirements.jsonl")
    def design_requirements(name: str) -> JSONResponse:
        design_dir = _design_dir(designs_dir, name)
        reqs = load_requirements(design_dir / "requirements.jsonl")
        return JSONResponse({"requirements": [r.model_dump(mode="json") for r in reqs]})

    @app.get("/designs/{name}/tour.json")
    def design_tour(name: str) -> JSONResponse:
        house_path = _design_file(designs_dir, name, "house.json")
        house = load_house(house_path)
        panos_dir = house_path.parent / "panos"

        def _pano_url(rid: str) -> str:
            jpg = panos_dir / f"{rid}.jpg"
            v = int(jpg.stat().st_mtime) if jpg.exists() else 0
            return f"/designs/{name}/panos/{rid}.jpg?v={v}"

        tour = build_tour(house, panorama_url=_pano_url)
        return JSONResponse(tour)

    @app.get("/designs/{name}/panos/{filename:path}")
    def design_pano(name: str, filename: str) -> FileResponse:
        return FileResponse(_design_file(designs_dir, name, "panos", filename))

    @app.get("/designs/{name}/massing/{filename:path}")
    def design_massing(name: str, filename: str) -> FileResponse:
        return FileResponse(_design_file(designs_dir, name, "massing", filename))

    @app.post("/designs/{name}/prompt")
    async def design_prompt(name: str, body: PromptRequest) -> JSONResponse:
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        design_dir = _design_dir(designs_dir, name)
        house = load_house(design_dir / "house.json")
        recent = load_requirements(design_dir / "requirements.jsonl")
        try:
            result = await extract_diffs(text, house, recent)
        except ExtractorError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return JSONResponse(result.model_dump(mode="json"))

    @app.post("/designs/{name}/requirements/approve")
    def design_approve(name: str, body: ApproveRequest) -> JSONResponse:
        design_dir = _design_dir(designs_dir, name)
        try:
            result = approve_diffs(body.diffs, body.user_prompt, design_dir)
        except ApprovalError as exc:
            return JSONResponse(
                {
                    "status": "blocked",
                    "issues": [i.model_dump(mode="json") for i in exc.issues],
                },
                status_code=409,
            )
        except DiffApplyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"status": "ok", **result})

    @app.post("/designs/{name}/requirements/reject")
    def design_reject(name: str, body: RejectRequest) -> JSONResponse:
        design_dir = _design_dir(designs_dir, name)
        result = reject_diffs(body.diffs, body.user_prompt, body.reason, design_dir)
        return JSONResponse({"status": "ok", **result})

    return app


def _design_dir(designs_dir: Path, name: str) -> Path:
    if not _safe_segment(name):
        raise HTTPException(status_code=400, detail="invalid design name")
    candidate = (designs_dir / name).resolve()
    try:
        candidate.relative_to(designs_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path traversal") from exc
    if not (candidate / "house.json").exists():
        raise HTTPException(status_code=404, detail=f"design {name} not found")
    return candidate


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

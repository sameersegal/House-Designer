from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from goa_house.agents.extractor import extract_diffs_stream
from goa_house.agents.sessions import (
    clear_session,
    get_session_id,
    new_session_id,
    save_session_id,
)
from goa_house.approval import ApprovalError, approve_diffs, reject_diffs
from goa_house.diffs import DiffApplyError, RequirementDiff
from goa_house.render.panorama import render_panorama
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


class RenderRequest(BaseModel):
    room_ids: list[str]
    force: bool = True


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
    async def design_prompt(name: str, body: PromptRequest) -> StreamingResponse:
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        design_dir = _design_dir(designs_dir, name)
        house = load_house(design_dir / "house.json")
        recent = load_requirements(design_dir / "requirements.jsonl")

        # Resume an existing per-design session, or mint a fresh one. Either
        # way, the saved id is what the next /prompt call will resume from.
        existing = get_session_id(design_dir)
        if existing:
            session_id_arg: Optional[str] = None
            resume_arg: Optional[str] = existing
        else:
            session_id_arg = new_session_id()
            save_session_id(design_dir, session_id_arg)
            resume_arg = None

        async def event_stream():
            yield _sse({"type": "session", "session_id": session_id_arg or existing})
            try:
                async for event in extract_diffs_stream(
                    text,
                    house,
                    recent,
                    session_id=session_id_arg,
                    resume=resume_arg,
                ):
                    yield _sse(event)
            except Exception as exc:  # noqa: BLE001
                yield _sse({"type": "error", "message": str(exc)})

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/designs/{name}/sessions/clear")
    def design_sessions_clear(name: str) -> JSONResponse:
        design_dir = _design_dir(designs_dir, name)
        cleared = clear_session(design_dir)
        return JSONResponse({"status": "ok", "cleared_session_id": cleared})

    @app.get("/designs/{name}/sessions")
    def design_sessions_get(name: str) -> JSONResponse:
        design_dir = _design_dir(designs_dir, name)
        return JSONResponse({"session_id": get_session_id(design_dir)})

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

    @app.post("/designs/{name}/render")
    async def design_render(name: str, body: RenderRequest) -> StreamingResponse:
        design_dir = _design_dir(designs_dir, name)
        house = load_house(design_dir / "house.json")
        requirements = load_requirements(design_dir / "requirements.jsonl")
        panos_dir = design_dir / "panos"
        log_dir = REPO_ROOT / "state" / "logs"

        rooms_by_id = {r.id: r for r in house.rooms}
        room_ids = [
            rid for rid in body.room_ids
            if rid in rooms_by_id and rooms_by_id[rid].tourable is not False
        ]

        async def event_stream():
            for rid in room_ids:
                yield _sse({"type": "rendering", "room_id": rid, "label": f"Rendering {rid}\u2026"})
                try:
                    room = rooms_by_id[rid]
                    out_path = panos_dir / f"{rid}.jpg"
                    await asyncio.to_thread(
                        render_panorama,
                        house,
                        room,
                        out_path,
                        requirements,
                        force=body.force,
                        log_dir=log_dir,
                    )
                    yield _sse({"type": "room_done", "room_id": rid})
                except Exception as exc:  # noqa: BLE001
                    yield _sse({"type": "room_error", "room_id": rid, "message": str(exc)})
            yield _sse({"type": "done"})

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app


def _sse(event: dict) -> str:
    """Format a dict as a Server-Sent Events `data:` frame."""
    return f"data: {json.dumps(event)}\n\n"


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

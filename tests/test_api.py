from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goa_house.api import create_app
from goa_house.diffs import (
    AddOpeningMutation,
    AddRoomMutation,
    ProposedRequirement,
    RequirementDiff,
)
from goa_house.state import (
    Camera,
    Opening,
    Room,
)


def _read_sse(text: str) -> list[dict]:
    """Parse an SSE response body into event dicts."""
    events = []
    for frame in text.split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data:"):
            events.append(json.loads(frame[len("data:"):].strip()))
    return events

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_HOUSE = REPO_ROOT / "designs" / "goa-sample" / "house.json"
SAMPLE_PANOS = REPO_ROOT / "designs" / "goa-sample" / "panos"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    designs_dir = tmp_path / "designs"
    sample = designs_dir / "goa-sample"
    sample.mkdir(parents=True)
    shutil.copyfile(SAMPLE_HOUSE, sample / "house.json")
    panos_dir = sample / "panos"
    panos_dir.mkdir()
    for jpg in SAMPLE_PANOS.glob("*.jpg"):
        shutil.copyfile(jpg, panos_dir / jpg.name)
    massing_dir = sample / "massing" / "living_room"
    massing_dir.mkdir(parents=True)
    (massing_dir / "topdown.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal stub
    web_dir = REPO_ROOT / "web"
    return TestClient(create_app(web_dir=web_dir, designs_dir=designs_dir))


def test_list_designs(client: TestClient):
    r = client.get("/designs")
    assert r.status_code == 200
    assert r.json() == {"designs": ["goa-sample"]}


def test_house_json(client: TestClient):
    r = client.get("/designs/goa-sample/house.json")
    assert r.status_code == 200
    body = r.json()
    assert body["plot"]["setbacks"]["front"] == 3.0
    assert {room["id"] for room in body["rooms"]} == {"living_room", "kitchen", "master_bedroom"}


def test_tour_json_uses_design_scoped_pano_urls(client: TestClient):
    r = client.get("/designs/goa-sample/tour.json")
    assert r.status_code == 200
    tour = r.json()
    assert tour["default"]["firstScene"] == "living_room"
    for scene_id, scene in tour["scenes"].items():
        assert scene["panorama"].startswith(f"/designs/goa-sample/panos/{scene_id}.jpg")


def test_static_pano_served(client: TestClient):
    r = client.get("/designs/goa-sample/panos/living_room.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 1000


def test_static_massing_served(client: TestClient):
    r = client.get("/designs/goa-sample/massing/living_room/topdown.png")
    assert r.status_code == 200
    assert r.content.startswith(b"\x89PNG")


def test_missing_design_returns_404(client: TestClient):
    r = client.get("/designs/no-such-design/house.json")
    assert r.status_code == 404


def test_path_traversal_rejected(client: TestClient):
    r = client.get("/designs/..%2Fpwn/house.json")
    assert r.status_code in (400, 404)
    r = client.get("/designs/goa-sample/panos/..%2F..%2Fhouse.json")
    assert r.status_code in (400, 404)


def test_index_served(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "Goa House Designer" in r.text
    assert "design-select" in r.text
    # Chat UI scaffolding
    assert "chat-log" in r.text
    assert "prompt-input" in r.text
    assert "prompt-send" in r.text
    assert "new-chat-btn" in r.text
    assert "reqs-log" in r.text


def test_static_app_js_includes_chat_flow(client: TestClient):
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "submitPrompt" in r.text
    assert "submitDecision" in r.text
    assert "renderDiffs" in r.text
    assert "readSSE" in r.text
    assert "newChat" in r.text
    assert "/requirements/approve" in r.text
    assert "/requirements/reject" in r.text
    assert "/sessions/clear" in r.text


def test_empty_designs_dir(tmp_path: Path):
    web_dir = REPO_ROOT / "web"
    designs_dir = tmp_path / "designs"
    designs_dir.mkdir()
    client = TestClient(create_app(web_dir=web_dir, designs_dir=designs_dir))
    assert client.get("/designs").json() == {"designs": []}


def test_real_designs_dir_lists_committed_designs():
    designs_dir = REPO_ROOT / "designs"
    web_dir = REPO_ROOT / "web"
    client = TestClient(create_app(web_dir=web_dir, designs_dir=designs_dir))
    listed = client.get("/designs").json()["designs"]
    assert "goa-sample" in listed
    assert "goa-two-floor" in listed

    tour = client.get("/designs/goa-two-floor/tour.json").json()
    assert "stairwell_g" in tour["scenes"]
    assert "landing" in tour["scenes"]
    stairs_hotspot = next(
        hs
        for hs in tour["scenes"]["stairwell_g"]["hotSpots"]
        if "cssClass" in hs and "goa-stairs" in hs["cssClass"]
    )
    assert stairs_hotspot["sceneId"] == "landing"


# ---- Prompt / approval / reject endpoints --------------------------------


def _new_bedroom() -> Room:
    return Room(
        id="new_bedroom",
        name="New Bedroom",
        polygon=[(2, 9), (8, 9), (8, 12), (2, 12)],
        openings=[
            Opening(type="window", wall="N", position_m=1.0, width_m=1.2),
            Opening(type="door", wall="S", position_m=2.0, width_m=0.9, to_room="living_room"),
        ],
        camera=Camera(x=5.0, y=10.5, z=1.6, yaw_deg=0.0),
    )


def _add_bedroom_diffs() -> list[RequirementDiff]:
    return [
        RequirementDiff(
            proposed=ProposedRequirement(
                scope="new_bedroom",
                type="feature",
                statement="Add a bedroom on the north side.",
            ),
            affected_rooms=["new_bedroom"],
            source_span="bedroom on the north side",
            mutation=AddRoomMutation(room=_new_bedroom()),
        ),
        RequirementDiff(
            proposed=ProposedRequirement(
                scope="living_room",
                type="adjacency",
                statement="Living room connects to the new bedroom.",
            ),
            affected_rooms=["living_room"],
            source_span="bedroom",
            mutation=AddOpeningMutation(
                room_id="living_room",
                opening=Opening(
                    type="door", wall="N", position_m=4.0, width_m=0.9, to_room="new_bedroom"
                ),
            ),
        ),
    ]


def _diffs_event(diffs: list[RequirementDiff]) -> dict:
    return {
        "type": "result",
        "extractor_result": {
            "kind": "diffs",
            "diffs": [d.model_dump(mode="json") for d in diffs],
            "question": None,
        },
    }


def _patch_stream(monkeypatch: pytest.MonkeyPatch, events: list[dict]):
    async def fake_stream(text, house, recent, **kwargs):
        for e in events:
            yield e

    monkeypatch.setattr("goa_house.api.extract_diffs_stream", fake_stream)


def test_prompt_streams_diffs_via_sse(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    diffs = _add_bedroom_diffs()
    _patch_stream(
        monkeypatch,
        [
            {"type": "status", "tool": "mcp__goa__get_house", "label": "Reading the plan…"},
            _diffs_event(diffs),
        ],
    )
    r = client.post(
        "/designs/goa-sample/prompt",
        json={"text": "Add a 4x5m bedroom NE."},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _read_sse(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "session"  # initial session frame
    assert "status" in types
    assert types[-1] == "result"
    assert events[-1]["extractor_result"]["kind"] == "diffs"
    assert len(events[-1]["extractor_result"]["diffs"]) == 2


def test_prompt_streams_clarification(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _patch_stream(
        monkeypatch,
        [
            {
                "type": "result",
                "extractor_result": {
                    "kind": "clarification",
                    "diffs": [],
                    "question": "How big?",
                },
            }
        ],
    )
    r = client.post("/designs/goa-sample/prompt", json={"text": "big bedroom"})
    assert r.status_code == 200
    events = _read_sse(r.text)
    assert events[-1]["extractor_result"]["question"] == "How big?"


def test_prompt_empty_text_400(client: TestClient):
    r = client.post("/designs/goa-sample/prompt", json={"text": "   "})
    assert r.status_code == 400


def test_prompt_unknown_design_404(client: TestClient):
    r = client.post("/designs/no-such/prompt", json={"text": "hello"})
    assert r.status_code == 404


def test_prompt_streams_error_event(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _patch_stream(
        monkeypatch,
        [{"type": "error", "message": "model returned garbage"}],
    )
    r = client.post("/designs/goa-sample/prompt", json={"text": "hello"})
    # Stream already started → 200, error arrives as an event.
    assert r.status_code == 200
    events = _read_sse(r.text)
    err = next(e for e in events if e["type"] == "error")
    assert "garbage" in err["message"]


def test_prompt_persists_session_id_for_resume(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    captured: dict = {}

    async def fake_stream(text, house, recent, *, session_id=None, resume=None, **kwargs):
        captured.setdefault("calls", []).append(
            {"session_id": session_id, "resume": resume}
        )
        yield _diffs_event([])

    monkeypatch.setattr("goa_house.api.extract_diffs_stream", fake_stream)

    # First call — no existing session, server mints one and saves it.
    r1 = client.post("/designs/goa-sample/prompt", json={"text": "hi"})
    assert r1.status_code == 200
    first = captured["calls"][0]
    assert first["session_id"] is not None
    assert first["resume"] is None

    # Confirm .session_id was persisted (verify via the GET endpoint).
    sid_resp = client.get("/designs/goa-sample/sessions").json()
    assert sid_resp["session_id"] == first["session_id"]

    # Second call — should resume the saved session.
    r2 = client.post("/designs/goa-sample/prompt", json={"text": "hi again"})
    assert r2.status_code == 200
    second = captured["calls"][1]
    assert second["session_id"] is None
    assert second["resume"] == first["session_id"]


def test_sessions_clear_rotates(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    async def fake_stream(text, house, recent, **kwargs):
        yield _diffs_event([])

    monkeypatch.setattr("goa_house.api.extract_diffs_stream", fake_stream)

    client.post("/designs/goa-sample/prompt", json={"text": "hi"})
    sid_before = client.get("/designs/goa-sample/sessions").json()["session_id"]
    assert sid_before is not None

    cleared = client.post("/designs/goa-sample/sessions/clear").json()
    assert cleared["status"] == "ok"
    assert cleared["cleared_session_id"] == sid_before

    after = client.get("/designs/goa-sample/sessions").json()["session_id"]
    assert after is None

    # Next prompt mints a fresh session id, distinct from the old one.
    client.post("/designs/goa-sample/prompt", json={"text": "again"})
    sid_after = client.get("/designs/goa-sample/sessions").json()["session_id"]
    assert sid_after is not None
    assert sid_after != sid_before


def test_sessions_endpoint_unknown_design_404(client: TestClient):
    r = client.get("/designs/no-such/sessions")
    assert r.status_code == 404
    r = client.post("/designs/no-such/sessions/clear")
    assert r.status_code == 404


def test_approve_applies_diffs(client: TestClient, tmp_path: Path):
    payload = {
        "diffs": [d.model_dump(mode="json") for d in _add_bedroom_diffs()],
        "user_prompt": "Add a 4x5m bedroom NE with a window.",
    }
    r = client.post("/designs/goa-sample/requirements/approve", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["applied"] == ["req_0001", "req_0002"]
    assert "new_bedroom" in body["affected_rooms"]

    # house.json on disk now has the new room
    house = client.get("/designs/goa-sample/house.json").json()
    assert "new_bedroom" in {r["id"] for r in house["rooms"]}

    # requirements.jsonl populated
    reqs = client.get("/designs/goa-sample/requirements.jsonl").json()["requirements"]
    assert [r["id"] for r in reqs] == ["req_0001", "req_0002"]
    assert all(r["status"] == "approved" for r in reqs)


def test_approve_blocked_returns_409(client: TestClient):
    bad = Room(
        id="garage",
        name="Garage",
        polygon=[(0, 0), (4, 0), (4, 4), (0, 4)],
        openings=[Opening(type="door", wall="N", position_m=1.0, width_m=0.9, to_room="living_room")],
        camera=Camera(x=2, y=2, z=1.6, yaw_deg=0.0),
    )
    diffs = [
        RequirementDiff(
            proposed=ProposedRequirement(scope="garage", type="feature", statement="Add."),
            source_span="garage",
            mutation=AddRoomMutation(room=bad),
        ),
        RequirementDiff(
            proposed=ProposedRequirement(scope="living_room", type="adjacency", statement="Door"),
            source_span="garage",
            mutation=AddOpeningMutation(
                room_id="living_room",
                opening=Opening(type="door", wall="S", position_m=1.0, width_m=0.9, to_room="garage"),
            ),
        ),
    ]
    payload = {"diffs": [d.model_dump(mode="json") for d in diffs], "user_prompt": "x"}
    r = client.post("/designs/goa-sample/requirements/approve", json=payload)
    assert r.status_code == 409
    body = r.json()
    assert body["status"] == "blocked"
    assert any(i["code"] == "room_violates_setback" for i in body["issues"])


def test_approve_diff_apply_error_returns_400(client: TestClient):
    diffs = [
        RequirementDiff(
            proposed=ProposedRequirement(
                scope="ghost", type="dimension", statement="Resize ghost."
            ),
            source_span="ghost",
            mutation=AddOpeningMutation(
                room_id="ghost",
                opening=Opening(type="window", wall="N", position_m=1.0, width_m=1.0),
            ),
        )
    ]
    payload = {"diffs": [d.model_dump(mode="json") for d in diffs], "user_prompt": "x"}
    r = client.post("/designs/goa-sample/requirements/approve", json=payload)
    assert r.status_code == 400


def test_reject_appends_rejected_record(client: TestClient):
    diffs = [
        RequirementDiff(
            proposed=ProposedRequirement(
                scope="global", type="material", statement="Use marble floors."
            ),
            source_span="marble",
        )
    ]
    payload = {
        "diffs": [d.model_dump(mode="json") for d in diffs],
        "user_prompt": "marble floors please",
        "reason": "out of budget",
    }
    r = client.post("/designs/goa-sample/requirements/reject", json=payload)
    assert r.status_code == 200
    assert r.json()["rejected"] == ["req_0001"]
    reqs = client.get("/designs/goa-sample/requirements.jsonl").json()["requirements"]
    assert reqs[0]["status"] == "rejected"
    assert reqs[0]["rejection_reason"] == "out of budget"


def test_requirements_endpoint_empty_when_no_log(client: TestClient):
    r = client.get("/designs/goa-sample/requirements.jsonl")
    assert r.status_code == 200
    assert r.json() == {"requirements": []}

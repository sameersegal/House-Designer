"""Per-design Claude Agent SDK session id persistence.

Each design directory may contain a `.session_id` file holding a single
hex uuid that pins the agent's conversational memory across successive
`POST /prompt` calls. The SDK keeps the actual transcript on disk under
its own session store; we only persist the *handle*.

`clear_session()` deletes the handle; the next prompt mints a new uuid
and starts a fresh agent session. The old SDK transcript is left intact
(it remains discoverable via `list_sessions()` for audit/debug).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

SESSION_FILE_NAME = ".session_id"


def get_session_id(design_dir: Path) -> Optional[str]:
    """Read the design's saved session id, or None if no session is active."""
    path = Path(design_dir) / SESSION_FILE_NAME
    if not path.exists():
        return None
    sid = path.read_text(encoding="utf-8").strip()
    return sid or None


def save_session_id(design_dir: Path, session_id: str) -> None:
    """Persist `session_id` as the design's current session handle."""
    path = Path(design_dir) / SESSION_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session_id.strip() + "\n", encoding="utf-8")


def clear_session(design_dir: Path) -> Optional[str]:
    """Drop the design's session handle. Returns the cleared id (or None)."""
    path = Path(design_dir) / SESSION_FILE_NAME
    if not path.exists():
        return None
    sid = path.read_text(encoding="utf-8").strip() or None
    path.unlink()
    return sid


def new_session_id() -> str:
    """Mint a fresh uuid4 hex string suitable for a Claude Agent SDK session id."""
    return str(uuid.uuid4())

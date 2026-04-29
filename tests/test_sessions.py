from __future__ import annotations

from pathlib import Path

from goa_house.agents.sessions import (
    SESSION_FILE_NAME,
    clear_session,
    get_session_id,
    new_session_id,
    save_session_id,
)


def test_get_returns_none_when_missing(tmp_path: Path):
    assert get_session_id(tmp_path) is None


def test_save_then_get_round_trips(tmp_path: Path):
    save_session_id(tmp_path, "abc123")
    assert get_session_id(tmp_path) == "abc123"


def test_save_strips_whitespace(tmp_path: Path):
    save_session_id(tmp_path, "  xyz  ")
    assert get_session_id(tmp_path) == "xyz"
    # File contents are exactly one trailing newline
    content = (tmp_path / SESSION_FILE_NAME).read_text(encoding="utf-8")
    assert content == "xyz\n"


def test_save_overwrites(tmp_path: Path):
    save_session_id(tmp_path, "first")
    save_session_id(tmp_path, "second")
    assert get_session_id(tmp_path) == "second"


def test_clear_removes_file_and_returns_id(tmp_path: Path):
    save_session_id(tmp_path, "deadbeef")
    cleared = clear_session(tmp_path)
    assert cleared == "deadbeef"
    assert not (tmp_path / SESSION_FILE_NAME).exists()
    assert get_session_id(tmp_path) is None


def test_clear_when_missing_is_noop(tmp_path: Path):
    assert clear_session(tmp_path) is None


def test_clear_empty_file_returns_none(tmp_path: Path):
    (tmp_path / SESSION_FILE_NAME).write_text("", encoding="utf-8")
    assert clear_session(tmp_path) is None
    assert not (tmp_path / SESSION_FILE_NAME).exists()


def test_new_session_id_is_unique_and_hex(tmp_path: Path):
    a = new_session_id()
    b = new_session_id()
    assert a != b
    assert len(a) == 32
    int(a, 16)  # must be valid hex


def test_save_creates_parent_dir(tmp_path: Path):
    nested = tmp_path / "designs" / "fresh"
    save_session_id(nested, new_session_id())
    assert (nested / SESSION_FILE_NAME).exists()

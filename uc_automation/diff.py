"""Content diff — detect new files and announcements since last run."""

import json
from datetime import datetime
from pathlib import Path

from .config import CONTENT_STATE_FILE
from .scraper import CourseFile, Announcement


# ── State persistence ────────────────────────────────────────────────────────

def _load_state(path: Path = CONTENT_STATE_FILE) -> dict:
    if not path.exists():
        return {"files": [], "announcements": [], "last_run": None}
    with open(path) as f:
        return json.load(f)


def _save_state(state: dict, path: Path = CONTENT_STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2, default=str)


def _file_key(f: CourseFile) -> str:
    return f"{f.course}::{f.name}::{f.url}"


def _announcement_key(a: Announcement) -> str:
    return f"{a.course}::{a.title}::{a.posted.isoformat()}"


# ── Diff functions ───────────────────────────────────────────────────────────

def find_new_files(
    current_files: list[CourseFile],
    state_path: Path = CONTENT_STATE_FILE,
) -> list[CourseFile]:
    """Return files that weren't present in the previous run."""
    state = _load_state(state_path)
    known_keys = set(state.get("files", []))
    new_files = [f for f in current_files if _file_key(f) not in known_keys]
    return new_files


def find_new_announcements(
    current_announcements: list[Announcement],
    state_path: Path = CONTENT_STATE_FILE,
) -> list[Announcement]:
    """Return announcements that weren't present in the previous run."""
    state = _load_state(state_path)
    known_keys = set(state.get("announcements", []))
    new_ann = [a for a in current_announcements if _announcement_key(a) not in known_keys]
    return new_ann


def save_current_state(
    files: list[CourseFile],
    announcements: list[Announcement],
    state_path: Path = CONTENT_STATE_FILE,
) -> None:
    """Persist the current content snapshot so the next run can diff against it."""
    state = {
        "files": [_file_key(f) for f in files],
        "announcements": [_announcement_key(a) for a in announcements],
        "last_run": datetime.now().isoformat(),
    }
    _save_state(state, state_path)
    print(f"[diff] State saved ({len(files)} files, {len(announcements)} announcements).")


def last_run_time(state_path: Path = CONTENT_STATE_FILE) -> datetime | None:
    """Return the datetime of the last successful run, or None."""
    state = _load_state(state_path)
    raw = state.get("last_run")
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return None

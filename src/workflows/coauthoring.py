"""Doc coauthoring — draft versioning and iterative refinement.

Implements the doc-coauthoring skill pattern: track draft versions as the CEO
and agent collaboratively refine memos, decks, and canvases. Each edit creates
a new immutable DraftVersion; the DraftSession holds the full history.
"""

import difflib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class DraftVersion(BaseModel):
    version: int
    content: str
    author: str  # "ceo" | "agent" | "system"
    edit_note: str = ""
    timestamp: str
    char_delta: int = 0
    line_diff: List[str] = Field(default_factory=list)


class DraftSession(BaseModel):
    session_id: str
    artifact_id: str
    draft_type: str  # "memo" | "deck" | "canvas" | "report"
    title: str
    versions: List[DraftVersion]
    created_at: str
    updated_at: str

    @property
    def current_version(self) -> DraftVersion:
        return self.versions[-1]

    @property
    def version_count(self) -> int:
        return len(self.versions)

    @property
    def current_content(self) -> str:
        return self.current_version.content


_SESSIONS: Dict[str, DraftSession] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_diff(old_content: str, new_content: str) -> List[str]:
    """Return unified diff lines (capped at 50) between two content strings."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(old_lines, new_lines, fromfile="v_prev", tofile="v_new")
    )
    return diff[:50]


def create_draft_session(
    artifact_id: str,
    draft_type: str,
    title: str,
    initial_content: str,
    author: str = "system",
) -> DraftSession:
    """Create a new DraftSession with version 1 seeded from initial_content."""
    session_id = f"draft_{artifact_id}_{uuid4().hex[:8]}"
    now = _now_iso()
    v1 = DraftVersion(
        version=1,
        content=initial_content,
        author=author,
        edit_note="Initial draft",
        timestamp=now,
        char_delta=len(initial_content),
        line_diff=[],
    )
    session = DraftSession(
        session_id=session_id,
        artifact_id=artifact_id,
        draft_type=draft_type,
        title=title,
        versions=[v1],
        created_at=now,
        updated_at=now,
    )
    _SESSIONS[session_id] = session
    return session


def get_draft_session(session_id: str) -> Optional[DraftSession]:
    """Return a session by ID, or None if not found."""
    return _SESSIONS.get(session_id)


def apply_edit(
    session_id: str,
    new_content: str,
    author: str,
    edit_note: str = "",
) -> Optional[DraftVersion]:
    """Apply an edit to an existing session, creating a new DraftVersion."""
    session = _SESSIONS.get(session_id)
    if session is None:
        return None

    prev_version = session.current_version
    char_delta = len(new_content) - len(prev_version.content)
    line_diff = _compute_diff(prev_version.content, new_content)

    new_ver = DraftVersion(
        version=prev_version.version + 1,
        content=new_content,
        author=author,
        edit_note=edit_note,
        timestamp=_now_iso(),
        char_delta=char_delta,
        line_diff=line_diff,
    )
    session.versions.append(new_ver)
    session.updated_at = new_ver.timestamp
    return new_ver


def get_diff(session_id: str, from_version: int, to_version: int) -> List[str]:
    """Return unified diff lines between any two version numbers in a session."""
    session = _SESSIONS.get(session_id)
    if session is None:
        return []

    version_map = {v.version: v for v in session.versions}
    from_ver = version_map.get(from_version)
    to_ver = version_map.get(to_version)
    if from_ver is None or to_ver is None:
        return []

    return _compute_diff(from_ver.content, to_ver.content)


def list_sessions() -> List[DraftSession]:
    """Return all sessions sorted by updated_at descending."""
    return sorted(_SESSIONS.values(), key=lambda s: s.updated_at, reverse=True)


def export_session_summary(session_id: str) -> Dict[str, Any]:
    """Return a summary dict for a session including full edit history."""
    session = _SESSIONS.get(session_id)
    if session is None:
        return {}

    edit_history = [
        {
            "version": v.version,
            "author": v.author,
            "edit_note": v.edit_note,
            "char_delta": v.char_delta,
            "timestamp": v.timestamp,
        }
        for v in session.versions
    ]

    return {
        "session_id": session.session_id,
        "artifact_id": session.artifact_id,
        "draft_type": session.draft_type,
        "title": session.title,
        "version_count": session.version_count,
        "current_content": session.current_content,
        "edit_history": edit_history,
    }

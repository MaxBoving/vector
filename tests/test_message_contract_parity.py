"""Contract parity tests: backend schemas ↔ frontend TypeScript types.

These tests parse the frontend TypeScript type file and compare the enum
unions and required fields against the Python schema definitions in
src/api/schemas.py.

A failing test here means the backend and frontend have drifted — fix the
divergence before merging, not at runtime.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import get_args

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TS_TYPES_FILE = _REPO_ROOT / "frontend" / "src" / "components" / "messages" / "types.ts"


# ---------------------------------------------------------------------------
# TypeScript parser helpers
# ---------------------------------------------------------------------------

def _read_ts() -> str:
    return _TS_TYPES_FILE.read_text(encoding="utf-8")


def _extract_ts_union(ts_source: str, field_pattern: str) -> set[str]:
    """Extract string literal union members for a typed field in the TS source.

    Matches patterns like:
        field_name: 'a' | 'b' | 'c'
        field_name?: 'a' | 'b' | null

    Returns the set of non-null string values.
    """
    # Escape the pattern for use in regex
    escaped = re.escape(field_pattern)
    # Match the field name followed by its union type (may span to end of the type)
    pattern = rf"{escaped}\??\s*:\s*((?:'[^']*'\s*\|?\s*)+)"
    match = re.search(pattern, ts_source)
    if not match:
        return set()
    raw = match.group(1)
    # Extract all single-quoted string literals
    return {m.group(1) for m in re.finditer(r"'([^']+)'", raw)}


# ---------------------------------------------------------------------------
# Backend schema helpers
# ---------------------------------------------------------------------------

def _backend_workflow_types() -> set[str]:
    from src.api.schemas import WorkflowType
    return set(get_args(WorkflowType))


def _backend_response_types() -> set[str]:
    from src.api.schemas import ResponseType
    return set(get_args(ResponseType))


def _backend_presentation_modes() -> set[str]:
    from src.api.schemas import PresentationMode
    return set(get_args(PresentationMode))


def _backend_message_top_level_fields() -> set[str]:
    from src.api.schemas import AssistantMessageResponse
    return set(AssistantMessageResponse.model_fields.keys())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_workflow_type_parity() -> None:
    """Backend WorkflowType Literal == frontend AssistantMessage.workflow_type union."""
    ts = _read_ts()
    frontend = _extract_ts_union(ts, "workflow_type")
    backend = _backend_workflow_types()

    missing_from_frontend = backend - frontend
    missing_from_backend = frontend - backend

    assert not missing_from_frontend, (
        f"workflow_type values present in backend but missing from frontend types.ts:\n"
        f"  {sorted(missing_from_frontend)}\n"
        f"Add them to the workflow_type union in AssistantMessage."
    )
    assert not missing_from_backend, (
        f"workflow_type values present in frontend types.ts but missing from backend:\n"
        f"  {sorted(missing_from_backend)}\n"
        f"Either add them to WorkflowType in schemas.py or remove from types.ts."
    )


def test_response_type_parity() -> None:
    """Backend ResponseType Literal == frontend AssistantMessage.response_type union."""
    ts = _read_ts()
    frontend = _extract_ts_union(ts, "response_type")
    backend = _backend_response_types()

    missing_from_frontend = backend - frontend
    missing_from_backend = frontend - backend

    assert not missing_from_frontend, (
        f"response_type values in backend but missing from frontend:\n"
        f"  {sorted(missing_from_frontend)}"
    )
    assert not missing_from_backend, (
        f"response_type values in frontend but missing from backend:\n"
        f"  {sorted(missing_from_backend)}"
    )


def test_presentation_mode_parity() -> None:
    """Backend PresentationMode Literal == frontend presentation.mode union."""
    ts = _read_ts()
    # The mode field appears inside the presentation block as `mode?:`
    frontend = _extract_ts_union(ts, "mode")
    backend = _backend_presentation_modes()

    missing_from_frontend = backend - frontend
    missing_from_backend = frontend - backend

    assert not missing_from_frontend, (
        f"presentation mode values in backend but missing from frontend:\n"
        f"  {sorted(missing_from_frontend)}"
    )
    assert not missing_from_backend, (
        f"presentation mode values in frontend but missing from backend:\n"
        f"  {sorted(missing_from_backend)}"
    )


def test_top_level_required_fields_present_in_frontend() -> None:
    """Every required field in AssistantMessageResponse appears in the frontend AssistantMessage type."""
    ts = _read_ts()

    # Extract top-level field names from the AssistantMessage type block.
    # Capture everything between `export type AssistantMessage = {` and the closing `}`
    match = re.search(r"export type AssistantMessage\s*=\s*\{(.+?)\n\}", ts, re.DOTALL)
    assert match, "Could not find AssistantMessage type definition in types.ts"

    type_body = match.group(1)
    # Field names: lines starting with optional whitespace + identifier + optional ? + :
    ts_fields = {
        m.group(1)
        for m in re.finditer(r"^\s{2}(\w+)\??\s*:", type_body, re.MULTILINE)
    }

    from src.api.schemas import AssistantMessageResponse
    required_backend_fields = {
        name
        for name, field in AssistantMessageResponse.model_fields.items()
        if field.is_required()
    }

    # Map Python snake_case to the field names as they appear in the serialized JSON.
    # Pydantic v2 uses the field name as-is by default (no alias transformation here).
    missing = required_backend_fields - ts_fields
    assert not missing, (
        f"Required backend fields missing from frontend AssistantMessage type:\n"
        f"  {sorted(missing)}\n"
        f"Add them to AssistantMessage in types.ts."
    )


def test_no_deprecated_schedule_types_in_frontend() -> None:
    """Deprecated schedule aliases must not appear as first-class types in the frontend."""
    ts = _read_ts()
    deprecated = {"day_schedule_planning", "week_schedule_planning"}
    frontend_workflow_types = _extract_ts_union(ts, "workflow_type")
    present = deprecated & frontend_workflow_types
    assert not present, (
        f"Deprecated schedule workflow types still present in frontend types.ts: {sorted(present)}\n"
        f"Remove them — use 'schedule_planning' instead."
    )


def test_no_deprecated_schedule_types_in_backend_schema() -> None:
    """Deprecated schedule aliases must not appear in the backend WorkflowType Literal."""
    deprecated = {"day_schedule_planning", "week_schedule_planning"}
    backend = _backend_workflow_types()
    present = deprecated & backend
    assert not present, (
        f"Deprecated schedule workflow types still in backend WorkflowType: {sorted(present)}\n"
        f"Remove them from the WorkflowType Literal in schemas.py."
    )

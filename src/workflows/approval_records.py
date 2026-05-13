from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from src.workflows.approval_envelope import normalize_gate_metadata


def build_approval_record(
    *,
    stage: str | None,
    gate: Mapping[str, Any] | None,
    decision: str,
    note: str | None = None,
    actor: str | None = None,
    mode: str | None = None,
    resolved_at: str | None = None,
) -> dict[str, Any]:
    normalized_gate = normalize_gate_metadata(gate)
    return {
        "stage": stage,
        "decision": str(decision).strip().lower(),
        "note": note,
        "actor": actor,
        "mode": mode or _mode_from_gate(normalized_gate),
        "gate_type": normalized_gate.get("gate_type") if normalized_gate else None,
        "reason": normalized_gate.get("reason") if normalized_gate else None,
        "triggered_at": normalized_gate.get("triggered_at") if normalized_gate else None,
        "resolved_at": resolved_at or datetime.now().isoformat(),
    }


def build_pending_interaction_context(
    *,
    gate: Mapping[str, Any] | None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    normalized_gate = normalize_gate_metadata(gate)
    if normalized_gate is not None:
        context["gate"] = normalized_gate
    if isinstance(extra, Mapping):
        context.update({key: value for key, value in extra.items()})
    return context


def build_resolved_interaction_context(*, approval: Mapping[str, Any] | None) -> dict[str, Any]:
    return {"approval": dict(approval)} if isinstance(approval, Mapping) else {}


def _mode_from_gate(gate: Mapping[str, Any] | None) -> str | None:
    if not isinstance(gate, Mapping):
        return None
    context = gate.get("context")
    if isinstance(context, Mapping):
        raw_mode = context.get("mode")
        if raw_mode:
            return str(raw_mode).strip().lower() or None
    return None

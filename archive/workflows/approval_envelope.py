from __future__ import annotations

from typing import Any, Mapping


def normalize_gate_metadata(gate: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(gate, Mapping):
        return None

    options = gate.get("options")
    context = gate.get("context")
    return {
        "gate_type": gate.get("gate_type"),
        "reason": gate.get("reason"),
        "options": [option for option in options if isinstance(option, dict)] if isinstance(options, list) else [],
        "context": dict(context) if isinstance(context, Mapping) else {},
        "triggered_at": gate.get("triggered_at"),
        "resolved_at": gate.get("resolved_at"),
        "resolution": _normalize_resolution(gate.get("resolution")),
    }


def build_approval_metadata(
    *,
    status: str,
    stage: str | None,
    gate: Mapping[str, Any] | None = None,
    decision: str | None = None,
    mode: str | None = None,
    note: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    normalized_gate = normalize_gate_metadata(gate)
    normalized_decision = _normalize_resolution(decision)
    return {
        "status": status,
        "required": normalized_gate is not None or status == "pending",
        "stage": stage,
        "gate_type": normalized_gate.get("gate_type") if normalized_gate else None,
        "reason": normalized_gate.get("reason") if normalized_gate else None,
        "triggered_at": normalized_gate.get("triggered_at") if normalized_gate else None,
        "resolved_at": normalized_gate.get("resolved_at") if normalized_gate else None,
        "decision": normalized_decision or (normalized_gate.get("resolution") if normalized_gate else None),
        "mode": mode,
        "note": note,
        "actor": actor,
    }


def build_approval_metadata_from_record(
    *,
    stage: str | None,
    record: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(record, Mapping) or not record:
        return None

    decision = _normalize_resolution(record.get("decision"))
    status = "approved" if decision == "approve" else "rejected" if decision == "reject" else "resolved"
    return {
        "status": status,
        "required": True,
        "stage": stage,
        "gate_type": record.get("gate_type"),
        "reason": record.get("reason"),
        "triggered_at": record.get("triggered_at"),
        "resolved_at": record.get("resolved_at"),
        "decision": decision,
        "mode": record.get("mode"),
        "note": record.get("note"),
        "actor": record.get("actor"),
    }


def _normalize_resolution(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class RequestInterpretationPolicy:
    document_explanation_markers: tuple[str, ...]
    meeting_context_markers: tuple[str, ...]
    meeting_prep_intent_markers: tuple[str, ...]
    analysis_only_guard_markers: tuple[str, ...]
    email_proposal_verbs: tuple[str, ...]
    email_proposal_context_markers: tuple[str, ...]
    calendar_proposal_verbs: tuple[str, ...]
    calendar_proposal_context_markers: tuple[str, ...]
    email_channel_markers: tuple[str, ...]
    email_explicit_draft_markers: tuple[str, ...]
    calendar_channel_markers: tuple[str, ...]
    calendar_explicit_scheduling_markers: tuple[str, ...]
    soft_write_markers: tuple[str, ...]
    explicit_action_markers: tuple[str, ...]
    non_act_report_markers: tuple[str, ...]
    compound_markers: tuple[str, ...]
    analysis_compound_markers: tuple[str, ...]
    action_candidate_draft_markers: tuple[str, ...]
    email_target_pronoun_markers: tuple[str, ...]
    no_email_markers: tuple[str, ...]
    document_object_markers: tuple[str, ...]
    document_deliverable_markers: tuple[str, ...]
    action_deliverable_markers: tuple[str, ...]
    planning_future_markers: tuple[str, ...]
    planning_intent_markers: tuple[str, ...]
    morning_scope_markers: tuple[str, ...]
    focus_intent_markers: tuple[str, ...]
    overview_markers: tuple[str, ...]
    report_deliverable_markers: tuple[str, ...]
    schedule_planning_markers: tuple[str, ...]
    retrospective_markers: tuple[str, ...]
    conversational_preference_markers: tuple[str, ...]
    report_decision_markers: tuple[str, ...]
    report_topic_markers: tuple[str, ...]
    arbitration_close_score_delta: float
    arbitration_compound_override_min_strength: int
    arbitration_block_act_promotion_from_non_act: bool


def _default_policy_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "request_interpretation_policy.json"


def _load_policy_json() -> dict[str, object]:
    configured = os.getenv("REQUEST_INTERPRETATION_POLICY_PATH", "").strip()
    path = Path(configured) if configured else _default_policy_path()
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise RuntimeError(f"request interpretation policy must be a JSON object: {path}")
    return data


def _read_markers(data: dict[str, object], key: str) -> tuple[str, ...]:
    raw = data.get(key)
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f"request interpretation policy key '{key}' must be a non-empty array")
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise RuntimeError(f"request interpretation policy key '{key}' must contain non-empty strings")
        values.append(item)
    return tuple(values)


def _read_float(data: dict[str, object], key: str, *, minimum: float = 0.0) -> float:
    raw = data.get(key)
    if not isinstance(raw, (int, float)):
        raise RuntimeError(f"request interpretation policy key '{key}' must be a number")
    value = float(raw)
    if value < minimum:
        raise RuntimeError(f"request interpretation policy key '{key}' must be >= {minimum}")
    return value


def _read_int(data: dict[str, object], key: str, *, minimum: int = 0) -> int:
    raw = data.get(key)
    if not isinstance(raw, int):
        raise RuntimeError(f"request interpretation policy key '{key}' must be an integer")
    if raw < minimum:
        raise RuntimeError(f"request interpretation policy key '{key}' must be >= {minimum}")
    return raw


def _read_bool(data: dict[str, object], key: str) -> bool:
    raw = data.get(key)
    if not isinstance(raw, bool):
        raise RuntimeError(f"request interpretation policy key '{key}' must be a boolean")
    return raw


@lru_cache(maxsize=1)
def get_request_interpretation_policy() -> RequestInterpretationPolicy:
    data = _load_policy_json()
    return RequestInterpretationPolicy(
        document_explanation_markers=_read_markers(data, "document_explanation_markers"),
        meeting_context_markers=_read_markers(data, "meeting_context_markers"),
        meeting_prep_intent_markers=_read_markers(data, "meeting_prep_intent_markers"),
        analysis_only_guard_markers=_read_markers(data, "analysis_only_guard_markers"),
        email_proposal_verbs=_read_markers(data, "email_proposal_verbs"),
        email_proposal_context_markers=_read_markers(data, "email_proposal_context_markers"),
        calendar_proposal_verbs=_read_markers(data, "calendar_proposal_verbs"),
        calendar_proposal_context_markers=_read_markers(data, "calendar_proposal_context_markers"),
        email_channel_markers=_read_markers(data, "email_channel_markers"),
        email_explicit_draft_markers=_read_markers(data, "email_explicit_draft_markers"),
        calendar_channel_markers=_read_markers(data, "calendar_channel_markers"),
        calendar_explicit_scheduling_markers=_read_markers(data, "calendar_explicit_scheduling_markers"),
        soft_write_markers=_read_markers(data, "soft_write_markers"),
        explicit_action_markers=_read_markers(data, "explicit_action_markers"),
        non_act_report_markers=_read_markers(data, "non_act_report_markers"),
        compound_markers=_read_markers(data, "compound_markers"),
        analysis_compound_markers=_read_markers(data, "analysis_compound_markers"),
        action_candidate_draft_markers=_read_markers(data, "action_candidate_draft_markers"),
        email_target_pronoun_markers=_read_markers(data, "email_target_pronoun_markers"),
        no_email_markers=_read_markers(data, "no_email_markers"),
        document_object_markers=_read_markers(data, "document_object_markers"),
        document_deliverable_markers=_read_markers(data, "document_deliverable_markers"),
        action_deliverable_markers=_read_markers(data, "action_deliverable_markers"),
        planning_future_markers=_read_markers(data, "planning_future_markers"),
        planning_intent_markers=_read_markers(data, "planning_intent_markers"),
        morning_scope_markers=_read_markers(data, "morning_scope_markers"),
        focus_intent_markers=_read_markers(data, "focus_intent_markers"),
        overview_markers=_read_markers(data, "overview_markers"),
        report_deliverable_markers=_read_markers(data, "report_deliverable_markers"),
        schedule_planning_markers=_read_markers(data, "schedule_planning_markers"),
        retrospective_markers=_read_markers(data, "retrospective_markers"),
        conversational_preference_markers=_read_markers(data, "conversational_preference_markers"),
        report_decision_markers=_read_markers(data, "report_decision_markers"),
        report_topic_markers=_read_markers(data, "report_topic_markers"),
        arbitration_close_score_delta=_read_float(data, "arbitration_close_score_delta", minimum=0.0),
        arbitration_compound_override_min_strength=_read_int(data, "arbitration_compound_override_min_strength", minimum=0),
        arbitration_block_act_promotion_from_non_act=_read_bool(data, "arbitration_block_act_promotion_from_non_act"),
    )

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlmodel import Session, select

import src.core.database as database
from src.core.models import AssistantConversation, SessionInteraction

OPTION_VALUE_TO_SIGNAL: dict[str, tuple[str, str]] = {
    "board_packet": ("output_format", "board_presentation"),
    "board_presentation": ("output_format", "board_presentation"),
    "personal_decision": ("output_format", "personal_decision"),
    "operating_decision": ("output_format", "personal_decision"),
    "calendar_first": ("day_optimization", "calendar_first"),
    "inbox_deadlines": ("day_optimization", "inbox_deadlines"),
    "meeting_focused": ("day_optimization", "meeting_focused"),
    "focus_blocks": ("day_optimization", "focus_blocks"),
    "close_workbook": ("data_source", "close_workbook"),
    "company_state": ("data_source", "company_state"),
    "current_month": ("time_anchor", "current_month"),
    "quarter_close": ("time_anchor", "quarter_close"),
    "draft_response": ("escalation_mode", "draft_response"),
    "brief_only": ("escalation_mode", "brief_only"),
}


def _normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(_normalize_text(left).split())
    right_tokens = set(_normalize_text(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _response_payload(interaction: SessionInteraction) -> dict[str, Any]:
    if not interaction.response:
        return {}
    try:
        parsed = json.loads(interaction.response)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clarification_options_from_response(interaction: SessionInteraction) -> list[dict[str, Any]]:
    payload = _response_payload(interaction)
    trust = payload.get("trust") if isinstance(payload, dict) else {}
    raw_question_options = trust.get("question_options") if isinstance(trust, dict) else []
    options: list[dict[str, Any]] = []
    for question_entry in raw_question_options or []:
        if not isinstance(question_entry, dict):
            continue
        if str(question_entry.get("offer_type") or "").strip() == "action_offer":
            continue
        question = str(question_entry.get("question") or "").strip()
        for option in question_entry.get("options") or []:
            if not isinstance(option, dict):
                continue
            options.append(
                {
                    "question": question,
                    "label": str(option.get("label") or "").strip(),
                    "value": str(option.get("value") or "").strip(),
                    "apply_text": str(option.get("apply_text") or "").strip(),
                    "description": str(option.get("description") or "").strip(),
                }
            )
    return options


def _find_latest_clarification_interaction(ceo_id: str, conversation_id: str) -> SessionInteraction | None:
    with Session(database.engine) as session:
        conversation = session.exec(
            select(AssistantConversation)
            .where(AssistantConversation.ceo_id == ceo_id)
            .where(AssistantConversation.conversation_id == conversation_id)
        ).first()
        interaction_ids = list(conversation.interaction_ids or []) if conversation else []

    interactions = database.get_interactions_for_conversation(ceo_id, interaction_ids)

    for interaction in reversed(interactions):
        payload = _response_payload(interaction)
        if str(payload.get("response_type") or "").strip() == "clarification":
            return interaction
    return None


def _match_option(answer_text: str, options: list[dict[str, Any]], selected_option_value: str | None = None) -> dict[str, Any] | None:
    normalized_answer = _normalize_text(answer_text)
    if not normalized_answer and not selected_option_value:
        return None

    if selected_option_value:
        for option in options:
            if option.get("value") == selected_option_value:
                return option

    best_option: dict[str, Any] | None = None
    best_score = 0.0
    for option in options:
        candidate = " ".join(
            part for part in (
                option.get("label"),
                option.get("value"),
                option.get("apply_text"),
                option.get("description"),
            )
            if part
        )
        normalized_candidate = _normalize_text(candidate)
        if not normalized_candidate:
            continue
        score = _token_overlap(normalized_answer, normalized_candidate)
        if normalized_answer == normalized_candidate:
            score = 1.0
        elif normalized_answer in normalized_candidate or normalized_candidate in normalized_answer:
            score = max(score, 0.8)
        else:
            score = max(score, SequenceMatcher(None, normalized_answer, normalized_candidate).ratio())
        if score > best_score:
            best_option = option
            best_score = score

    return best_option if best_score >= 0.35 else None


def record_clarification_follow_up(
    *,
    ceo_id: str,
    conversation_id: str,
    answer_text: str,
    source_interaction_id: int | None = None,
    source_response_type: str | None = None,
    selected_option_value: str | None = None,
    selected_option_label: str | None = None,
    selected_option_apply_text: str | None = None,
) -> dict[str, str] | None:
    if not ceo_id or not conversation_id:
        return None

    source_interaction: SessionInteraction | None = None
    if source_interaction_id is not None:
        with Session(database.engine) as session:
            interaction = session.get(SessionInteraction, source_interaction_id)
            if interaction and interaction.ceo_id == ceo_id:
                conversation = session.exec(
                    select(AssistantConversation)
                    .where(AssistantConversation.ceo_id == ceo_id)
                    .where(AssistantConversation.conversation_id == conversation_id)
                ).first()
                if conversation and source_interaction_id in (conversation.interaction_ids or []):
                    source_interaction = interaction

    if source_interaction is None:
        source_interaction = _find_latest_clarification_interaction(ceo_id, conversation_id)

    if source_interaction is None:
        return None

    payload = _response_payload(source_interaction)
    response_type = str(source_response_type or payload.get("response_type") or "").strip()
    if response_type != "clarification":
        return None

    options = _clarification_options_from_response(source_interaction)
    if not options:
        return None

    selected_option = _match_option(
        answer_text or selected_option_apply_text or selected_option_label or selected_option_value or "",
        options,
        selected_option_value=selected_option_value,
    )
    if selected_option is None:
        return None

    signal = OPTION_VALUE_TO_SIGNAL.get(selected_option.get("value") or "")
    if not signal:
        return None

    signal_type, signal_value = signal
    database.record_clarification_answer(
        ceo_id,
        conversation_id,
        signal_type=signal_type,
        signal_value=signal_value,
    )
    return {
        "signal_type": signal_type,
        "signal_value": signal_value,
        "option_value": str(selected_option.get("value") or ""),
        "option_label": str(selected_option.get("label") or ""),
    }

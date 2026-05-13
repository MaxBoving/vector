from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlmodel import Session, select

import src.core.database as database
from src.core.models import AssistantConversation, ConversationLiveContext, SessionInteraction

OPTION_VALUE_TO_SIGNAL: dict[str, tuple[str, str]] = {
    "board_packet": ("output_format", "board_presentation"),
    "board_presentation": ("output_format", "board_presentation"),
    "personal_decision": ("output_format", "personal_decision"),
    "operating_decision": ("output_format", "personal_decision"),
    "list_form": ("presentation_style", "list_form"),
    "narrative_recap": ("presentation_style", "narrative_recap"),
    "timeline": ("presentation_style", "timeline"),
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


def _missing_data_context(interaction: SessionInteraction) -> dict[str, Any]:
    raw_context = interaction.missing_data_context
    if not raw_context:
        return {}
    if isinstance(raw_context, dict):
        return raw_context
    try:
        parsed = json.loads(raw_context)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _gate_from_interaction(interaction: SessionInteraction) -> dict[str, Any]:
    context = _missing_data_context(interaction)
    gate = context.get("gate") if isinstance(context, dict) else {}
    return gate if isinstance(gate, dict) else {}


def _latest_clarification_resolution_record(
    ceo_id: str,
    conversation_id: str,
    *,
    source_interaction_id: int | None = None,
) -> dict[str, Any] | None:
    with Session(database.engine) as session:
        ctx = session.exec(
            select(ConversationLiveContext)
            .where(ConversationLiveContext.ceo_id == ceo_id)
            .where(ConversationLiveContext.conversation_id == conversation_id)
        ).first()
    if not ctx:
        return None
    for record in reversed(list(ctx.clarification_resolutions or [])):
        if not isinstance(record, dict):
            continue
        if source_interaction_id is not None and record.get("source_interaction_id") != source_interaction_id:
            continue
        return record
    return None


def _clarification_options_from_interaction(interaction: SessionInteraction) -> list[dict[str, Any]]:
    gate = _gate_from_interaction(interaction)
    raw_question_options = gate.get("options") if isinstance(gate, dict) else []
    if not raw_question_options:
        payload = _response_payload(interaction)
        trust = payload.get("trust") if isinstance(payload, dict) else {}
        raw_question_options = trust.get("question_options") if isinstance(trust, dict) else []
        if not raw_question_options and isinstance(payload, dict):
            raw_question_options = payload.get("clarification_options") or []
    options: list[dict[str, Any]] = []
    for question_entry in raw_question_options or []:
        if not isinstance(question_entry, dict):
            continue
        question = str(
            question_entry.get("question")
            or gate.get("reason")
            or ""
        ).strip()
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
        if interaction.gate_type == "CLARIFICATION_REQUIRED":
            return interaction
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


def _resolve_selected_option(
    *,
    answer_text: str,
    options: list[dict[str, Any]],
    selected_option_value: str | None = None,
    selected_option_label: str | None = None,
    selected_option_apply_text: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if selected_option_value:
        for option in options:
            if option.get("value") == selected_option_value:
                return option, "explicit_value"

    if selected_option_label:
        normalized_label = _normalize_text(selected_option_label)
        if normalized_label:
            for option in options:
                if _normalize_text(str(option.get("label") or "")) == normalized_label:
                    return option, "explicit_label"

    if selected_option_apply_text:
        normalized_apply_text = _normalize_text(selected_option_apply_text)
        if normalized_apply_text:
            for option in options:
                if _normalize_text(str(option.get("apply_text") or "")) == normalized_apply_text:
                    return option, "explicit_apply_text"

    selected_option = _match_option(
        answer_text,
        options,
        selected_option_value=selected_option_value,
    )
    if selected_option is not None:
        return selected_option, "text_match"

    return None, None


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

    existing_resolution = _latest_clarification_resolution_record(
        ceo_id,
        conversation_id,
        source_interaction_id=source_interaction.id,
    )
    if existing_resolution:
        selected_option = existing_resolution.get("selected_option") if isinstance(existing_resolution, dict) else {}
        signal_type = str(existing_resolution.get("signal_type") or "").strip()
        signal_value = str(existing_resolution.get("signal_value") or "").strip()
        if signal_type and signal_value:
            return {
                "signal_type": signal_type,
                "signal_value": signal_value,
                "option_value": str(selected_option.get("value") or "") if isinstance(selected_option, dict) else "",
                "option_label": str(selected_option.get("label") or "") if isinstance(selected_option, dict) else "",
                "match_strategy": str(existing_resolution.get("match_strategy") or ""),
            }

    payload = _response_payload(source_interaction)
    response_type = str(source_response_type or payload.get("response_type") or "").strip()
    if response_type != "clarification" and source_interaction.gate_type != "CLARIFICATION_REQUIRED":
        return None

    gate = _gate_from_interaction(source_interaction)
    options = _clarification_options_from_interaction(source_interaction)
    if not options:
        return None

    selected_option, match_strategy = _resolve_selected_option(
        answer_text=answer_text,
        options=options,
        selected_option_value=selected_option_value,
        selected_option_label=selected_option_label,
        selected_option_apply_text=selected_option_apply_text,
    )
    if selected_option is None:
        return None

    signal = OPTION_VALUE_TO_SIGNAL.get(selected_option.get("value") or "")
    if not signal:
        return None

    signal_type, signal_value = signal
    resolution_record = {
        "ceo_id": ceo_id,
        "conversation_id": conversation_id,
        "source_interaction_id": source_interaction.id,
        "source_response_type": response_type,
        "gate_type": source_interaction.gate_type or "CLARIFICATION_REQUIRED",
        "question": str(selected_option.get("question") or gate.get("reason") or options[0].get("question") or "").strip(),
        "selected_option": {
            "label": str(selected_option.get("label") or "").strip(),
            "value": str(selected_option.get("value") or "").strip(),
            "apply_text": str(selected_option.get("apply_text") or "").strip(),
            "description": str(selected_option.get("description") or "").strip(),
        },
        "signal_type": signal_type,
        "signal_value": signal_value,
        "answer_text": answer_text,
        "match_strategy": match_strategy or "text_match",
    }
    database.record_clarification_answer(
        ceo_id,
        conversation_id,
        signal_type=signal_type,
        signal_value=signal_value,
        resolution=resolution_record,
    )
    from src.workflows.world_simulation import record_world_event

    record_world_event(
        ceo_id,
        domain="memory",
        event_type="clarification_resolved",
        description=f"Clarification resolved for {signal_type}.",
        source_ids=[str(source_interaction.id)],
        payload=resolution_record,
    )
    return {
        "signal_type": signal_type,
        "signal_value": signal_value,
        "option_value": str(selected_option.get("value") or ""),
        "option_label": str(selected_option.get("label") or ""),
        "match_strategy": match_strategy or "text_match",
    }

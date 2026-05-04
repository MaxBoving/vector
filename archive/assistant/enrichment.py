"""
Follow-up enrichment for the assistant pipeline.

Free functions that pre-process the incoming payload before classification:

  enrich_clarification_followup  — prepend the original question when the prior
                                   turn was a clarification response; signals
                                   that the clarification gate should be skipped.

  enrich_live_context_followup   — inject live-context state into the message
                                   when the CEO is continuing a prior schedule
                                   or report conversation.
"""
from __future__ import annotations

import json

from sqlmodel import Session, select

from src.api.schemas import AssistantQueryRequest
from src.core.models import SessionInteraction, User
from src.core.database import engine, get_or_create_live_context, get_previous_conversation_interaction, record_clarification_answer
from src.assistant.clarification_signals import OPTION_VALUE_TO_SIGNAL
from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.runner_semantics import classify_runner_semantics
from src.workflows.types import WorkflowType


def enrich_clarification_followup(
    *,
    payload: AssistantQueryRequest,
    interaction: SessionInteraction,
    current_user: User,
) -> tuple[AssistantQueryRequest, bool]:
    """
    If the previous interaction in this conversation was a clarification response,
    prepend the original query to the current message and return skip_clarification_gate=True.

    Returns (payload, skip_clarification_gate).
    """
    if not interaction.id:
        return payload, False
    try:
        # Primary path: conversation-linked lookup
        prev = (
            get_previous_conversation_interaction(
                current_user.ceo_id, payload.conversation_id, interaction.id
            )
            if payload.conversation_id
            else None
        )
        # Fallback: most recent prior interaction for this CEO with a clarification gate.
        # Handles cases where interactions aren't linked to conversation.interaction_ids.
        if prev is None:
            with Session(engine) as db:
                prev = db.exec(
                    select(SessionInteraction)
                    .where(
                        SessionInteraction.ceo_id == current_user.ceo_id,
                        SessionInteraction.id < interaction.id,
                        SessionInteraction.gate_type == "CLARIFICATION_REQUIRED",
                    )
                    .order_by(SessionInteraction.id.desc())
                    .limit(1)
                ).first()

        if prev and prev.response:
            resp = json.loads(prev.response)
            if resp.get("response_type") == "clarification":
                # Record the CEO's answer structurally before returning.
                if payload.conversation_id:
                    _record_clarification_response(
                        ceo_id=current_user.ceo_id,
                        conversation_id=payload.conversation_id,
                        ceo_message=payload.message,
                        prev_response=resp,
                    )
                original_query = resp.get("metadata", {}).get("original_query") or prev.query
                enriched = (
                    f"[Original question: {original_query}]\n\n"
                    f"CEO context: {payload.message}"
                )
                return payload.model_copy(update={"message": enriched}), True
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    return payload, False


def _record_clarification_response(
    *,
    ceo_id: str,
    conversation_id: str,
    ceo_message: str,
    prev_response: dict,
) -> None:
    """
    Match the CEO's reply against the structured options from the previous
    clarification response and persist the answer.

    Matching is purely structural: we score the CEO's message against each
    option's `value`, `label`, and `apply_text` fields using token overlap.
    No keyword lists — the options themselves are the vocabulary.
    """
    question_options = (prev_response.get("trust") or {}).get("question_options") or []
    clarification_offers = [
        qo for qo in question_options
        if isinstance(qo, dict) and qo.get("offer_type") == "clarification"
    ]
    if not clarification_offers:
        return

    message_tokens = _tokens(ceo_message)
    best_value: str | None = None
    best_score = 0.0

    for offer in clarification_offers:
        for option in (offer.get("options") or []):
            if not isinstance(option, dict):
                continue
            candidate_text = " ".join(filter(None, [
                str(option.get("value") or "").replace("_", " "),
                str(option.get("label") or ""),
                str(option.get("apply_text") or ""),
            ]))
            score = _token_overlap(message_tokens, _tokens(candidate_text))
            if score > best_score:
                best_score = score
                best_value = str(option.get("value") or "").strip()

    if not best_value or best_score < 0.15:
        return

    signal = OPTION_VALUE_TO_SIGNAL.get(best_value)
    if not signal:
        return

    signal_type, signal_value = signal
    try:
        record_clarification_answer(ceo_id, conversation_id, signal_type, signal_value)
    except Exception:
        pass  # Never block the pipeline for a preference write failure


def _tokens(text: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in str(text or ""))
    return {t for t in cleaned.split() if len(t) >= 3}


def _token_overlap(lhs: set[str], rhs: set[str]) -> float:
    if not lhs or not rhs:
        return 0.0
    return len(lhs & rhs) / max(1, len(rhs))


def enrich_live_context_followup(
    *,
    payload: AssistantQueryRequest,
    current_user: User,
) -> AssistantQueryRequest:
    """
    Inject live-context state into the CEO's message when it looks like a
    follow-up to a schedule or report conversation.

    Returns the (possibly enriched) payload unchanged if no enrichment applies.
    """
    if not payload.conversation_id:
        return payload
    live_context = get_or_create_live_context(current_user.ceo_id, payload.conversation_id).model_dump()
    if not _looks_like_live_context_followup(payload.message, live_context):
        return payload

    context_lines = ["[Conversation live context]"]
    schedule = live_context.get("current_schedule") or {}
    if isinstance(schedule, dict) and schedule:
        blocks = schedule.get("blocks") or []
        if blocks:
            block_labels = []
            for block in blocks[:5]:
                if isinstance(block, dict):
                    label = str(block.get("title") or "Untitled block")
                    window = str(block.get("time_window") or block.get("starts_at") or "").strip()
                    block_labels.append(f"{window} {label}".strip())
            if block_labels:
                context_lines.append("Schedule blocks: " + "; ".join(block_labels))
        meetings = schedule.get("meetings") or []
        if meetings:
            meeting_labels = []
            for meeting in meetings[:4]:
                if isinstance(meeting, dict):
                    meeting_labels.append(
                        f"{meeting.get('title', 'Meeting')} @ {meeting.get('starts_at', '')}".strip()
                    )
            if meeting_labels:
                context_lines.append("Meetings: " + "; ".join(meeting_labels))
        deadlines = schedule.get("deadlines") or []
        if deadlines:
            context_lines.append("Deadlines: " + "; ".join(str(item) for item in deadlines[:4]))
    decisions = live_context.get("open_decisions") or []
    if decisions:
        context_lines.append("Open decisions: " + "; ".join(str(item) for item in decisions[:3]))
    contributions = live_context.get("last_agent_contributions") or []
    if contributions:
        latest = contributions[-1]
        if isinstance(latest, dict):
            context_lines.append(
                f"Latest contribution [{latest.get('actor')} turn {latest.get('turn')}]: "
                f"{str(latest.get('content_summary') or '')[:180]}"
            )
    enriched_message = "\n".join(context_lines) + f"\n\nCEO follow-up: {payload.message}"

    workflow_hint = payload.workflow_hint
    if _looks_like_report_followup(payload.message, live_context):
        workflow_hint = WorkflowType.REPORT_GENERATION

    return payload.model_copy(
        update={
            "message": enriched_message,
            "workflow_hint": workflow_hint,
        }
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _looks_like_live_context_followup(message: str, live_context: dict) -> bool:
    return classify_runner_semantics(
        message=message,
        live_context=live_context,
    ).live_context_followup


def _looks_like_report_followup(message: str, live_context: dict) -> bool:
    return classify_runner_semantics(
        message=message,
        live_context=live_context,
    ).report_followup

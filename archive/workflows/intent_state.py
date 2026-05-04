"""
IntentState — conversational turn intent, resolved and persisted per-turn.

Each turn parses the CEO's message into an IntentState, merges it with the
previous turn's state (resolve_intent), and persists the result so the next
turn can read it via unified memory.

Exports
-------
  IntentState             — full turn intent model (Pydantic, persisted to DB)
  IntentDeliverable       — nested artifact delivery intent
  parse_turn_intent       — parse current turn from message + prior state
  resolve_intent          — merge prior state with current parsed state
  intent_state_from_payload — deserialize from DB payload
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from src.core.llm import LLMClient
from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.planning_types import RequestPlan
from src.workflows.request_planner import plan_request
from src.workflows.runner_semantics import classify_runner_semantics


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class IntentDeliverable(BaseModel):
    artifact_type: Optional[str] = None
    output_modality: Optional[str] = None


_VALID_MODES = frozenset(
    {"new_request", "continuation", "correction", "clarification_response"}
)


class IntentState(BaseModel):
    primary_intent: str = "report"
    mode: str = "new_request"
    execution_mode: str = "analysis"
    workflow_preference: Optional[str] = None
    task_topic: Optional[str] = None
    rationale: str = ""
    deliverable: IntentDeliverable = Field(default_factory=IntentDeliverable)
    timeframe: Optional[str] = None
    deadline: Optional[str] = None
    entities: List[str] = Field(default_factory=list)
    requested_actions: List[str] = Field(default_factory=list)
    must_do: List[str] = Field(default_factory=list)
    must_not_do: List[str] = Field(default_factory=list)
    rejected_offer_classes: List[str] = Field(default_factory=list)
    last_user_message: Optional[str] = None
    write_action_requested: bool = False


# ---------------------------------------------------------------------------
# LLM parse schema
# ---------------------------------------------------------------------------


class _TurnIntentParseOutput(BaseModel):
    mode: str = "new_request"
    primary_intent: str = "report"
    execution_mode: str = "analysis"
    task_topic: Optional[str] = None
    rationale: str = ""
    deliverable: dict = Field(default_factory=dict)
    timeframe: Optional[str] = None
    deadline: Optional[str] = None
    entities: List[str] = Field(default_factory=list)
    requested_actions: List[str] = Field(default_factory=list)
    must_do: List[str] = Field(default_factory=list)
    must_not_do: List[str] = Field(default_factory=list)
    write_action_requested: bool = False


_PARSE_SYSTEM_PROMPT = """\
Parse a CEO message into structured intent. Return JSON with these fields:
- mode: "new_request" | "continuation" | "correction" | "clarification_response"
- primary_intent: "report" | "analysis" | "action" | "clarification" | "scheduling"
- execution_mode: "analysis" | "execution"
- task_topic: brief topic label (string or null)
- rationale: one-sentence explanation of the parse
- deliverable: {"artifact_type": "board_brief" | "action_plan" | "email" | null}
- timeframe: time horizon if mentioned (string or null)
- deadline: deadline if mentioned (string or null)
- entities: list of named entities (people, companies, products)
- requested_actions: list of specific actions requested
- must_do: explicit requirements from the CEO
- must_not_do: explicit exclusions from the CEO
- write_action_requested: true only if CEO wants something sent, scheduled, or executed
Return ONLY valid JSON, no prose."""


def _parse_via_llm(
    message: str,
    previous_state: IntentState | None,
) -> _TurnIntentParseOutput | None:
    context_lines: list[str] = []
    if previous_state:
        context_lines.append(f"Previous mode: {previous_state.mode}")
        if previous_state.workflow_preference:
            context_lines.append(f"Previous workflow: {previous_state.workflow_preference}")
        if previous_state.task_topic:
            context_lines.append(f"Previous topic: {previous_state.task_topic}")

    context = "\n".join(context_lines)
    prompt = (
        f"Context:\n{context}\n\nCEO message: {message}"
        if context
        else f"CEO message: {message}"
    )
    try:
        return LLMClient().complete_structured(
            prompt=prompt,
            response_model=_TurnIntentParseOutput,
            system_prompt=_PARSE_SYSTEM_PROMPT,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Plan-based fallback (no LLM)
# ---------------------------------------------------------------------------


def _fallback_from_plan(
    message: str,
    plan: RequestPlan,
    previous_state: IntentState | None,
) -> IntentState:
    """Derive IntentState from the request planner when LLM is unavailable."""
    workflow_preference = (
        str(plan.direct_workflow or plan.target_workflow or "").strip() or None
    )
    runner_signals = classify_runner_semantics(message=message)
    write_action_requested = runner_signals.explicit_execution_request

    if previous_state is None:
        mode = "new_request"
    elif write_action_requested and previous_state.workflow_preference:
        mode = "correction"
    else:
        mode = "continuation"

    return IntentState(
        workflow_preference=workflow_preference,
        mode=mode,
        write_action_requested=write_action_requested,
        last_user_message=extract_visible_request_text(message),
        rejected_offer_classes=list(previous_state.rejected_offer_classes)
        if previous_state and mode in {"continuation", "correction"}
        else [],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_turn_intent(
    *,
    message: str,
    previous_state: IntentState | None,
    artifact_context: dict[str, Any] | None,
    precomputed_request_plan: RequestPlan | None = None,
) -> IntentState:
    """Parse the current CEO message into an IntentState.

    Always derives workflow_preference from the request planner (authoritative).
    Uses an LLM call for nuanced fields (mode, topic, entities, deliverable).
    Falls back to plan-derived defaults when the LLM is unavailable.
    """
    visible_message = extract_visible_request_text(message)
    plan = precomputed_request_plan or plan_request(visible_message)
    workflow_preference = (
        str(plan.direct_workflow or plan.target_workflow or "").strip() or None
    )

    llm_output = _parse_via_llm(visible_message, previous_state)
    if llm_output is None:
        return _fallback_from_plan(message, plan, previous_state)

    mode = llm_output.mode if llm_output.mode in _VALID_MODES else (
        "continuation" if previous_state else "new_request"
    )
    deliverable_raw = llm_output.deliverable or {}
    deliverable = IntentDeliverable(artifact_type=deliverable_raw.get("artifact_type"))
    carry_rejected = (
        list(previous_state.rejected_offer_classes)
        if previous_state and mode in {"continuation", "correction"}
        else []
    )
    merged_rejected = sorted(
        {*carry_rejected, *[str(r) for r in (llm_output.must_not_do or []) if str(r)]}
    )

    return IntentState(
        primary_intent=llm_output.primary_intent or "report",
        mode=mode,
        execution_mode=llm_output.execution_mode or "analysis",
        workflow_preference=workflow_preference,
        task_topic=llm_output.task_topic,
        rationale=llm_output.rationale,
        deliverable=deliverable,
        timeframe=llm_output.timeframe,
        deadline=llm_output.deadline,
        entities=[str(e) for e in (llm_output.entities or []) if str(e)],
        requested_actions=[str(a) for a in (llm_output.requested_actions or []) if str(a)],
        must_do=[str(d) for d in (llm_output.must_do or []) if str(d)],
        must_not_do=[str(d) for d in (llm_output.must_not_do or []) if str(d)],
        rejected_offer_classes=merged_rejected,
        last_user_message=visible_message,
        write_action_requested=llm_output.write_action_requested,
    )


def resolve_intent(
    previous_state: IntentState | None,
    parsed: IntentState,
    artifact_context: dict[str, Any] | None,
    *,
    conversation_id: str | None,
    last_user_message: str,
) -> IntentState:
    """Merge prior IntentState with newly parsed state.

    In continuation/correction modes, accumulate fields that build up across
    turns (rejected offers, entity set). Otherwise the new state wins.
    """
    is_followup = parsed.mode in {"continuation", "correction", "clarification_response"}

    if not is_followup or previous_state is None:
        return parsed.model_copy(update={"last_user_message": last_user_message})

    merged_rejected = sorted(
        {*previous_state.rejected_offer_classes, *parsed.rejected_offer_classes}
    )
    merged_entities = sorted(
        {*previous_state.entities, *parsed.entities}
    )
    return parsed.model_copy(
        update={
            "rejected_offer_classes": merged_rejected,
            "entities": merged_entities,
            "last_user_message": last_user_message,
        }
    )


def intent_state_from_payload(payload: dict[str, Any] | None) -> IntentState | None:
    """Deserialise a persisted IntentState from DB payload (result of model_dump)."""
    if not isinstance(payload, dict) or not payload:
        return None
    try:
        raw = dict(payload)
        deliverable_raw = raw.get("deliverable")
        if isinstance(deliverable_raw, dict):
            raw["deliverable"] = IntentDeliverable(**deliverable_raw)
        return IntentState(**raw)
    except Exception:
        return None

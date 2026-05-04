"""
Memory loading, building, and persistence for the assistant pipeline.

Free functions that isolate all unified-memory and intent-state I/O so that
the runner's orchestration method does not import database modules directly.

  load_previous_intent_state       — fetch and deserialise latest IntentState
  persist_intent_state             — write resolved IntentState back to the DB
  build_and_persist_unified_memory — assemble UnifiedMemoryState and persist it
  persist_pending_actions          — infer + merge pending actions into LiveContext
  build_artifact_context           — read previous response and build artifact context dict
"""
from __future__ import annotations

import json
from typing import Any

from src.core.database import (
    get_or_create_live_context,
    get_ceo_preferences,
    get_ceo_memories,
    get_recent_signals,
    get_or_create_situational_profile,
    get_latest_intent_state,
    get_previous_conversation_interaction,
    get_recent_conversation_interactions,
    persist_latest_intent_state,
    persist_latest_unified_memory,
    update_live_context,
)
from src.core.models import normalize_preferences_payload
from src.workflows.action_references import infer_pending_actions_from_response, merge_pending_actions
from src.workflows.intent_state import IntentState, intent_state_from_payload
from src.workflows.unified_memory import build_unified_memory_state
from src.api.schemas import AssistantMessageResponse


def load_previous_intent_state(
    *,
    ceo_id: str,
    conversation_id: str | None,
) -> IntentState | None:
    """Fetch and deserialise the latest IntentState for this conversation."""
    if not conversation_id:
        return None
    return intent_state_from_payload(get_latest_intent_state(ceo_id, conversation_id))


def persist_intent_state(
    *,
    ceo_id: str,
    conversation_id: str | None,
    intent_state: IntentState,
) -> None:
    """Write resolved IntentState back to the DB."""
    if not conversation_id:
        return
    persist_latest_intent_state(
        ceo_id=ceo_id,
        conversation_id=conversation_id,
        intent_state=intent_state.model_dump(mode="json"),
    )


def build_and_persist_unified_memory(
    *,
    ceo_id: str,
    conversation_id: str | None,
    resolved_intent: IntentState,
    conversation_history: list[dict],
    artifact_context: dict[str, object],
    live_context_override: dict[str, Any] | None = None,
    resolved_action_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Assemble the UnifiedMemoryState from all available sources, persist it, and
    return it as a plain dict.  Returns {} when no conversation_id is set.
    """
    if not conversation_id:
        return {}
    live_context = (
        live_context_override
        or get_or_create_live_context(ceo_id, conversation_id).model_dump(mode="json")
    )
    preferences = normalize_preferences_payload(get_ceo_preferences(ceo_id))
    situational_profile = get_or_create_situational_profile(ceo_id).model_dump(mode="json")
    ceo_memories = [memory.model_dump(mode="json") for memory in get_ceo_memories(ceo_id, limit=12)]
    recent_signals = [signal.model_dump(mode="json") for signal in get_recent_signals(ceo_id, limit=6)]
    recent_history = [
        {
            "query": str(item.get("content") or "")[:220],
            "response": "",
            "timestamp": "",
        }
        for item in (conversation_history or [])
        if item.get("role") == "user"
    ]
    unified_memory = build_unified_memory_state(
        resolved_intent=resolved_intent.model_dump(mode="json"),
        conversation_id=conversation_id,
        live_context=live_context,
        preferences=preferences,
        situational_profile=situational_profile,
        ceo_memories=ceo_memories,
        recent_history=recent_history,
        artifact_context=artifact_context,
        signals=recent_signals,
        resolved_action_reference=resolved_action_reference,
    )
    persist_latest_unified_memory(
        ceo_id=ceo_id,
        conversation_id=conversation_id,
        unified_memory=unified_memory.model_dump(mode="json"),
    )
    return unified_memory.model_dump(mode="json")


def persist_pending_actions(
    *,
    ceo_id: str,
    conversation_id: str | None,
    interaction_id: int | None,
    response: AssistantMessageResponse,
) -> None:
    """Infer new pending actions from the response and merge them into LiveContext."""
    if not conversation_id:
        return
    live_context = get_or_create_live_context(ceo_id, conversation_id).model_dump(mode="json")
    merged = merge_pending_actions(
        existing=live_context.get("pending_actions") if isinstance(live_context, dict) else [],
        new_actions=infer_pending_actions_from_response(response=response, interaction_id=interaction_id),
    )
    update_live_context(
        conversation_id,
        ceo_id=ceo_id,
        pending_actions=merged,
    )


def build_artifact_context(
    *,
    ceo_id: str,
    conversation_id: str | None,
    current_interaction_id: int | None,
) -> dict[str, object]:
    """Read the previous interaction response and build the artifact context dict."""
    if not conversation_id or not current_interaction_id:
        return {}
    previous = get_previous_conversation_interaction(ceo_id, conversation_id, current_interaction_id)
    if not previous or not previous.response:
        return {}
    try:
        response = json.loads(previous.response)
    except (json.JSONDecodeError, TypeError):
        return {}
    return {
        "previous_workflow_type": response.get("workflow_type"),
        "previous_response_type": response.get("response_type"),
        "previous_artifacts": [
            artifact.get("artifact_type")
            for artifact in (response.get("artifacts") or [])
            if isinstance(artifact, dict) and artifact.get("artifact_type")
        ],
        "previous_title": (response.get("answer") or {}).get("title"),
        "previous_summary": (response.get("answer") or {}).get("summary"),
    }


def build_conversation_history(
    *,
    ceo_id: str,
    conversation_id: str | None,
    current_interaction_id: int | None,
) -> list[dict]:
    """Fetch the last 3 turns and format them for the LLM router prompt."""
    if not conversation_id or not current_interaction_id:
        return []
    try:
        recent = get_recent_conversation_interactions(
            ceo_id, conversation_id, current_interaction_id, limit=3
        )
        history: list[dict] = []
        for item in recent:
            if item.query:
                history.append({"role": "user", "content": item.query})
            if item.response:
                try:
                    resp = json.loads(item.response)
                    resp_type = resp.get("response_type", "")
                    workflow_type = resp.get("workflow_type", "")
                    summary = (resp.get("answer") or {}).get("summary", "")[:200]
                    label = f"[{resp_type}/{workflow_type}]" if workflow_type else f"[{resp_type}]"
                    history.append({"role": "assistant", "content": f"{label} {summary}"})
                except (json.JSONDecodeError, AttributeError):
                    pass
        return history
    except Exception:
        return []

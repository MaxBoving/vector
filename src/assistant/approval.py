"""Approval gate for write actions.

Write actions are never auto-executed. When the agent decides to call a write
tool, the action is stored in ConversationLiveContext.pending_actions. The CEO
approves or rejects via the /resolve endpoint, which calls execute_approval()
or reject_approval() here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.core.database import get_or_create_live_context, update_live_context
from src.tools.base import ToolContext
from src.assistant.sdk_tools import WRITE_TOOL_NAMES, execute_tool


def is_write_tool(tool_name: str) -> bool:
    return tool_name in WRITE_TOOL_NAMES


def store_pending_action(
    *,
    ceo_id: str,
    conversation_id: str,
    tool_name: str,
    tool_inputs: dict[str, Any],
    interaction_id: int,
) -> None:
    """Persist a pending write action in the conversation live context."""
    ctx = get_or_create_live_context(ceo_id, conversation_id)
    existing = list(ctx.pending_actions or [])
    existing.append({
        "tool_name": tool_name,
        "tool_inputs": tool_inputs,
        "interaction_id": interaction_id,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    })
    update_live_context(conversation_id, ceo_id=ceo_id, pending_actions=existing)


def execute_approval(
    *,
    ceo_id: str,
    conversation_id: str,
    interaction_id: int,
) -> dict[str, Any]:
    """Execute the pending write action for this interaction and mark it done."""
    ctx = get_or_create_live_context(ceo_id, conversation_id)
    pending = [a for a in (ctx.pending_actions or []) if a.get("interaction_id") == interaction_id]
    if not pending:
        raise ValueError(f"No pending action for interaction_id={interaction_id}")

    action = pending[0]
    context = ToolContext(ceo_id=ceo_id, interaction_id=interaction_id)
    result = execute_tool(action["tool_name"], action["tool_inputs"], context)

    updated = [
        {**a, "status": "executed"} if a.get("interaction_id") == interaction_id else a
        for a in (ctx.pending_actions or [])
    ]
    update_live_context(conversation_id, ceo_id=ceo_id, pending_actions=updated)
    return {"executed": action["tool_name"], "result": result}


def reject_approval(
    *,
    ceo_id: str,
    conversation_id: str,
    interaction_id: int,
) -> None:
    """Mark the pending action as rejected."""
    ctx = get_or_create_live_context(ceo_id, conversation_id)
    updated = [
        {**a, "status": "rejected"} if a.get("interaction_id") == interaction_id else a
        for a in (ctx.pending_actions or [])
    ]
    update_live_context(conversation_id, ceo_id=ceo_id, pending_actions=updated)

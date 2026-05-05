"""Agentic assistant — replaces the routing/workflow pipeline.

One Claude call with tools. Read tools execute in the loop. Write tools
short-circuit and are stored for CEO approval via approval.py.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import anthropic

from src.api.schemas import (
    AnswerPayload,
    AssistantMessageResponse,
    AssistantQueryRequest,
    TrustMetadata,
)
from src.core.database import get_ceo_preferences, get_session_history
from src.core.models import SessionInteraction, User
from src.tools.base import ToolContext
from src.assistant.approval import is_write_tool, store_pending_action
from src.assistant.formatter import ResponseFormatter
from src.assistant.sdk_tools import execute_tool, get_anthropic_tools

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")
_MAX_TOOL_ITERATIONS = 10


class AgenticAssistant:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic()
        self._formatter = ResponseFormatter()

    async def handle(
        self,
        *,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        current_user: User,
    ) -> AssistantMessageResponse:
        ceo_id = current_user.ceo_id
        context = ToolContext(
            ceo_id=ceo_id,
            interaction_id=interaction.id,
            company_name=current_user.company_name,
            conversation_id=payload.conversation_id,
        )
        system_prompt = await asyncio.to_thread(self._build_system_prompt, current_user)
        history = await asyncio.to_thread(self._load_history, ceo_id)
        messages: list[dict[str, Any]] = history + [{"role": "user", "content": payload.message}]
        tools = get_anthropic_tools()

        final_text, pending_action, tools_called = self._run_tool_loop(messages, system_prompt, tools, context)

        if pending_action:
            store_pending_action(
                ceo_id=ceo_id,
                conversation_id=payload.conversation_id,
                tool_name=pending_action["tool_name"],
                tool_inputs=pending_action["tool_inputs"],
                interaction_id=interaction.id,
            )

        answer = await asyncio.to_thread(self._formatter.format, final_text, tools_called)

        return self._build_response(
            payload=payload,
            interaction=interaction,
            answer=answer,
            pending_action=pending_action,
        )

    def _run_tool_loop(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        context: ToolContext,
    ) -> tuple[str, dict[str, Any] | None, list[str]]:
        """Run the tool loop. Returns (final_text, pending_action_or_None, tools_called)."""
        tools_called: list[str] = []

        for _ in range(_MAX_TOOL_ITERATIONS):
            response = self._client.messages.create(
                model=_MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

            text = next((b.text for b in response.content if hasattr(b, "text")), "")

            if response.stop_reason == "end_turn":
                return text, None, tools_called

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return text, None, tools_called

            # Surface the first write tool — if Claude batches multiple write tools in one
            # response, only the first is shown to the CEO. This is intentional: approval
            # is per-interaction, and batching multiple write actions in one turn is rare.
            for tool_use in tool_uses:
                if is_write_tool(tool_use.name):
                    tools_called.append(tool_use.name)
                    return text, {"tool_name": tool_use.name, "tool_inputs": tool_use.input}, tools_called

            # Execute read tools and continue
            messages = messages + [{"role": "assistant", "content": response.content}]
            tool_results = []
            for tool_use in tool_uses:
                tools_called.append(tool_use.name)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": execute_tool(tool_use.name, tool_use.input, context),
                })
            messages = messages + [{"role": "user", "content": tool_results}]

        return "Reached tool iteration limit. Please try a more specific question.", None, tools_called

    def _build_system_prompt(self, user: User) -> str:
        prefs = get_ceo_preferences(user.ceo_id)
        company = user.company_name or "your company"
        lines = [
            f"You are an executive AI assistant for the CEO of {company}.",
            "Be direct, concise, and executive-facing. No filler, no preamble.",
            "",
            "## When to use tools",
            "- Email or inbox questions → read_email_threads",
            "- Schedule or meeting questions → read_calendar_events",
            "- What to focus on, priorities, open items → get_live_context, get_recent_signals",
            "- Company metrics, financials, or strategy → get_company_state",
            "- Specific person or company mentioned → get_entity_context",
            "- Past conversations or prior decisions → get_thread_entries, semantic_search",
            "- After answering, note key decisions/commitments the CEO made → write_thread_entry",
            "- Observe a recurring pressure or mode shift → update_situational_profile",
            "- CRM deals or pipeline → crm_deal_context",
            "- Slack channels or messages → slack_read",
            "",
            "Call tools before answering when the question needs real data. "
            "Don't speculate — if you need data, fetch it.",
            "",
            "## Write actions",
            "send_email_draft, slack_post, and create_calendar_event require CEO approval — call the tool and it will be shown for confirmation.",
            "Document creation (create_docx_memo, create_pptx_deck, create_workbook, create_canvas) executes immediately.",
            "- To save something the CEO said for future sessions → memory_management with action=save",
        ]
        if prefs and prefs.priority_senders:
            lines.append(f"\nPriority senders: {', '.join(list(prefs.priority_senders)[:5])}")
        if prefs and prefs.ignored_senders:
            lines.append(f"Deprioritize emails from: {', '.join(list(prefs.ignored_senders)[:5])}")
        return "\n".join(lines)

    def _load_history(self, ceo_id: str) -> list[dict[str, Any]]:
        """Load last 5 turns as Anthropic message format."""
        recent = get_session_history(ceo_id, limit=10)
        messages: list[dict[str, Any]] = []
        for interaction in recent[-5:]:
            if interaction.query:
                messages.append({"role": "user", "content": interaction.query})
            if interaction.response:
                messages.append({"role": "assistant", "content": interaction.response})
        return messages

    def _build_response(
        self,
        *,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        answer: AnswerPayload,
        pending_action: dict[str, Any] | None,
    ) -> AssistantMessageResponse:
        metadata: dict[str, Any] = {}
        if pending_action:
            metadata["pending_action"] = pending_action

        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=str(interaction.id),
            workflow_type="conversational",
            response_type="conversational",
            status="pending" if pending_action else "completed",
            answer=answer,
            trust=TrustMetadata(),
            metadata=metadata,
        )

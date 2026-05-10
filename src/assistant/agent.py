"""Agentic assistant — replaces the routing/workflow pipeline.

One Claude call with tools. Read tools execute in the loop. Write tools
short-circuit and are stored for CEO approval via approval.py.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any, Literal

import anthropic

from src.api.schemas import (
    AnswerPayload,
    AssistantMessageResponse,
    AssistantQueryRequest,
    MessagePresentation,
    TrustMetadata,
)
from src.core.database import get_ceo_preferences, get_session_history
from src.core.models import SessionInteraction, User
from src.tools.base import ToolContext
from src.assistant.approval import is_write_tool, store_pending_action
from src.assistant.formatter import ResponseFormatter, TOOL_LABELS
from src.assistant.sdk_tools import execute_tool, get_anthropic_tools
from src.workflows.request_planner import plan_request

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")
_FAST_MODEL = os.getenv("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5-20251001")
_MAX_TOOL_ITERATIONS = 10

QueryComplexity = Literal["conversational", "data_required"]

# Human-readable names for preamble ("Built from your inbox and calendar.")
_PREAMBLE_SOURCE_NAMES: dict[str, str] = {
    "email":          "inbox",
    "calendar":       "calendar",
    "signals":        "company signals",
    "CRM / pipeline": "pipeline data",
    "Slack":          "Slack",
    "Google Drive":   "Drive",
}

# Response types that warrant a tool-coverage preamble
_PREAMBLE_RESPONSE_TYPES = {"morning_brief", "inbox_summary", "calendar_summary"}

_WORKFLOW_RESPONSE_CONTRACTS: dict[str, tuple[str, str | None]] = {
    "report_generation": ("report", "report"),
    "document_explanation": ("explanation", "report"),
    "email_ingestion": ("brief", "brief"),
    "email_watcher": ("brief", "brief"),
    "calendar_briefing": ("brief", "calendar"),
    "morning_brief": ("brief", "brief"),
    "schedule_planning": ("schedule", "schedule"),
    "meeting_prep": ("brief", "brief"),
    "weekly_recap": ("brief", "brief"),
    "conversational": ("conversational", None),
}

_WORKFLOW_TITLES: dict[str, str] = {
    "report_generation": "Executive Report",
    "document_explanation": "Business Implication Brief",
    "email_ingestion": "Inbox Brief",
    "email_watcher": "Inbox Brief",
    "calendar_briefing": "Calendar Brief",
    "morning_brief": "Morning Brief",
    "schedule_planning": "Executive Schedule",
    "meeting_prep": "Meeting Brief",
    "weekly_recap": "Week in Review",
}


def _build_preamble(
    response_type: str,
    tools_called: list[str],
    missing_context: list[str],
) -> str | None:
    """Deterministic one-liner: 'Built from your inbox and calendar. CRM unavailable.'

    Reads which tools returned data vs. which are in missing_context.
    No LLM call — purely from trust metadata already computed.
    """
    if response_type not in _PREAMBLE_RESPONSE_TYPES:
        return None

    missing_labels = {mc.split(":")[0] for mc in missing_context}

    seen: set[str] = set()
    connected: list[str] = []
    for tool in tools_called:
        label = TOOL_LABELS.get(tool)
        if not label or label in missing_labels or label in seen:
            continue
        seen.add(label)
        name = _PREAMBLE_SOURCE_NAMES.get(label)
        if name:
            connected.append(name)

    if not connected:
        return None

    if len(connected) == 1:
        source_str = connected[0]
    elif len(connected) == 2:
        source_str = f"{connected[0]} and {connected[1]}"
    else:
        source_str = ", ".join(connected[:-1]) + f", and {connected[-1]}"

    line = f"Built from your {source_str}."

    unavailable = [
        _PREAMBLE_SOURCE_NAMES[ml]
        for ml in sorted(missing_labels)
        if ml in _PREAMBLE_SOURCE_NAMES
    ]
    if unavailable:
        noun = unavailable[0] if len(unavailable) == 1 else ", ".join(unavailable[:-1]) + f" and {unavailable[-1]}"
        line += f" {noun.capitalize()} unavailable."

    return line

_COMPLEXITY_CLASSIFIER_PROMPT = """\
Classify the following CEO query with exactly ONE word.

conversational — The response can come from general knowledge or recent conversation history alone. No live business data needed.
data_required  — The response requires fetching live data: emails, calendar, CRM deals, documents, Slack, signals, or metrics.

Output ONLY the word: conversational  OR  data_required
No explanation.

Query: {message}"""


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
        request_plan = await asyncio.to_thread(
            plan_request,
            payload.message,
            has_attachments=bool(payload.attachments),
        )
        system_prompt, fast_system_prompt, history, query_complexity = await asyncio.gather(
            asyncio.to_thread(self._build_system_prompt, current_user, request_plan, payload.attachments),
            asyncio.to_thread(self._build_fast_system_prompt, current_user),
            asyncio.to_thread(self._load_history, ceo_id),
            self._classify_query(payload.message),
        )
        messages: list[dict[str, Any]] = history + [{"role": "user", "content": payload.message}]
        tools = get_anthropic_tools()

        if query_complexity == "conversational":
            final_text, pending_action, tools_called, tool_results = await asyncio.to_thread(
                self._run_fast_response, messages, fast_system_prompt
            )
        else:
            final_text, pending_action, tools_called, tool_results = self._run_tool_loop(messages, system_prompt, tools, context)

        if pending_action:
            store_pending_action(
                ceo_id=ceo_id,
                conversation_id=payload.conversation_id,
                tool_name=pending_action["tool_name"],
                tool_inputs=pending_action["tool_inputs"],
                interaction_id=interaction.id,
            )

        answer, trust, response_type = await asyncio.to_thread(self._formatter.format, final_text, tools_called, tool_results)
        workflow_type = self._resolve_workflow_type(
            request_plan=request_plan,
            query_complexity=query_complexity,
            tools_called=tools_called,
            response_type=response_type,
        )
        preamble = _build_preamble(response_type, tools_called, trust.missing_context)
        answer = self._normalize_answer(answer, workflow_type=workflow_type, final_text=final_text)

        return self._build_response(
            payload=payload,
            interaction=interaction,
            answer=answer,
            trust=trust,
            pending_action=pending_action,
            workflow_type=workflow_type,
            preamble=preamble,
        )

    def _run_tool_loop(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        context: ToolContext,
    ) -> tuple[str, dict[str, Any] | None, list[str], dict[str, str]]:
        """Run the tool loop. Returns (final_text, pending_action_or_None, tools_called, tool_results_by_name)."""
        tools_called: list[str] = []
        tool_results_by_name: dict[str, str] = {}

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
                return text, None, tools_called, tool_results_by_name

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return text, None, tools_called, tool_results_by_name

            # Surface the first write tool — if Claude batches multiple write tools in one
            # response, only the first is shown to the CEO. This is intentional: approval
            # is per-interaction, and batching multiple write actions in one turn is rare.
            for tool_use in tool_uses:
                if is_write_tool(tool_use.name):
                    tools_called.append(tool_use.name)
                    return text, {"tool_name": tool_use.name, "tool_inputs": tool_use.input}, tools_called, tool_results_by_name

            # Execute read tools and continue
            messages = messages + [{"role": "assistant", "content": response.content}]
            tool_results = []
            for tool_use in tool_uses:
                tools_called.append(tool_use.name)
                result_str = execute_tool(tool_use.name, tool_use.input, context)
                tool_results_by_name[tool_use.name] = result_str
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result_str,
                })
            messages = messages + [{"role": "user", "content": tool_results}]

        return "Reached tool iteration limit. Please try a more specific question.", None, tools_called, tool_results_by_name

    async def _classify_query(self, message: str) -> QueryComplexity:
        """Classify the query complexity with a fast Haiku call.

        Runs in parallel with _build_system_prompt and _load_history so it adds
        zero latency to the critical path.
        """
        prompt = _COMPLEXITY_CLASSIFIER_PROMPT.format(message=message[:400])
        try:
            response = await asyncio.to_thread(
                self._client.messages.create,
                model=_FAST_MODEL,
                max_tokens=8,
                messages=[{"role": "user", "content": prompt}],
            )
            label = next((b.text for b in response.content if hasattr(b, "text")), "").strip().lower()
            if "conversational" in label:
                return "conversational"
        except Exception:
            pass
        return "data_required"

    def _run_fast_response(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
    ) -> tuple[str, dict[str, Any] | None, list[str], dict[str, str]]:
        """Single Haiku call with no tools — used for conversational queries."""
        response = self._client.messages.create(
            model=_FAST_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
        text = next((b.text for b in response.content if hasattr(b, "text")), "")
        return text, None, [], {}

    def _build_fast_system_prompt(self, user: User) -> str:
        """Minimal prompt for conversational (no-tool) Haiku responses.

        Omits all tool guidance — the fast path has no tools available, so
        including tool instructions causes Haiku to emit fake XML tool calls.
        """
        company = user.company_name or "your company"
        today = date.today().strftime("%A, %B %-d, %Y")
        return "\n".join([
            f"You are a conversational AI assistant for the CEO of {company}.",
            f"Today is {today}.",
            "Answer plainly and directly. No executive-report framing, no invented titles, no filler.",
            "When relevant, mention concrete numbers and tradeoffs, but keep the tone conversational.",
        ])

    def _build_system_prompt(
        self,
        user: User,
        request_plan: Any | None = None,
        attachments: list[Any] | None = None,
    ) -> str:
        prefs = get_ceo_preferences(user.ceo_id)
        company = user.company_name or "your company"
        today = date.today().strftime("%A, %B %-d, %Y")
        lines = [
            f"You are an executive AI assistant for the CEO of {company}.",
            f"Today is {today}. Use this as the reference point for all date and time references.",
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
            "## Saving facts for later",
            "When you encounter specific facts in tool results — offer terms, deal amounts, deadlines, "
            "commitments, names, decisions — save them with memory_management action=save. "
            "Be specific: include names, amounts, and dates in the memory text. "
            "Do not save vague summaries. Save the fact itself: "
            "'VP Engineering offer: $195K base, 0.6% options, competing offer expires Monday May 11.'",
            "",
            "## Answering follow-up questions",
            "When the CEO asks about something mentioned in a prior response:",
            "1. Check semantic_search first.",
            "2. If not found, identify which tool produced the original data and re-read it "
            "(e.g. read_email_threads to get the full thread that mentioned the offer).",
            "3. Never say you don't know when you could re-fetch the source.",
            "",
            "## Write actions",
            "send_email_draft, slack_post, and create_calendar_event require CEO approval — call the tool and it will be shown for confirmation.",
            "Document creation (create_docx_memo, create_pptx_deck, create_workbook, create_canvas) executes immediately. "
            "Use create_docx_memo when drafting anything the CEO will share externally (memos, proposals, narratives).",
            "- To save something the CEO said for future sessions → memory_management with action=save",
            "",
            "When answering decisions or tradeoffs, include the key numbers — charts help communicate options and actions clearly.",
        ]
        plan_workflow = str(getattr(request_plan, "target_workflow", "") or getattr(request_plan, "direct_workflow", "") or "").strip()
        if plan_workflow:
            lines.append("")
            lines.append(f"Current request workflow: {plan_workflow}. Keep the response in that workflow lane.")
            if plan_workflow == "document_explanation":
                attachment_lines: list[str] = []
                for attachment in attachments or []:
                    if isinstance(attachment, dict):
                        filename = str(attachment.get("filename") or attachment.get("document_id") or "").strip()
                        if filename:
                            attachment_lines.append(filename)
                if attachment_lines:
                    lines.append(f"Referenced attachments: {', '.join(attachment_lines[:5])}.")
                lines.append("Treat provided attachments as primary source material; do not ask for an upload when attachments are already present.")
            elif plan_workflow == "schedule_planning":
                lines.append("This is an executive schedule-planning request, not a morning brief.")
            elif plan_workflow == "meeting_prep":
                lines.append("This is meeting preparation for a specific meeting, not a general brief.")
            elif plan_workflow == "weekly_recap":
                lines.append("This is a backward-looking weekly recap, not a morning brief.")
            elif plan_workflow == "calendar_briefing":
                lines.append("This is a calendar briefing, not a morning brief.")
        if prefs and prefs.priority_senders:
            lines.append(f"\nPriority senders: {', '.join(list(prefs.priority_senders)[:5])}")
        if prefs and prefs.ignored_senders:
            lines.append(f"Deprioritize emails from: {', '.join(list(prefs.ignored_senders)[:5])}")
        return "\n".join(lines)

    def _load_history(self, ceo_id: str) -> list[dict[str, Any]]:
        """Load last 5 turns as Anthropic message format."""
        import json as _json
        recent = get_session_history(ceo_id, limit=5)
        messages: list[dict[str, Any]] = []
        for interaction in recent:
            if interaction.query:
                messages.append({"role": "user", "content": interaction.query})
            if interaction.response:
                # response may be a full AssistantMessageResponse JSON blob — extract prose
                text = interaction.response
                try:
                    parsed = _json.loads(text)
                    if isinstance(parsed, dict) and "answer" in parsed:
                        answer = parsed["answer"]
                        # Prefer summary (one-liner); fall back to title
                        text = answer.get("summary") or answer.get("title") or text
                except (ValueError, KeyError):
                    pass
                messages.append({"role": "assistant", "content": text})
        return messages

    # Maps formatter response_type → (workflow_type, response_type_schema, presentation_mode)
    _RESPONSE_TYPE_MAP: dict[str, tuple[str, str, str | None]] = {
        "morning_brief":    ("morning_brief",    "brief",          "brief"),
        "inbox_summary":    ("email_ingestion",  "brief",          "brief"),
        "calendar_summary": ("calendar_briefing","brief",          "calendar"),
        "pipeline_summary": ("conversational",   "report",         "report"),
        "entity_lookup":    ("conversational",   "conversational", None),
        "conversational":   ("conversational",   "conversational", None),
    }

    def _build_response(
        self,
        *,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        answer: AnswerPayload,
        trust: TrustMetadata,
        pending_action: dict[str, Any] | None,
        workflow_type: str = "conversational",
        preamble: str | None = None,
    ) -> AssistantMessageResponse:
        answer = self._apply_answer_title_contract(answer, workflow_type=workflow_type)
        metadata: dict[str, Any] = {}
        if pending_action:
            metadata["pending_action"] = pending_action

        schema_response_type, pres_mode = _WORKFLOW_RESPONSE_CONTRACTS.get(
            workflow_type,
            ("conversational", None),
        )
        presentation = MessagePresentation(mode=pres_mode, preamble=preamble) if (pres_mode or preamble) else None  # type: ignore[arg-type]
        if presentation and pres_mode and answer.summary:
            presentation.summary = answer.summary

        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=str(interaction.id),
            workflow_type=workflow_type,  # type: ignore[arg-type]
            response_type=schema_response_type,  # type: ignore[arg-type]
            status="pending" if pending_action else "completed",
            answer=answer,
            trust=trust,
            presentation=presentation,
            metadata=metadata,
        )

    def _apply_answer_title_contract(self, answer: AnswerPayload, *, workflow_type: str) -> AnswerPayload:
        generic_titles = {
            "Morning Brief",
            "Executive Report",
            "Calendar Brief",
            "Inbox Brief",
            "Meeting Brief",
            "Week in Review",
        }
        title = answer.title.strip()
        if workflow_type == "conversational":
            title = ""
        elif not title or title in generic_titles:
            title = _WORKFLOW_TITLES.get(workflow_type, "") or workflow_type.replace("_", " ").title()
        return answer.model_copy(update={"title": title})

    def _resolve_workflow_type(
        self,
        *,
        request_plan: Any,
        query_complexity: QueryComplexity,
        tools_called: list[str],
        response_type: str,
    ) -> str:
        if query_complexity == "conversational":
            if request_plan.direct_workflow == "report_generation" and not request_plan.is_compound:
                return "conversational"
            if request_plan.direct_workflow in {"schedule_planning", "meeting_prep", "weekly_recap", "morning_brief", "calendar_briefing", "document_explanation", "email_watcher", "email_ingestion"}:
                return str(request_plan.direct_workflow)
            return "conversational"

        if request_plan.is_compound:
            return str(request_plan.target_workflow)

        if request_plan.direct_workflow:
            return str(request_plan.direct_workflow)

        if response_type == "morning_brief" and set(tools_called) & {"read_email_threads", "read_calendar_events", "get_recent_signals"}:
            return "morning_brief"

        return "report_generation"

    def _normalize_answer(self, answer: AnswerPayload, *, workflow_type: str, final_text: str) -> AnswerPayload:
        summary = answer.summary.strip()
        if not summary:
            summary = final_text.strip() or ("Working on your request." if workflow_type != "conversational" else "")
        return answer.model_copy(update={"summary": summary})

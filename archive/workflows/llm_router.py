"""
LLM-first intent router.

Makes a single fast Haiku call to classify the user's message by SEMANTIC
INTENT — understanding what the user actually wants, using conversation
context to resolve references like "this meeting" or "that report".

Falls back to None on failure or low confidence so runner.py can fall
through to the keyword-based classifier.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from pydantic import BaseModel, Field

from src.core.llm import LLMClient
from src.workflows.routing import RouteFamily
from src.workflows.types import WorkflowType

# ── model ──────────────────────────────────────────────────────────────────────

ROUTER_MODEL = (
    os.getenv("ROUTER_LLM_MODEL")
    or os.getenv("ANTHROPIC_SIMPLE_MODEL")
    or os.getenv("ANTHROPIC_MODEL")
    or "claude-3-haiku-20240307"
)
CONFIDENCE_THRESHOLD = float(os.getenv("ROUTER_LLM_CONFIDENCE", "0.60"))

# ── workflow catalogue ─────────────────────────────────────────────────────────
# Each description tells the model WHEN to use the workflow and gives
# example phrasings so it can generalise, not just keyword-match.

WORKFLOW_DESCRIPTIONS: dict[str, str] = {
    WorkflowType.CONVERSATIONAL: (
        "CEO wants a direct answer in plain language. Questions, opinions, broad topics, "
        "situational awareness, anything that doesn't need a structured artifact. "
        "DEFAULT when unsure."
    ),
    WorkflowType.REPORT_GENERATION: (
        "CEO explicitly wants structured analysis with data — financial review, "
        "board prep, KPI summary, burn/runway analysis, escalation analysis, "
        "or any request for a formal report or brief."
    ),
    WorkflowType.CALENDAR_BRIEFING: (
        "User wants to SEE their calendar or a meeting overview — not build a plan. "
        "E.g. 'what's on my calendar this week', 'show me my meetings', "
        "'what do I have tomorrow'."
    ),
    WorkflowType.SCHEDULE_PLANNING: (
        "User wants to BUILD or GENERATE a time-blocked schedule or work plan. "
        "E.g. 'plan my day', 'block out my week', 'organise my schedule', "
        "'block prep time before back-to-back meetings', 'help me prioritise this week'."
    ),
    WorkflowType.MEETING_PREP: (
        "User wants preparation notes for a specific upcoming meeting they are attending. "
        "E.g. 'what do I need to prepare for this meeting', 'prep me for the finance call', "
        "'what should I bring / know for this meeting', "
        "'check whether any meeting requires materials', "
        "'flag attendees who need a pre-read or agenda'. "
        "NOT for producing deliverable documents, briefs, reports, or packets — "
        "those are report_generation. "
        "NOT for requests that include 'build me a brief', 'write a report', "
        "'board packet', 'executive summary', 'talking points document', "
        "'comprehensive brief', or 'data-backed brief' — those are report_generation."
    ),
    WorkflowType.EMAIL_WATCHER: (
        "User wants to review or scan their email inbox. "
        "E.g. 'check my emails', 'what emails need attention', 'scan my inbox', "
        "'any important messages'."
    ),
    WorkflowType.MORNING_BRIEF: (
        "Morning or daily digest that combines inbox + calendar + signals. "
        "E.g. 'morning brief', 'daily digest', 'brief me on today', "
        "'what do I need to know this morning'."
    ),
    WorkflowType.WEEKLY_RECAP: (
        "Backward-looking end-of-week review. "
        "E.g. 'recap my week', 'week in review', 'what did I accomplish this week'."
    ),
    WorkflowType.DOCUMENT_EXPLANATION: (
        "User uploaded a file or asks to analyse a specific document, memo, or contract."
    ),
}

# RouteFamily each workflow belongs to — used to build a consistent RouteDecision
WORKFLOW_TO_FAMILY: dict[str, RouteFamily] = {
    WorkflowType.REPORT_GENERATION:    RouteFamily.REPORT,
    WorkflowType.DOCUMENT_EXPLANATION: RouteFamily.REPORT,
    WorkflowType.CALENDAR_BRIEFING:    RouteFamily.WATCH,
    WorkflowType.EMAIL_WATCHER:        RouteFamily.WATCH,
    WorkflowType.EMAIL_INGESTION:      RouteFamily.WATCH,
    WorkflowType.MORNING_BRIEF:        RouteFamily.WATCH,
    WorkflowType.WEEKLY_RECAP:         RouteFamily.WATCH,
    WorkflowType.SCHEDULE_PLANNING:    RouteFamily.PLAN,
    WorkflowType.MEETING_PREP:         RouteFamily.PLAN,
}

# ── output schema ──────────────────────────────────────────────────────────────

class LLMRouteDecision(BaseModel):
    workflow_type: str = Field(
        description="The single best workflow for this request. Must be one of the listed workflow type strings."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in this choice, 0.0–1.0."
    )
    rationale: str = Field(
        description="One sentence explaining the intent classification."
    )
    requires_approval: bool = Field(
        default=False,
        description="True only if the action writes data (send email, create calendar event)."
    )

# ── prompts ────────────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    catalogue = "\n".join(
        f'  "{wt}": {desc}'
        for wt, desc in WORKFLOW_DESCRIPTIONS.items()
    )
    return (
        "You are the intent classifier for an AI executive assistant.\n\n"
        "Your job: given the user's message and optional conversation context, "
        "select the ONE workflow that best serves their intent. "
        "Reason from MEANING, not keywords — 'what do I need to prepare for this meeting?' "
        "is meeting_prep, not calendar_briefing, even though it mentions a meeting.\n\n"
        "Use conversation context to resolve pronouns and references "
        "(e.g. 'it', 'this meeting', 'that report').\n\n"
        "Messages prefixed with 'Follow-up action:' are specific action items from a prior response. "
        "A [Context: ...] prefix tells you what the prior response was about — use it to sharpen "
        "your classification, but classify by the action's OWN intent, not the prior workflow type. "
        "Examples: 'Approve cloud spend' → requires_approval=true, likely report_generation or ACT. "
        "'Decide on margin variance' → report_generation (analysis). "
        "'Finalize board narrative' → report_generation (document/synthesis). "
        "'Show me the calendar for this' → calendar_briefing.\n\n"
        f"Available workflows:\n{catalogue}\n\n"
        "Return ONLY valid JSON:\n"
        '{"workflow_type":"<string>","confidence":<0.0-1.0>,"rationale":"<one sentence>","requires_approval":<bool>}'
    )


def _build_user_prompt(
    message: str,
    history: list[dict],
    has_attachments: bool,
    unified_memory: dict[str, Any] | None = None,
) -> str:
    parts: list[str] = []

    if unified_memory:
        working_memory = unified_memory.get("working_memory") or {}
        session_memory = unified_memory.get("session_memory") or {}
        compact_memory = {
            "working_memory": {
                "primary_intent": working_memory.get("primary_intent"),
                "mode": working_memory.get("mode"),
                "execution_mode": working_memory.get("execution_mode"),
                "workflow_preference": working_memory.get("workflow_preference"),
                "task_topic": working_memory.get("task_topic"),
                "deliverable": working_memory.get("deliverable"),
                "timeframe": working_memory.get("timeframe"),
                "deadline": working_memory.get("deadline"),
                "must_not_do": working_memory.get("must_not_do", [])[:6],
            },
            "session_memory": {
                "open_decisions": session_memory.get("open_decisions", [])[:5],
                "open_commitments": session_memory.get("open_commitments", [])[:5],
                "recent_artifacts": session_memory.get("recent_artifacts", [])[:4],
                "previous_workflow_type": session_memory.get("previous_workflow_type"),
                "previous_response_title": session_memory.get("previous_response_title"),
            },
        }
        parts.append("Unified memory:")
        parts.append(json.dumps(compact_memory, ensure_ascii=True))
        parts.append("")

    if history:
        parts.append("Conversation context (chronological):")
        for turn in history[-3:]:
            role = turn.get("role", "user")
            content = str(turn.get("content", ""))[:400]
            parts.append(f"  [{role}]: {content}")
        parts.append("")

    if has_attachments:
        parts.append("[User has attached a file]\n")

    parts.append(f"User message: {message}")
    return "\n".join(parts)

# ── router ─────────────────────────────────────────────────────────────────────

class LLMRouter:
    """
    Single-call Haiku router.  Returns an LLMRouteDecision or None.
    None means "fall back to keyword routing".
    """

    def __init__(self) -> None:
        self._llm = LLMClient(model=ROUTER_MODEL)
        self._system = _build_system_prompt()

    async def classify(
        self,
        message: str,
        history: list[dict] | None = None,
        has_attachments: bool = False,
        unified_memory: dict[str, Any] | None = None,
    ) -> Optional[LLMRouteDecision]:
        if not self._llm.anthropic_async and not self._llm.openai_async:
            return None

        prompt = _build_user_prompt(message, history or [], has_attachments, unified_memory=unified_memory)

        try:
            raw = await self._llm.complete_async(prompt, self._system)
        except Exception as exc:
            print(f"[LLMRouter] LLM call failed: {exc}")
            return None

        parsed, err, _ = self._llm._try_parse_structured(raw, LLMRouteDecision)
        if parsed is None:
            print(f"[LLMRouter] Parse failed: {err}")
            return None

        if parsed.workflow_type not in WORKFLOW_DESCRIPTIONS:
            print(f"[LLMRouter] Unknown workflow_type returned: {parsed.workflow_type!r}")
            return None

        if parsed.confidence < CONFIDENCE_THRESHOLD:
            print(
                f"[LLMRouter] Low confidence ({parsed.confidence:.2f}) for "
                f"'{parsed.workflow_type}' — falling back to keywords"
            )
            return None

        print(
            f"[LLMRouter] {parsed.workflow_type} "
            f"(conf={parsed.confidence:.2f}) — {parsed.rationale}"
        )
        return parsed

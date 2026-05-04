"""
IntentClassifier — single LLM call to determine what the CEO actually wants.

Replaces the keyword routing chain (request_planner keyword lists, LLMRouter
workflow catalogue). Gets smarter with longer conversation context, not worse.

The classifier answers one question: given this message and conversation,
what does this CEO actually want — in terms of format, data, and action?

Usage
-----
    from src.assistant.intent_classifier import IntentClassifier

    result = await IntentClassifier().classify(
        message="What should I focus on today?",
        history=[...],
    )
    # result.workflow  → "conversational"
    # result.data_needed → []
    # result.reasoning → "CEO asking for prioritisation guidance, no data pull implied"
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from src.workflows.types import WorkflowType

CLASSIFIER_MODEL = (
    os.getenv("INTENT_CLASSIFIER_MODEL")
    or os.getenv("ANTHROPIC_SIMPLE_MODEL")
    or os.getenv("ANTHROPIC_MODEL")
    or "claude-sonnet-4-20250514"
)
CONFIDENCE_THRESHOLD = float(os.getenv("INTENT_CLASSIFIER_CONFIDENCE", "0.55"))

_VALID_WORKFLOWS = {
    WorkflowType.CONVERSATIONAL,
    WorkflowType.REPORT_GENERATION,
    WorkflowType.DOCUMENT_EXPLANATION,
    WorkflowType.EMAIL_WATCHER,
    WorkflowType.EMAIL_ACTION,
    WorkflowType.CALENDAR_BRIEFING,
    WorkflowType.CALENDAR_ACTION,
    WorkflowType.MORNING_BRIEF,
    WorkflowType.WEEKLY_RECAP,
    WorkflowType.SCHEDULE_PLANNING,
    WorkflowType.MEETING_PREP,
}

_SYSTEM = """\
You are an intent classifier for a CEO executive assistant.

Given a CEO's message and recent conversation history, determine exactly what they want.

Return JSON only — no explanation outside the JSON block:
{
  "workflow": string,
  "response_format": string,
  "data_needed": array,
  "action_requested": boolean,
  "task_topic": string,
  "time_scope": string,
  "confidence": float,
  "reasoning": string
}

WORKFLOW — choose exactly one:
- "conversational": CEO wants a direct answer in plain language. Questions, opinions, \
broad topics, situational awareness. DEFAULT when nothing more specific fits.
- "report_generation": CEO wants a structured artifact with data — financial review, \
strategic assessment, board prep, board packet, KPI summary, burn/runway, escalation analysis. \
Also use for: refining, reformatting, or optimizing an existing report/brief/packet \
(e.g. "optimize this for board language", "reformat the packet", "restructure the brief").
- "document_explanation": CEO uploaded or referenced a specific document to analyse.
- "email_watcher": CEO wants to review or summarise their email inbox or threads.
- "email_action": CEO wants to draft or send an email.
- "calendar_briefing": CEO wants to see their calendar or meeting schedule.
- "calendar_action": CEO wants to create or modify a calendar event.
- "morning_brief": CEO asked for a combined inbox + calendar overview (morning brief, \
daily digest, what do I need to know today).
- "weekly_recap": CEO wants a backward-looking week review (what happened, what did I accomplish).
- "schedule_planning": CEO wants to plan or time-block their schedule.
- "meeting_prep": CEO wants personal prep NOTES to help them attend and perform in a meeting \
(talking points, background context, what to watch for). \
NOT for producing or refining documents, briefs, or packets — those are report_generation. \
NOT when CEO is asking to reformat or improve an existing artifact.

RESPONSE FORMAT — choose one:
- "conversational": plain prose answer, no artifact
- "report": structured analysis with sections
- "document": deliverable document (board brief, action plan)
- "draft": draft to review before sending (email, event)

DATA NEEDED — include only what the request genuinely requires:
- "email": inbox or thread content is needed
- "calendar": meeting or schedule data is needed
- "company_state": company financials, org structure, strategy, or performance data
- "documents": uploaded files or indexed company documents

TIME SCOPE — one of: "now", "today", "this_week", "historical"

CONFIDENCE — your confidence 0.0–1.0 that you've correctly identified the intent.

REASONING — one sentence explaining the classification.

TASK TOPIC — if the request is about a specific domain, choose one:
- "customer_escalation": accounts at risk, churn, outages, support issues (e.g. "Apex Health", "Redwood").
- "finance_close": close week, board prep, monthly/quarterly variance.
- "cost_containment": burn reduction, cloud spend, runway extension, AWS overages.
- "pricing_strategy": price war, competitive response, margin impact, pricing tiers.
- "hiring": candidates, interviews, comp packages, team growth.
- "renewal_contingency": specific deal rescue or ARR-at-risk planning.
- null: no specific topic found.

KEY RULES:
1. Default to "conversational" when unsure — never force report_generation on a vague question.
2. "What should I focus on today?" → conversational (CEO wants guidance, not a report). \
   BUT if the question specifies a domain (customer issues, deals, financials, risks) → report_generation.
3. "Give me a board brief on Q1 burn" → report_generation + data_needed: ["company_state"].
10. DATA DOMAIN RULE: "What customer/deal/account issues need my attention?" → report_generation \
    data_needed: ["company_state"]. Any question asking about SPECIFIC real data \
    (customers at risk, pipeline, financials, deal status) requires report_generation — \
    the assistant must pull actual data, not answer conversationally from memory.
4. "What did I miss in my inbox?" → email_watcher.
5. "Draft a delegation email to Sarah" → email_action, action_requested: true.
6. Use conversation history to resolve references like "that meeting", "this deal", \
"the email I mentioned".
7. action_requested is true only for write operations (sending email, creating events).
8. DOCUMENT REFINEMENT RULE: If the prior assistant response was a report/brief/packet and \
the CEO says "optimize it", "reformat it", "restructure it", "in board language", \
"use board format", "make it more concise" — classify as report_generation. \
Never switch to meeting_prep just because the document will be used in a meeting.
9. MEETING_PREP vs REPORT: meeting_prep is for "what do I need to know to ATTEND this meeting". \
report_generation is for "build/refine a DOCUMENT I will present or use in a meeting".

SEMANTIC TOPIC HINTS:
- "escalation" triggers: outages, miss, delay, "Apex", "Redwood", "rescue".
- "finance" triggers: variance vs plan, board call, monthly close, budget, forecast.
- "hiring" triggers: candidates, offer letters, headcount, recruiting.
10. DATA DOMAIN RULE: "What customer/deal/account issues need my attention?" → report_generation + task_topic: "customer_escalation".

SEMANTIC TOPIC HINTS:
- "escalation" triggers: outages, miss, delay, "Apex", "Redwood", "rescue".
- "finance" triggers: variance vs plan, board call, monthly close, budget, forecast.
- "hiring" triggers: candidates, offer letters, headcount, recruiting.
"""


@dataclass
class ClassifiedIntent:
    workflow: str
    response_format: str
    data_needed: list[str] = field(default_factory=list)
    action_requested: bool = False
    task_topic: str | None = None
    time_scope: str = "now"
    confidence: float = 0.8
    reasoning: str = ""

    @property
    def is_confident(self) -> bool:
        return self.confidence >= CONFIDENCE_THRESHOLD


_FALLBACK = ClassifiedIntent(
    workflow=WorkflowType.CONVERSATIONAL,
    response_format="conversational",
    data_needed=[],
    action_requested=False,
    task_topic=None,
    time_scope="now",
    confidence=0.0,
    reasoning="Fallback: classifier unavailable.",
)


class IntentClassifier:
    """Single LLM call to classify CEO request intent."""

    def __init__(self, model: str = CLASSIFIER_MODEL) -> None:
        self._model = model

    async def classify(
        self,
        message: str,
        history: list[dict[str, Any]] | None = None,
        has_attachments: bool = False,
    ) -> ClassifiedIntent:
        """Classify the CEO's intent. Returns _FALLBACK on any failure."""
        if has_attachments:
            return ClassifiedIntent(
                workflow=WorkflowType.DOCUMENT_EXPLANATION,
                response_format="report",
                data_needed=["documents"],
                reasoning="File attachment present.",
                confidence=1.0,
            )

        prompt = _build_prompt(message, history or [])
        try:
            from src.core.llm import LLMClient
            raw = await LLMClient(model=self._model).complete_async(prompt, _SYSTEM)
            return _parse(raw)
        except Exception:
            return _FALLBACK

    def classify_sync(
        self,
        message: str,
        history: list[dict[str, Any]] | None = None,
        has_attachments: bool = False,
    ) -> ClassifiedIntent:
        """Synchronous variant for call sites that cannot await."""
        if has_attachments:
            return ClassifiedIntent(
                workflow=WorkflowType.DOCUMENT_EXPLANATION,
                response_format="report",
                data_needed=["documents"],
                reasoning="File attachment present.",
                confidence=1.0,
            )

        prompt = _build_prompt(message, history or [])
        try:
            from src.core.llm import LLMClient
            raw = LLMClient(model=self._model).complete(prompt, _SYSTEM)
            return _parse(raw)
        except Exception:
            return _FALLBACK


def _build_prompt(message: str, history: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    if history:
        lines.append("Recent conversation (oldest first):")
        for turn in history[-8:]:
            role = turn.get("role", "user")
            content = str(turn.get("content", ""))[:400]
            lines.append(f"  {role}: {content}")
        lines.append("")
    lines.append(f"Current message: {message}")
    return "\n".join(lines)


def _parse(raw: str) -> ClassifiedIntent:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return _FALLBACK
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return _FALLBACK

    workflow = data.get("workflow", WorkflowType.CONVERSATIONAL)
    if workflow not in _VALID_WORKFLOWS:
        workflow = WorkflowType.CONVERSATIONAL

    response_format = data.get("response_format", "conversational")
    data_needed = [
        s for s in (data.get("data_needed") or [])
        if s in ("email", "calendar", "company_state", "documents", "session_history", "signals")
    ]
    action_requested = bool(data.get("action_requested", False))
    task_topic = data.get("task_topic")
    time_scope = data.get("time_scope", "now")
    if time_scope not in ("now", "today", "this_week", "historical"):
        time_scope = "now"
    confidence = float(data.get("confidence", 0.8))
    reasoning = str(data.get("reasoning", ""))

    return ClassifiedIntent(
        workflow=workflow,
        response_format=response_format,
        data_needed=data_needed,
        action_requested=action_requested,
        task_topic=task_topic,
        time_scope=time_scope,
        confidence=confidence,
        reasoning=reasoning,
    )

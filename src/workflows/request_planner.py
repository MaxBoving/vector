from __future__ import annotations

from datetime import date, datetime
import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel

from src.workflows.planner_semantics import infer_planner_semantics
from src.workflows.planning_time import build_planning_window
from src.workflows.planning_types import PlanStep, PlannedSubtask, PlanningTimeHorizon, RequestPlan, RetrievalPlan, RetrievalSourceRequest
from src.workflows.types import WorkflowType

logger = logging.getLogger(__name__)

INBOX_KEYWORDS = ("email", "emails", "inbox", "thread", "threads", "mail")
CALENDAR_KEYWORDS = ("calendar", "meeting", "meetings", "agenda", "availability")
DOCUMENT_KEYWORDS = ("document", "memo", "attachment", "attached", "brief", "plan doc", "deck", "policy", "contract")
PLANNING_KEYWORDS = (
    "schedule",
    "organize",
    "prioritize",
    "map out",
    "block out",
    "generate my plan",
    "generate me a schedule",
    "schedule plan",
)
WATCH_KEYWORDS = ("scan", "review", "watch", "brief", "digest", "recap", "important")
NORMALIZED_PHRASES = (
    ("calender", "calendar"),
    ("calandar", "calendar"),
    (" cal ", " calendar "),
    ("wk", "week"),
    ("wk.", "week"),
    ("e-mails", "emails"),
    ("e-mail", "email"),
    ("my week", "this week"),
    ("for my week", "for this week"),
    ("during my week", "during this week"),
    ("this wk", "this week"),
    ("next wk", "next week"),
)
THIS_WEEK_PATTERNS = (
    "this week",
    "my week",
    "the week",
    "rest of the week",
    "for the week",
    "for my week",
    "during the week",
    "during my week",
    "week ahead",
)
NEXT_WEEK_PATTERNS = ("next week", "next wk")
SEMANTIC_EMAIL_CUES = (
    "messages",
    "correspondence",
    "follow ups",
    "follow-ups",
    "replies",
    "inbound",
    "senders",
    "threads",
)
SEMANTIC_CALENDAR_CUES = (
    "free time",
    "open time",
    "availability",
    "commitments",
    "deadlines",
    "when i have time",
    "when do i have time",
    "when i have free time",
    "when do i have free time",
    "open slots",
    "time blocks",
    "blocked",
)
SEMANTIC_DOCUMENT_CUES = ("deck", "readout", "materials", "attachment", "attachments", "memo", "briefing")
SEMANTIC_PLANNING_CUES = (
    "what do i need to do",
    "what should i work on",
    "what should i focus on",
    "make time for",
    "carve out time",
    "fit in",
    "line up",
    "lay out",
    "sequence",
    "slot in",
    "free time",
)
SEMANTIC_WATCH_CUES = ("what changed", "what matters", "what's important", "what is important", "keep me posted")
FINANCE_ANALYSIS_KEYWORDS = (
    "financial metrics",
    "finance close",
    "finance close meeting",
    "revenue",
    "expenses",
    "cash flow",
    "burn",
    "runway",
    "variance",
    "forecast",
    "budget",
    "board packet",
    "cloud spend",
    "investor update",
)
STRATEGIC_ANALYSIS_KEYWORDS = (
    "overall strategy",
    "strategic impact",
    "hiring freeze",
    "team morale",
    "project timelines",
    "critical roles",
    "immediate hiring",
    "specific roles",
    "headcount plan",
    "staffing impact",
    "org impact",
    "most critical to close",
    "which role is most",
    "role to prioritize",
    "hiring priority",
    "go/no-go recommendation",
    "go no go recommendation",
    "before the meeting",
    "before the board",
    "deadlines before",
    "open items",
    "who is responsible",
    "who owns",
    "board packet",
    "board narrative",
    "must happen today",
    "highest-priority actions",
    "highest priority actions",
    "must complete today",
    "what decisions need to be made",
    "defer or delegate",
    "safely defer",
    "investor call prep",
)
ACTION_PLAN_KEYWORDS = (
    "action plan",
    "action plans",
    "immediate steps",
    "next steps",
    "what steps should we take",
    "what should we do",
    "what do we do next",
    "resolve their issues",
    "address their concerns",
    "resolve these escalations",
    "resolve this escalation",
    "accountability",
    "accountability measures",
    "recovery commitment",
    "recovery commitments",
    "recovery milestones",
    "recovery plan",
    "rescue plan",
)
ESCALATION_KEYWORDS = (
    "escalation",
    "escalations",
    "customer escalation",
    "urgent customer",
    "customer concern",
    "customer concerns",
    "customer issue",
    "customer issues",
)
RECOMMENDATION_KEYWORDS = (
    "recommend",
    "recommendation",
    "what should i",
    "what should we",
    "advise me",
    "your advice",
    "what would you do",
    "what do you suggest",
    "what's your take",
    "what is your recommendation",
    "suggest an approach",
    "what's the right move",
    "which option",
    "which approach should",
)
SCHEDULING_INTENT_KEYWORDS = (
    "schedule",
    "organize",
    "prioritize",
    "map out",
    "block out",
    "generate my plan",
    "generate me a schedule",
    "schedule plan",
    "plan my day",
    "plan my week",
    "plan this week",
    "plan next week",
    "organize my day",
    "organize my week",
    "block prep time",
    "prep time before",
    "back-to-back",
    "buffer time",
    "block time",
)
class IntentClassification(BaseModel):
    workflow: str
    mode: str = "direct_workflow"
    needs_email: bool = False
    needs_calendar: bool = False
    needs_documents: bool = False
    time_horizon: str = "unspecified"
    target_label: Optional[str] = None
    rationale: str = ""


_VALID_WORKFLOWS = {
    WorkflowType.CONVERSATIONAL,
    WorkflowType.REPORT_GENERATION,
    WorkflowType.SCHEDULE_PLANNING,
    WorkflowType.MORNING_BRIEF,
    WorkflowType.WEEKLY_RECAP,
    WorkflowType.MEETING_PREP,
    WorkflowType.EMAIL_WATCHER,
    WorkflowType.CALENDAR_BRIEFING,
}

_VALID_HORIZONS = {"today", "tomorrow", "this_week", "next_week", "week_after_next", "unspecified"}

_CONTEXT_SOURCES_FOR_WORKFLOW: dict[str, list[str]] = {
    WorkflowType.REPORT_GENERATION: ["email", "calendar", "documents", "session_history", "signals"],
    WorkflowType.DOCUMENT_EXPLANATION: ["documents", "session_history"],
    WorkflowType.SCHEDULE_PLANNING: ["email", "calendar", "signals", "session_history"],
    WorkflowType.MORNING_BRIEF: ["email", "calendar", "signals"],
    WorkflowType.WEEKLY_RECAP: ["email", "calendar", "signals", "session_history"],
    WorkflowType.MEETING_PREP: ["email", "calendar", "documents", "signals", "session_history"],
    WorkflowType.EMAIL_WATCHER: ["email", "signals"],
    WorkflowType.CALENDAR_BRIEFING: ["calendar", "signals"],
}

_INTENT_SYSTEM_PROMPT = """\
You are a request router for an executive AI assistant. Given a user message, classify it into the correct workflow.

WORKFLOWS:
- conversational: CEO wants a direct plain-language answer. Questions, opinions, broad topics, guidance. DEFAULT when nothing more specific fits.
- report_generation: CEO wants structured analysis — financial review, KPI summary, burn/runway, escalation, strategic assessment, or a formal report. Use this when the request is to REVIEW DATA or GET ANALYSIS, even when a meeting is the stated context for needing it.
- schedule_planning: Building or reorganizing a work schedule, prioritizing tasks for a day or week, blocking time, mapping out what to work on.
- morning_brief: Explicitly asked for a morning brief, daily brief, or daily digest.
- weekly_recap: Backward-looking review — what happened this week, week in review, accomplishments.
- meeting_prep: Preparing briefing materials for a SPECIFIC SCHEDULED MEETING — agenda, attendee backgrounds, talking points, who will be in the room. NOT for general financial or operational analysis that happens to precede a meeting.
- email_watcher: Reviewing or summarizing inbox, email threads, or correspondence (not combined with scheduling).
- calendar_briefing: Reviewing calendar or upcoming meetings for a specific time window (not combined with scheduling).

COMPOUND PLAN: Use mode="compound_plan" only for schedule_planning when inbox or calendar data is needed to inform the schedule. Otherwise use "direct_workflow".

CONTEXT SOURCES — set to true only when the user's request clearly implies that data:
- needs_email: inbox/email threads are relevant input
- needs_calendar: calendar/meetings are relevant input
- needs_documents: attached files or referenced docs are needed

TIME HORIZON — classify any date reference:
  today | tomorrow | this_week | next_week | week_after_next | unspecified
"""


def _classify_intent_semantic(
    message: str,
    today: date,
    unified_memory: dict[str, Any] | None = None,
) -> Optional[IntentClassification]:
    """Single LLM call that classifies the full intent of a request.
    Returns None if the LLM is unavailable, so callers must handle fallback.
    """
    try:
        from src.core.llm import LLMClient
        weekday = today.strftime("%A")
        memory_block = f'Unified memory: {json.dumps(_compact_unified_memory(unified_memory), ensure_ascii=True)}\n\n' if unified_memory else ""
        prompt = (
            f'Today is {today.isoformat()} ({weekday}).\n\n'
            f'{memory_block}'
            f'User message: "{message}"\n\n'
            "Return JSON only:\n"
            "{\n"
            '  "workflow": "<workflow_name>",\n'
            '  "mode": "direct_workflow" | "compound_plan",\n'
            '  "needs_email": true|false,\n'
            '  "needs_calendar": true|false,\n'
            '  "needs_documents": true|false,\n'
            '  "time_horizon": "today"|"tomorrow"|"this_week"|"next_week"|"week_after_next"|"unspecified",\n'
            '  "target_label": "<short human label for the time window, or null>",\n'
            '  "rationale": "<one sentence>"\n'
            "}"
        )
        raw = LLMClient().complete(prompt, _INTENT_SYSTEM_PROMPT)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group(0))
        workflow = data.get("workflow", "")
        if workflow not in _VALID_WORKFLOWS:
            workflow = WorkflowType.REPORT_GENERATION
        horizon = data.get("time_horizon", "unspecified")
        if horizon not in _VALID_HORIZONS:
            horizon = "unspecified"
        return IntentClassification(
            workflow=workflow,
            mode=data.get("mode", "direct_workflow"),
            needs_email=bool(data.get("needs_email", False)),
            needs_calendar=bool(data.get("needs_calendar", False)),
            needs_documents=bool(data.get("needs_documents", False)),
            time_horizon=horizon,
            target_label=data.get("target_label") or None,
            rationale=data.get("rationale", ""),
        )
    except Exception:
        return None


def _build_plan_from_classification(
    cl: IntentClassification,
    message: str,
    has_attachments: bool,
    reference_dt: datetime | None,
) -> RequestPlan:
    """Convert a semantic IntentClassification into a RequestPlan."""
    normalized_message = _normalize_message(message)
    planning_window = build_planning_window(
        cl.time_horizon,
        reference_dt=reference_dt,
        target_label=cl.target_label,
    )
    time_horizon = cl.time_horizon
    target_date = planning_window.target_date
    if target_date is None and time_horizon in {"today", "tomorrow"}:
        target_date = planning_window.start_date
    target_label = cl.target_label or planning_window.target_label
    semantic_metadata = infer_planner_semantics(
        workflow=cl.workflow,
        needs_email=cl.needs_email,
        needs_calendar=cl.needs_calendar,
        needs_documents=cl.needs_documents,
        time_horizon=time_horizon,
    ).as_metadata()

    mentions_email = semantic_metadata["email"]
    mentions_calendar = semantic_metadata["calendar"]
    mentions_documents = semantic_metadata["documents"]
    mentions_planning = semantic_metadata["planning"]
    mentions_watch = semantic_metadata["watch"]

    # Prefer the semantic classifier's horizon if it's more specific.
    if has_attachments:
        cl.needs_documents = True

    base_sources = list(_CONTEXT_SOURCES_FOR_WORKFLOW.get(cl.workflow, ["session_history"]))
    base_retrieval_plan = _build_retrieval_plan(
        sources=base_sources,
        time_horizon=time_horizon,
        target_date=target_date,
        target_label=target_label,
        rationale=cl.rationale,
        planner_version="v3-semantic",
        execution_model="carrier_workflow_with_planner_execution",
    )

    # Compound schedule plan: build subtasks + execution steps
    if cl.workflow == WorkflowType.SCHEDULE_PLANNING and cl.mode == "compound_plan":
        needed: list[str] = []
        subtasks: list[PlannedSubtask] = []
        steps: list[PlanStep] = []

        if cl.needs_email:
            needed.append("email")
            subtasks.append(PlannedSubtask(
                key="scan_inbox", kind="email_watch",
                description="Review recent executive email threads for actionable items, deadlines, and asks.",
                context_sources=["email", "signals"],
            ))
            steps.append(PlanStep(
                key="scan_inbox", kind="email_watch",
                description="Gather actionable inbox evidence for the requested planning window.",
                context_sources=["email", "signals"],
            ))

        if cl.needs_calendar or time_horizon in {"this_week", "next_week", "week_after_next"}:
            needed.append("calendar")
            subtasks.append(PlannedSubtask(
                key="review_calendar", kind="calendar_watch",
                description="Review meetings and calendar commitments that constrain the planning window.",
                context_sources=["calendar"],
            ))
            steps.append(PlanStep(
                key="review_calendar", kind="calendar_watch",
                description="Gather meetings and calendar constraints for the requested planning window.",
                context_sources=["calendar"],
            ))

        if cl.needs_documents:
            needed.append("documents")
            subtasks.append(PlannedSubtask(
                key="review_documents", kind="document_context",
                description="Fold attached or referenced documents into the planning evidence set.",
                context_sources=["documents"],
            ))
            steps.append(PlanStep(
                key="review_documents", kind="document_context",
                description="Gather attached or referenced document evidence for the planning lane.",
                context_sources=["documents"],
            ))

        subtasks.append(PlannedSubtask(
            key="build_schedule", kind="schedule_synthesis",
            description="Synthesize inbox, calendar, and recent context into a concrete schedule proposal.",
            context_sources=["email", "calendar", "session_history", "documents"],
        ))
        steps.extend([
            PlanStep(
                key="synthesize_planning_candidates", kind="schedule_candidate_synthesis",
                description="Turn gathered evidence into concrete planning candidates.",
                context_sources=["email", "calendar", "documents", "session_history"],
            ),
            PlanStep(
                key="place_candidates", kind="schedule_slotting",
                description="Place planning candidates into slots inside the requested planning window.",
                context_sources=["calendar"],
            ),
            PlanStep(
                key="build_schedule", kind="schedule_synthesis",
                description="Assemble the final schedule proposal from placed candidates and supporting evidence.",
                context_sources=["email", "calendar", "documents", "session_history"],
            ),
        ])

        return RequestPlan(
            mode="compound_plan",
            target_workflow=WorkflowType.SCHEDULE_PLANNING,
            subtasks=subtasks,
            execution_steps=steps,
            needed_context_sources=_dedupe(needed + ["email", "session_history", "documents", "signals"]),
            retrieval_plan=_build_retrieval_plan(
                sources=_dedupe(needed + ["email", "session_history", "documents", "signals"]),
                time_horizon=time_horizon,
                target_date=target_date,
                target_label=target_label,
                rationale=cl.rationale,
                planner_version="v3-semantic",
                execution_model="carrier_workflow_with_planner_execution",
            ),
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale=cl.rationale,
            planning_metadata={
                "planner_version": "v3-semantic",
                "semantic_fallback_used": True,
                "semantic_signals": semantic_metadata,
                "needs_email": cl.needs_email,
                "needs_calendar": cl.needs_calendar,
                "needs_documents": cl.needs_documents,
                "execution_model": "carrier_workflow_with_planner_execution",
            },
        )

    # Direct workflow
    return RequestPlan(
        mode="direct_workflow",
        target_workflow=cl.workflow,
        direct_workflow=cl.workflow,
        needed_context_sources=base_sources,
        retrieval_plan=base_retrieval_plan,
        time_horizon=time_horizon,
        target_date=target_date,
        target_label=target_label,
        rationale=cl.rationale,
        planning_metadata={
            "planner_version": "v3-semantic",
            "semantic_fallback_used": True,
            "semantic_signals": semantic_metadata,
            "needs_email": cl.needs_email,
            "needs_calendar": cl.needs_calendar,
            "needs_documents": cl.needs_documents,
            "execution_model": "carrier_workflow_with_planner_execution",
        },
    )


def plan_request(
    message: str,
    *,
    has_attachments: bool = False,
    unified_memory: dict[str, Any] | None = None,
    reference_dt: datetime | None = None,
) -> RequestPlan:
    memory_plan = _plan_from_unified_memory(unified_memory, has_attachments=has_attachments)
    if memory_plan is not None:
        return memory_plan
    today = (reference_dt or datetime.now().astimezone()).date()
    classification = _classify_intent_semantic(message, today, unified_memory=unified_memory)
    if classification is not None:
        plan = _build_plan_from_classification(classification, message, has_attachments, reference_dt)
        logger.info(
            "request_planner.classified workflow=%s mode=%s horizon=%s target_date=%s target_label=%s message=%r",
            plan.target_workflow,
            plan.mode,
            plan.time_horizon,
            plan.target_date,
            plan.target_label,
            message,
        )
        return plan
    plan = RequestPlan(
        mode="direct_workflow",
        target_workflow=WorkflowType.CONVERSATIONAL,
        direct_workflow=WorkflowType.CONVERSATIONAL,
        needed_context_sources=["session_history"],
        retrieval_plan=_build_retrieval_plan(
            sources=["session_history"],
            time_horizon="unspecified",
            target_date=None,
            target_label=None,
            rationale="Planner classification was unavailable, so return a conservative conversational default rather than guessing with keyword rules.",
            planner_version="v4-llm-only",
            execution_model="conversational_default",
        ),
        rationale="Planner classification was unavailable, so return a conservative conversational default rather than guessing with keyword rules.",
        planning_metadata={
            "planner_version": "v4-llm-only",
            "planner_unavailable": True,
            "semantic_fallback_used": False,
        },
    )
    logger.info(
        "request_planner.unavailable defaulted workflow=%s mode=%s message=%r",
        plan.target_workflow,
        plan.mode,
        message,
    )
    return plan


def _compact_unified_memory(unified_memory: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(unified_memory, dict):
        return {}
    working = unified_memory.get("working_memory") or {}
    session = unified_memory.get("session_memory") or {}
    return {
        "working_memory": {
            "workflow_preference": working.get("workflow_preference"),
            "mode": working.get("mode"),
            "deliverable": working.get("deliverable"),
            "task_topic": working.get("task_topic"),
            "timeframe": working.get("timeframe"),
            "deadline": working.get("deadline"),
        },
        "session_memory": {
            "previous_workflow_type": session.get("previous_workflow_type"),
            "recent_artifacts": session.get("recent_artifacts", [])[:4],
            "open_decisions": session.get("open_decisions", [])[:4],
            "open_commitments": session.get("open_commitments", [])[:4],
            "current_schedule": bool(session.get("current_schedule")),
        },
    }


def _plan_from_unified_memory(
    unified_memory: dict[str, Any] | None,
    *,
    has_attachments: bool,
) -> RequestPlan | None:
    if not isinstance(unified_memory, dict):
        return None
    working = unified_memory.get("working_memory") or {}
    session = unified_memory.get("session_memory") or {}
    workflow_preference = str(working.get("workflow_preference") or "")
    deliverable = working.get("deliverable") or {}
    deliverable_kind = str(deliverable.get("kind") or "")
    mode = str(working.get("mode") or "")

    if workflow_preference == WorkflowType.REPORT_GENERATION and (
        deliverable_kind in {"artifact_revision", "email", "resolution_language", "execution_bundle"}
        or mode in {"revision", "continuation", "correction"}
    ):
        retrieval_sources = ["documents", "session_history", "signals"]
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.REPORT_GENERATION,
            direct_workflow=WorkflowType.REPORT_GENERATION,
            needed_context_sources=retrieval_sources,
            retrieval_plan=_build_retrieval_plan(
                sources=retrieval_sources,
                time_horizon="unspecified",
                target_date=None,
                target_label=None,
                rationale="Working memory indicates an active report-side deliverable, so preserve that workflow across the follow-up turn.",
                planner_version="v3-memory",
                execution_model="carrier_workflow_with_planner_execution",
            ),
            rationale="Working memory indicates an active report-side deliverable, so preserve that workflow across the follow-up turn.",
            planning_metadata={
                "planner_version": "v3-memory",
                "memory_override": True,
                "deliverable_kind": deliverable_kind,
                "previous_workflow_type": session.get("previous_workflow_type"),
            },
        )

    if workflow_preference == WorkflowType.SCHEDULE_PLANNING and (
        mode in {"revision", "continuation", "correction"}
        or bool(session.get("current_schedule"))
    ):
        retrieval_sources = ["email", "calendar", "signals", "session_history"]
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.SCHEDULE_PLANNING,
            direct_workflow=WorkflowType.SCHEDULE_PLANNING,
            needed_context_sources=retrieval_sources,
            retrieval_plan=_build_retrieval_plan(
                sources=retrieval_sources,
                time_horizon="unspecified",
                target_date=None,
                target_label=working.get("timeframe") or working.get("deadline"),
                rationale="Working/session memory indicates an active scheduling task, so preserve schedule planning rather than reclassifying from isolated wording.",
                planner_version="v3-memory",
                execution_model="carrier_workflow_with_planner_execution",
            ),
            target_label=working.get("timeframe") or working.get("deadline"),
            rationale="Working/session memory indicates an active scheduling task, so preserve schedule planning rather than reclassifying from isolated wording.",
            planning_metadata={
                "planner_version": "v3-memory",
                "memory_override": True,
                "previous_workflow_type": session.get("previous_workflow_type"),
            },
        )

    return None


def _normalize_message(message: str) -> str:
    normalized = re.sub(r"\s+", " ", (message or "").strip().lower())
    for source, target in NORMALIZED_PHRASES:
        normalized = normalized.replace(source, target)
    return normalized


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _build_retrieval_plan(
    *,
    sources: list[str],
    time_horizon: PlanningTimeHorizon,
    target_date: date | None,
    target_label: str | None,
    rationale: str,
    planner_version: str,
    execution_model: str,
) -> RetrievalPlan:
    retrieval_sources: list[RetrievalSourceRequest] = []
    for priority, source in enumerate(_dedupe([source for source in sources if source])):
        retrieval_sources.append(
            RetrievalSourceRequest(
                source=source,
                required=True,
                priority=priority,
                rationale=f"Planner marked {source} as required for this request.",
            )
        )
    return RetrievalPlan(
        sources=retrieval_sources,
        time_horizon=time_horizon,
        target_date=target_date,
        target_label=target_label,
        rationale=rationale,
        planner_version=planner_version,
        execution_model=execution_model,
    )

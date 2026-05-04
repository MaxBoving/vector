from __future__ import annotations

from datetime import date, datetime
import json
import re
from typing import Any, Optional

from pydantic import BaseModel

from src.workflows.planner_semantics import infer_planner_semantics
from src.workflows.planning_types import PlanStep, PlannedSubtask, RequestPlan
from src.workflows.planning_time import resolve_time_horizon_target, resolve_date_window_semantic, _TIME_CUES
from src.workflows.types import WorkflowType


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
MEETING_PREP_KEYWORDS = (
    "prep for",
    "prep me for",
    "prep for my",
    "prepare for the meeting",
    "prepare for my meeting",
    "prepare for this meeting",
    "need to prepare for",
    "what do i need to prepare",
    "what should i prepare",
    "prepare me for",
    "meeting prep",
    "pre-meeting",
    "meeting brief for",
    "briefing for the meeting",
    "prep materials",
    "materials for the meeting",
    "materials for this meeting",
    "pre-read or agenda",
    "requires materials",
)
MORNING_BRIEF_KEYWORDS = (
    "morning brief",
    "daily brief",
    "morning digest",
    "daily digest",
    "my morning brief",
    "my daily brief",
)
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
WEEKLY_RECAP_KEYWORDS = (
    "recap my week",
    "recap this week",  # post-normalisation form of "recap my week"
    "week in review",
    "weekly summary",
    "what happened this week",
    "what did i accomplish",
    "how did this week go",
    "weekly recap",
    "end of week",
    "week recap",
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
    today: date,
) -> RequestPlan:
    """Convert a semantic IntentClassification into a RequestPlan."""
    if has_attachments:
        cl.needs_documents = True
        # Attachments are a structural tag: if the LLM defaulted to conversational,
        # override to report_generation — a document is present and needs synthesis.
        if cl.workflow == WorkflowType.CONVERSATIONAL:
            cl.workflow = WorkflowType.REPORT_GENERATION

    # Resolve a concrete date from the horizon label if the LLM didn't give us one
    normalized_message = _normalize_message(message)
    time_horizon, target_date, target_label = _detect_time_horizon(normalized_message)
    # Prefer the semantic classifier's horizon if it's more specific.
    # Keep the pattern-matched label when available — it preserves the user's
    # relative expression (e.g. "Friday this week") rather than the LLM's
    # resolved absolute label (e.g. "Friday May 9th").
    if cl.time_horizon != "unspecified":
        time_horizon = cl.time_horizon  # type: ignore[assignment]
        target_label = target_label or cl.target_label
    semantic_metadata = infer_planner_semantics(
        text=normalized_message,
        has_time_scope=time_horizon != "unspecified" or "week" in normalized_message,
        inbox_keywords=INBOX_KEYWORDS,
        calendar_keywords=CALENDAR_KEYWORDS,
        document_keywords=DOCUMENT_KEYWORDS,
        watch_keywords=WATCH_KEYWORDS,
        planning_keywords=PLANNING_KEYWORDS,
        scheduling_intent_keywords=SCHEDULING_INTENT_KEYWORDS,
        semantic_email_cues=SEMANTIC_EMAIL_CUES,
        semantic_calendar_cues=SEMANTIC_CALENDAR_CUES,
        semantic_document_cues=SEMANTIC_DOCUMENT_CUES,
        semantic_planning_cues=SEMANTIC_PLANNING_CUES,
        semantic_watch_cues=SEMANTIC_WATCH_CUES,
        finance_analysis_keywords=FINANCE_ANALYSIS_KEYWORDS,
        strategic_analysis_keywords=STRATEGIC_ANALYSIS_KEYWORDS,
        action_plan_keywords=ACTION_PLAN_KEYWORDS,
        escalation_keywords=ESCALATION_KEYWORDS,
        recommendation_keywords=RECOMMENDATION_KEYWORDS,
    ).as_metadata()

    base_sources = list(_CONTEXT_SOURCES_FOR_WORKFLOW.get(cl.workflow, ["session_history"]))

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


def plan_request(message: str, *, has_attachments: bool = False, unified_memory: dict[str, Any] | None = None) -> RequestPlan:
    today = datetime.now().astimezone().date()
    memory_plan = _plan_from_unified_memory(unified_memory, has_attachments=has_attachments)
    if memory_plan is not None:
        return memory_plan
    classification = _classify_intent_semantic(message, today, unified_memory=unified_memory)
    if classification is not None:
        return _build_plan_from_classification(classification, message, has_attachments, today)
    # LLM unavailable — fall back to keyword-based routing
    return _plan_request_keyword(message, has_attachments=has_attachments, unified_memory=unified_memory)


def _plan_request_keyword(message: str, *, has_attachments: bool = False, unified_memory: dict[str, Any] | None = None) -> RequestPlan:
    lowered = _normalize_message(message)
    working_memory = (unified_memory or {}).get("working_memory") or {}
    session_memory = (unified_memory or {}).get("session_memory") or {}
    workflow_preference = str(working_memory.get("workflow_preference") or "")
    active_deliverable = (working_memory.get("deliverable") or {}).get("kind")

    if workflow_preference == WorkflowType.REPORT_GENERATION and active_deliverable in {
        "artifact_revision",
        "email",
        "resolution_language",
        "execution_bundle",
    }:
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.REPORT_GENERATION,
            direct_workflow=WorkflowType.REPORT_GENERATION,
            needed_context_sources=["documents", "session_history", "signals"],
            time_horizon="unspecified",
            target_date=None,
            target_label=None,
            rationale="Working memory indicates an active report-side deliverable, so preserve the report workflow instead of replanning from raw phrasing.",
            planning_metadata={
                "planner_version": "v3-memory",
                "memory_override": True,
                "previous_workflow_type": session_memory.get("previous_workflow_type"),
                "deliverable_kind": active_deliverable,
            },
        )

    time_horizon, target_date, target_label = _detect_time_horizon(lowered)
    semantic_signals = infer_planner_semantics(
        text=lowered,
        has_time_scope=time_horizon != "unspecified" or "week" in lowered,
        inbox_keywords=INBOX_KEYWORDS,
        calendar_keywords=CALENDAR_KEYWORDS,
        document_keywords=DOCUMENT_KEYWORDS,
        watch_keywords=WATCH_KEYWORDS,
        planning_keywords=PLANNING_KEYWORDS,
        scheduling_intent_keywords=SCHEDULING_INTENT_KEYWORDS,
        semantic_email_cues=SEMANTIC_EMAIL_CUES,
        semantic_calendar_cues=SEMANTIC_CALENDAR_CUES,
        semantic_document_cues=SEMANTIC_DOCUMENT_CUES,
        semantic_planning_cues=SEMANTIC_PLANNING_CUES,
        semantic_watch_cues=SEMANTIC_WATCH_CUES,
        finance_analysis_keywords=FINANCE_ANALYSIS_KEYWORDS,
        strategic_analysis_keywords=STRATEGIC_ANALYSIS_KEYWORDS,
        action_plan_keywords=ACTION_PLAN_KEYWORDS,
        escalation_keywords=ESCALATION_KEYWORDS,
    )
    semantic_metadata = semantic_signals.as_metadata()
    mentions_inbox = semantic_signals.email
    mentions_calendar = semantic_signals.calendar
    mentions_documents = has_attachments or semantic_signals.documents
    mentions_finance_analysis = semantic_signals.finance_analysis
    mentions_strategic_analysis = semantic_signals.strategic_analysis
    mentions_action_plan = semantic_signals.action_plan
    mentions_escalation = semantic_signals.escalation
    mentions_recommendation = semantic_signals.recommendation
    mentions_watch = semantic_signals.watch
    has_schedule_context = mentions_inbox or mentions_calendar or time_horizon != "unspecified"
    mentions_scheduling = semantic_signals.planning
    is_schedule_planning_request = mentions_scheduling and (
        has_schedule_context or _contains_any(lowered, ("schedule", "plan my day", "plan my week"))
    )

    if mentions_finance_analysis or mentions_strategic_analysis or (mentions_escalation and mentions_action_plan):
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.REPORT_GENERATION,
            direct_workflow=WorkflowType.REPORT_GENERATION,
            needed_context_sources=["email", "calendar", "documents", "session_history", "signals"],
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale="The request asks for decision support or issue analysis, not a watch-only or scheduling response.",
            planning_metadata={
                "planner_version": "v2",
                "semantic_fallback_used": any(semantic_metadata.values()),
                "semantic_signals": semantic_metadata,
                "mentions_finance_analysis": mentions_finance_analysis,
                "mentions_strategic_analysis": mentions_strategic_analysis,
                "mentions_action_plan": mentions_action_plan,
                "mentions_escalation": mentions_escalation,
                "mentions_recommendation": mentions_recommendation,
            },
        )

    should_compound_plan = is_schedule_planning_request and (
        mentions_inbox or mentions_calendar or mentions_documents or time_horizon in {"this_week", "next_week", "week_after_next"}
    )
    if should_compound_plan:
        needed_context_sources: list[str] = []
        subtasks: list[PlannedSubtask] = []
        execution_steps: list[PlanStep] = []

        if mentions_inbox:
            needed_context_sources.append("email")
            subtasks.append(
                PlannedSubtask(
                    key="scan_inbox",
                    kind="email_watch",
                    description="Review recent executive email threads for actionable items, deadlines, and asks.",
                    context_sources=["email", "signals"],
                )
            )
            execution_steps.append(
                PlanStep(
                    key="scan_inbox",
                    kind="email_watch",
                    description="Gather actionable inbox evidence for the requested planning window.",
                    context_sources=["email", "signals"],
                )
            )

        if mentions_calendar or time_horizon in {"this_week", "next_week"}:
            needed_context_sources.append("calendar")
            subtasks.append(
                PlannedSubtask(
                    key="review_calendar",
                    kind="calendar_watch",
                    description="Review meetings and calendar commitments that constrain the planning window.",
                    context_sources=["calendar"],
                )
            )
            execution_steps.append(
                PlanStep(
                    key="review_calendar",
                    kind="calendar_watch",
                    description="Gather meetings and calendar constraints for the requested planning window.",
                    context_sources=["calendar"],
                )
            )

        if mentions_documents:
            needed_context_sources.append("documents")
            subtasks.append(
                PlannedSubtask(
                    key="review_documents",
                    kind="document_context",
                    description="Fold attached or referenced documents into the planning evidence set.",
                    context_sources=["documents"],
                )
            )
            execution_steps.append(
                PlanStep(
                    key="review_documents",
                    kind="document_context",
                    description="Gather attached or referenced document evidence for the planning lane.",
                    context_sources=["documents"],
                )
            )

        subtasks.append(
            PlannedSubtask(
                key="build_schedule",
                kind="schedule_synthesis",
                description="Synthesize inbox, calendar, and recent context into a concrete schedule proposal.",
                context_sources=["email", "calendar", "session_history", "documents"],
            )
        )
        execution_steps.extend(
            [
                PlanStep(
                    key="synthesize_planning_candidates",
                    kind="schedule_candidate_synthesis",
                    description="Turn gathered evidence into concrete planning candidates.",
                    context_sources=["email", "calendar", "documents", "session_history"],
                ),
                PlanStep(
                    key="place_candidates",
                    kind="schedule_slotting",
                    description="Place planning candidates into slots inside the requested planning window.",
                    context_sources=["calendar"],
                ),
                PlanStep(
                    key="build_schedule",
                    kind="schedule_synthesis",
                    description="Assemble the final schedule proposal from placed candidates and supporting evidence.",
                    context_sources=["email", "calendar", "documents", "session_history"],
                ),
            ]
        )

        compound_target = WorkflowType.SCHEDULE_PLANNING
        return RequestPlan(
            mode="compound_plan",
            target_workflow=compound_target,
            subtasks=subtasks,
            execution_steps=execution_steps,
            needed_context_sources=_dedupe(needed_context_sources + ["email", "session_history", "documents", "signals"]),
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale="The request mixes planning with multiple context sources, so the planner should gather reusable evidence before producing a schedule inside the planning workflow path.",
            planning_metadata={
                "mentions_inbox": mentions_inbox,
                "mentions_calendar": mentions_calendar or time_horizon in {"this_week", "next_week", "week_after_next"},
                "mentions_documents": mentions_documents,
                "mentions_planning": is_schedule_planning_request,
                "semantic_fallback_used": any(semantic_metadata.values()),
                "semantic_signals": semantic_metadata,
                "execution_model": "carrier_workflow_with_planner_execution",
                "planner_version": "v2",
            },
        )

    mentions_meeting_prep = _contains_any(lowered, MEETING_PREP_KEYWORDS) and mentions_calendar
    if mentions_meeting_prep:
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.MEETING_PREP,
            direct_workflow=WorkflowType.MEETING_PREP,
            needed_context_sources=["email", "calendar", "documents", "signals", "session_history"],
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale="The request is a direct meeting preparation ask.",
            planning_metadata={
                "planner_version": "v2",
                "execution_model": "carrier_workflow_with_planner_execution",
                "semantic_fallback_used": any(semantic_metadata.values()),
                "semantic_signals": semantic_metadata,
            },
        )

    if is_schedule_planning_request:
        direct_planning_workflow = WorkflowType.SCHEDULE_PLANNING
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=direct_planning_workflow,
            direct_workflow=direct_planning_workflow,
            needed_context_sources=["email", "calendar", "signals", "session_history"],
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale="The request is a direct planning ask — email is always included because inbox signal informs schedule candidates.",
            planning_metadata={
                "planner_version": "v2",
                "execution_model": "carrier_workflow_with_planner_execution",
                "semantic_fallback_used": any(semantic_metadata.values()),
                "semantic_signals": semantic_metadata,
            },
        )

    if _contains_any(lowered, MORNING_BRIEF_KEYWORDS):
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.MORNING_BRIEF,
            direct_workflow=WorkflowType.MORNING_BRIEF,
            needed_context_sources=["email", "calendar", "signals"],
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale="The request is a direct morning or daily brief ask.",
            planning_metadata={
                "planner_version": "v1",
                "execution_model": "carrier_workflow_with_planner_execution",
                "semantic_fallback_used": any(semantic_metadata.values()),
                "semantic_signals": semantic_metadata,
            },
        )

    if _contains_any(lowered, WEEKLY_RECAP_KEYWORDS):
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.WEEKLY_RECAP,
            direct_workflow=WorkflowType.WEEKLY_RECAP,
            needed_context_sources=["email", "calendar", "signals", "session_history"],
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale="The request is a backward-looking weekly recap.",
            planning_metadata={
                "planner_version": "v2",
                "execution_model": "carrier_workflow_with_planner_execution",
                "semantic_fallback_used": any(semantic_metadata.values()),
                "semantic_signals": semantic_metadata,
            },
        )

    if mentions_inbox and mentions_calendar and (mentions_watch or time_horizon != "unspecified"):
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.MORNING_BRIEF,
            direct_workflow=WorkflowType.MORNING_BRIEF,
            needed_context_sources=["email", "calendar", "signals"],
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale="The request asks for a combined watch brief rather than a synthesized plan.",
            planning_metadata={"planner_version": "v1", "semantic_fallback_used": any(semantic_metadata.values()), "semantic_signals": semantic_metadata},
        )

    if mentions_inbox:
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.EMAIL_WATCHER,
            direct_workflow=WorkflowType.EMAIL_WATCHER,
            needed_context_sources=["email", "signals"],
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale="The request is primarily an inbox review.",
            planning_metadata={"planner_version": "v1", "semantic_fallback_used": any(semantic_metadata.values()), "semantic_signals": semantic_metadata},
        )

    # Only route to calendar briefing when there's an explicit watch/review intent or time horizon;
    # a bare mention of "meeting" in a general question should fall through to report generation.
    if mentions_calendar and (mentions_watch or time_horizon != "unspecified"):
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.CALENDAR_BRIEFING,
            direct_workflow=WorkflowType.CALENDAR_BRIEFING,
            needed_context_sources=["calendar", "signals"],
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale="The request is primarily a calendar or meeting briefing.",
            planning_metadata={"planner_version": "v1", "semantic_fallback_used": any(semantic_metadata.values()), "semantic_signals": semantic_metadata},
        )

    return RequestPlan(
        mode="direct_workflow",
        target_workflow=WorkflowType.REPORT_GENERATION,
        direct_workflow=WorkflowType.REPORT_GENERATION,
        needed_context_sources=["documents", "session_history"],
        rationale="No reusable compound planning path was detected.",
        planning_metadata={"planner_version": "v2-keyword-fallback", "semantic_fallback_used": any(semantic_metadata.values()), "semantic_signals": semantic_metadata},
    )


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
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.REPORT_GENERATION,
            direct_workflow=WorkflowType.REPORT_GENERATION,
            needed_context_sources=["documents", "session_history", "signals"],
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
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.SCHEDULE_PLANNING,
            direct_workflow=WorkflowType.SCHEDULE_PLANNING,
            needed_context_sources=["email", "calendar", "signals", "session_history"],
            target_label=working.get("timeframe") or working.get("deadline"),
            rationale="Working/session memory indicates an active scheduling task, so preserve schedule planning rather than reclassifying from isolated wording.",
            planning_metadata={
                "planner_version": "v3-memory",
                "memory_override": True,
                "previous_workflow_type": session.get("previous_workflow_type"),
            },
        )

    return None


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _detect_time_horizon(text: str) -> tuple[str, date | None, str | None]:
    # Fast path: try deterministic pattern matching first
    horizon, target_date, label = resolve_time_horizon_target(
        text,
        this_week_patterns=THIS_WEEK_PATTERNS,
        next_week_patterns=NEXT_WEEK_PATTERNS,
    )
    if horizon != "unspecified":
        return horizon, target_date, label

    # Semantic fallback: only when the text has time cues that the patterns missed
    if _TIME_CUES.search(text):
        from datetime import datetime as _dt
        today = _dt.now().astimezone().date()
        result = resolve_date_window_semantic(text, today)
        if result and result[0] != "unspecified":
            return result[0], None, result[1]

    return "unspecified", None, None


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

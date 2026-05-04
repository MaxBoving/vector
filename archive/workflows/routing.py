from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from src.api.schemas import AssistantQueryRequest
from src.workflows.planning_types import RequestPlan
from src.workflows.request_planner import plan_request
from src.workflows.types import WorkflowType


class RouteFamily(str, Enum):
    WATCH = "watch"
    PLAN = "plan"
    ACT = "act"
    REPORT = "report"


class RouteSubintent(str, Enum):
    EMAIL_WATCH = "email_watch"
    CALENDAR_WATCH = "calendar_watch"
    WEEKLY_WATCH = "weekly_watch"
    DAY_SCHEDULE = "day_schedule"
    WEEK_SCHEDULE = "week_schedule"
    MEETING_PREP = "meeting_prep"
    CREATE_CALENDAR_EVENT = "create_calendar_event"
    UPDATE_CALENDAR_EVENT = "update_calendar_event"
    DRAFT_EMAIL = "draft_email"
    SEND_EMAIL = "send_email"
    FINANCIAL_REPORT = "financial_report"
    DOCUMENT_EXPLANATION = "document_explanation"
    EXECUTIVE_BRIEF = "executive_brief"


class RouteDecision(BaseModel):
    primary_intent: RouteFamily
    subintents: List[RouteSubintent] = Field(default_factory=list)
    workflow_chain: List[str] = Field(default_factory=list)
    request_plan: RequestPlan | None = None
    requires_write: bool = False
    requires_approval: bool = False
    clarification_needed: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    rationale: str = ""


_WRITE_INTENT_SYSTEM = (
    "You are a write-action classifier for an executive AI assistant. "
    "Determine if the user is asking to CREATE or SEND something — not just analyze or plan. "
    "Return JSON only: "
    '{"is_write": bool, "action_type": "calendar"|"email"|null, "rationale": "one sentence"}'
    "\n\nCalendar write = scheduling/creating/moving a meeting or event. "
    "Email write = drafting or sending an email to someone, OR delegating a task by email. "
    "'Delegate X' or 'Delegate X to Y' = email write — it means compose a delegation email. "
    "If the request is to VIEW, ANALYZE, PLAN, or PREPARE — it is NOT a write action."
)


def _classify_write_intent(message: str) -> tuple[bool, Optional[str]]:
    """Return (is_write_action, action_type) using LLM, falling back to narrow literal phrases."""
    try:
        from src.core.llm import LLMClient
        raw = LLMClient().complete(f'User message: "{message}"', _WRITE_INTENT_SYSTEM)
        match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            if data.get("is_write"):
                action_type = data.get("action_type") or None
                if action_type in ("calendar", "email", None):
                    return True, action_type
        return False, None
    except Exception:
        # Narrow literal fallback — only the most unambiguous phrases
        lowered = message.lower()
        _cal = ("schedule a meeting", "add a meeting", "create an event", "add to my calendar", "reschedule a meeting")
        _email = ("send an email", "draft an email", "write an email", "email to")
        is_cal = any(p in lowered for p in _cal)
        is_email = any(p in lowered for p in _email)
        if is_cal:
            return True, "calendar"
        if is_email:
            return True, "email"
        return False, None
def classify_route(
    payload: AssistantQueryRequest,
    *,
    unified_memory: dict[str, Any] | None = None,
    precomputed_request_plan: RequestPlan | None = None,
    precomputed_write_intent: tuple[bool, Optional[str]] | None = None,
) -> RouteDecision:
    message = (payload.message or "").strip()
    request_plan = precomputed_request_plan or plan_request(
        message,
        has_attachments=bool(payload.attachments),
        unified_memory=unified_memory,
    )

    # Respect the semantic planner first for planning-style requests. This avoids
    # brittle write-intent hijacks on generic asks like "make me a schedule".
    # Covers both direct plans (direct_workflow set) and compound plans (target_workflow set,
    # direct_workflow=None) so that write-intent detection never fires on planning requests.
    if (
        request_plan.direct_workflow == WorkflowType.SCHEDULE_PLANNING
        or request_plan.target_workflow == WorkflowType.SCHEDULE_PLANNING
    ):
        schedule_subintent = (
            RouteSubintent.WEEK_SCHEDULE
            if (request_plan.time_horizon or "") in {"this_week", "next_week", "week_after_next"}
            else RouteSubintent.DAY_SCHEDULE
        )
        return RouteDecision(
            primary_intent=RouteFamily.PLAN,
            subintents=[schedule_subintent],
            workflow_chain=[WorkflowType.SCHEDULE_PLANNING],
            request_plan=request_plan,
            rationale=request_plan.rationale or "Semantic planner routed this request to schedule planning.",
        )

    if request_plan.direct_workflow == WorkflowType.MEETING_PREP:
        return RouteDecision(
            primary_intent=RouteFamily.PLAN,
            subintents=[RouteSubintent.MEETING_PREP],
            workflow_chain=[WorkflowType.MEETING_PREP],
            request_plan=request_plan,
            rationale=request_plan.rationale or "Semantic planner routed this request to meeting prep.",
        )

    # Trust the semantic planner's report_generation decision over write-intent detection.
    # Compound requests ("analyze X and draft an email") belong in report_generation — the
    # report agent can produce email drafts as part of its output. Only route to ACT when
    # the planner did NOT identify this as a report request.
    if request_plan.direct_workflow == WorkflowType.REPORT_GENERATION:
        return RouteDecision(
            primary_intent=RouteFamily.REPORT,
            subintents=[RouteSubintent.EXECUTIVE_BRIEF],
            workflow_chain=[WorkflowType.REPORT_GENERATION],
            request_plan=request_plan,
            rationale=request_plan.rationale or "Semantic planner routed this request to report generation.",
        )

    # ACT family: semantic write-intent detection (only reached when planner did not
    # classify the request as report_generation, schedule_planning, or meeting_prep).
    if precomputed_write_intent is not None:
        is_write, action_type = precomputed_write_intent
    else:
        is_write, action_type = _classify_write_intent(message)

    if is_write:
        subintents: list[RouteSubintent] = []
        if action_type == "calendar":
            subintents.append(RouteSubintent.CREATE_CALENDAR_EVENT)
        elif action_type == "email":
            subintents.append(RouteSubintent.DRAFT_EMAIL)
        return RouteDecision(
            primary_intent=RouteFamily.ACT,
            subintents=subintents,
            workflow_chain=[],
            requires_write=True,
            requires_approval=True,
            rationale="Write action detected: creating or sending a calendar event or email.",
        )

    if request_plan.is_compound:
        return RouteDecision(
            primary_intent=RouteFamily.PLAN,
            subintents=_subintents_for_plan(request_plan),
            workflow_chain=_workflow_sequence_for_plan(request_plan),
            request_plan=request_plan,
            rationale=request_plan.rationale,
        )

    # Attachments always go to document explanation
    if payload.attachments:
        return RouteDecision(
            primary_intent=RouteFamily.REPORT,
            subintents=[RouteSubintent.DOCUMENT_EXPLANATION],
            workflow_chain=[WorkflowType.DOCUMENT_EXPLANATION],
            request_plan=request_plan,
            rationale="File attachment present — routing to document explanation.",
        )

    # Trust plan_request's semantic workflow decision for everything else
    _WORKFLOW_TO_ROUTE: dict[str, RouteDecision] = {}  # built inline below

    if request_plan.direct_workflow == WorkflowType.WEEKLY_RECAP:
        return RouteDecision(
            primary_intent=RouteFamily.WATCH,
            subintents=[RouteSubintent.EMAIL_WATCH, RouteSubintent.CALENDAR_WATCH, RouteSubintent.WEEKLY_WATCH],
            workflow_chain=[WorkflowType.WEEKLY_RECAP],
            request_plan=request_plan,
            rationale=request_plan.rationale,
        )

    if request_plan.direct_workflow == WorkflowType.MORNING_BRIEF:
        return RouteDecision(
            primary_intent=RouteFamily.WATCH,
            subintents=[RouteSubintent.EMAIL_WATCH, RouteSubintent.CALENDAR_WATCH, RouteSubintent.WEEKLY_WATCH],
            workflow_chain=[WorkflowType.MORNING_BRIEF],
            request_plan=request_plan,
            rationale=request_plan.rationale,
        )

    if request_plan.direct_workflow == WorkflowType.CALENDAR_BRIEFING:
        return RouteDecision(
            primary_intent=RouteFamily.WATCH,
            subintents=[RouteSubintent.CALENDAR_WATCH],
            workflow_chain=[WorkflowType.CALENDAR_BRIEFING],
            request_plan=request_plan,
            rationale=request_plan.rationale,
        )

    if request_plan.direct_workflow == WorkflowType.EMAIL_WATCHER:
        return RouteDecision(
            primary_intent=RouteFamily.WATCH,
            subintents=[RouteSubintent.EMAIL_WATCH],
            workflow_chain=[WorkflowType.EMAIL_WATCHER],
            request_plan=request_plan,
            rationale=request_plan.rationale,
        )

    if request_plan.direct_workflow == WorkflowType.EMAIL_INGESTION:
        return RouteDecision(
            primary_intent=RouteFamily.WATCH,
            subintents=[RouteSubintent.EMAIL_WATCH],
            workflow_chain=[WorkflowType.EMAIL_INGESTION],
            request_plan=request_plan,
            rationale=request_plan.rationale,
        )

    # Default: conversational response.
    # report_generation is only used when the semantic planner explicitly identifies
    # a structured artifact request. Everything else gets a plain conversational answer.
    return RouteDecision(
        primary_intent=RouteFamily.REPORT,
        subintents=[],
        workflow_chain=[WorkflowType.CONVERSATIONAL],
        request_plan=request_plan,
        rationale=request_plan.rationale or "No artifact intent detected — responding conversationally.",
    )


def _subintents_for_plan(request_plan: RequestPlan) -> list[RouteSubintent]:
    subintents: list[RouteSubintent] = []
    subtask_keys = {subtask.key for subtask in request_plan.subtasks}
    if "scan_inbox" in subtask_keys:
        subintents.append(RouteSubintent.EMAIL_WATCH)
    if "review_calendar" in subtask_keys:
        subintents.append(RouteSubintent.CALENDAR_WATCH)
    subintents.append(
        RouteSubintent.WEEK_SCHEDULE
        if request_plan.time_horizon in {"this_week", "next_week", "week_after_next"}
        else RouteSubintent.DAY_SCHEDULE
    )
    if request_plan.time_horizon in {"this_week", "next_week", "week_after_next"}:
        subintents.append(RouteSubintent.WEEKLY_WATCH)
    return subintents


def _workflow_sequence_for_plan(request_plan: RequestPlan) -> list[str]:
    workflow_chain: list[str] = []
    subtask_keys = {subtask.key for subtask in request_plan.subtasks}
    if "scan_inbox" in subtask_keys:
        workflow_chain.append(WorkflowType.EMAIL_WATCHER)
    if "review_calendar" in subtask_keys:
        workflow_chain.append(WorkflowType.CALENDAR_BRIEFING)
    if request_plan.target_workflow not in workflow_chain:
        workflow_chain.append(request_plan.target_workflow)
    return workflow_chain

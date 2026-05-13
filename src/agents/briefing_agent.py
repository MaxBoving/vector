import logging
import os
import re
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

from src.core.llm import DEFAULT_ANTHROPIC_MODEL
from src.presentation import PresentationBlock, PresentationSpec, normalize_and_validate_presentation_spec
from src.tools.registry import ToolRegistry
from src.workflows.planning_time import build_planning_window
from src.workflows.retrieval_manifest import RetrievalManifest
from src.workflows.planning_types import PlanningWindow
from src.workflows.types import WorkflowType
from src.workflows.action_items import (
    action_item_text,
    filter_structured_watch_for_window,
    normalize_structured_watch,
    unresolved_action_items,
)
from src.workflows.follow_up_planner import build_follow_up_candidate, select_follow_up_candidates
from src.workflows.semantic_followups import (
    SemanticContext,
    build_semantic_context,
    build_semantic_follow_up_candidates,
    build_semantic_question_options,
)

from .base import BaseAgent
from .schemas import (
    AgentInput,
    AgentMetadata,
    AgentOutput,
    complete_stage_action,
    complete_workflow_action,
    tool_action,
    write_artifact_action,
)


BRIEFING_AGENT_SOURCE_VERSION = "briefing_agent@2026-05-12.morning_brief_fallback_v2"


class BriefSection(BaseModel):
    label: str
    content: Optional[str] = None
    items: List[str] = Field(default_factory=list)


class BriefAnswer(BaseModel):
    title: str
    summary: str
    sections: List[BriefSection] = Field(default_factory=list)


class BriefTrust(BaseModel):
    confidence: str
    confidence_score: float
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    data_quality: str
    calculation_used: bool = False
    missing_context: List[str] = Field(default_factory=list)
    evidence_state: Optional[str] = None
    evidence_reasons: List[str] = Field(default_factory=list)
    safe_to_act: Optional[bool] = None
    question_options: List[Dict[str, Any]] = Field(default_factory=list)
    semantic_context: Optional[SemanticContext] = None


class PresentationSection(BaseModel):
    title: str
    content: Optional[str] = None
    items: List[str] = Field(default_factory=list)


class WeeklyPlanBlock(BaseModel):
    title: str
    kind: Optional[str] = None
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    day_label: Optional[str] = None
    time_window: Optional[str] = None
    reason: Optional[str] = None
    source_refs: List[str] = Field(default_factory=list)
    confidence: Optional[str] = None


class WeeklyPlanMeeting(BaseModel):
    title: str
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    attendees: List[str] = Field(default_factory=list)


class WeeklyPlanWindow(BaseModel):
    horizon: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    timezone: Optional[str] = None
    workday_start: Optional[str] = None
    workday_end: Optional[str] = None
    span_days: Optional[int] = None


class WeeklyPlanPresentation(BaseModel):
    planning_window: Optional[WeeklyPlanWindow] = None
    blocks: List[WeeklyPlanBlock] = Field(default_factory=list)
    deadlines: List[str] = Field(default_factory=list)
    meetings: List[WeeklyPlanMeeting] = Field(default_factory=list)
    follow_ups: List[str] = Field(default_factory=list)


class CalendarEvent(BaseModel):
    title: str
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    day_label: Optional[str] = None
    attendees: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    kind: Optional[str] = None  # "meeting" | "deadline" | "focus"


class CalendarPresentation(BaseModel):
    events: List[CalendarEvent] = Field(default_factory=list)
    follow_ups: List[str] = Field(default_factory=list)


class BriefPresentation(BaseModel):
    mode: Optional[str] = None
    variant: Optional[str] = None
    preamble: Optional[str] = None
    summary: Optional[str] = None
    priorities: List[PresentationSection] = Field(default_factory=list)
    recommended_actions: List[PresentationSection] = Field(default_factory=list)
    risks: List[PresentationSection] = Field(default_factory=list)
    details: List[PresentationSection] = Field(default_factory=list)
    weekly_plan: Optional[WeeklyPlanPresentation] = None
    calendar: Optional[CalendarPresentation] = None


class BriefPayload(BaseModel):
    answer: BriefAnswer
    trust: BriefTrust
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    presentation: Optional[BriefPresentation] = None


class BriefingWorkflowProfile(BaseModel):
    workflow_type: str
    presentation_mode: str
    presentation_variant: Optional[str] = None
    live_context_kind: str
    summary_kind: str
    title_kind: str


class BriefingAgent(BaseAgent):
    COMPLETION_MODEL = os.getenv("BRIEFING_AGENT_MODEL", DEFAULT_ANTHROPIC_MODEL)

    WORKFLOW_PROFILES: dict[str, BriefingWorkflowProfile] = {
        "email_ingestion": BriefingWorkflowProfile(
            workflow_type="email_ingestion",
            presentation_mode="brief",
            presentation_variant="inbox_watch",
            live_context_kind="email",
            summary_kind="email",
            title_kind="email",
        ),
        "email_watcher": BriefingWorkflowProfile(
            workflow_type="email_watcher",
            presentation_mode="brief",
            presentation_variant="inbox_watch",
            live_context_kind="email",
            summary_kind="email",
            title_kind="email",
        ),
        "calendar_briefing": BriefingWorkflowProfile(
            workflow_type="calendar_briefing",
            presentation_mode="calendar",
            presentation_variant="day_grid",
            live_context_kind="calendar",
            summary_kind="calendar",
            title_kind="calendar",
        ),
        "morning_brief": BriefingWorkflowProfile(
            workflow_type="morning_brief",
            presentation_mode="brief",
            presentation_variant="weekly_watch",
            live_context_kind="watch",
            summary_kind="morning_brief",
            title_kind="morning_brief",
        ),
        "schedule_planning": BriefingWorkflowProfile(
            workflow_type="schedule_planning",
            presentation_mode="schedule",
            presentation_variant="timeline",  # overridden dynamically by _presentation_variant
            live_context_kind="watch",
            summary_kind="schedule",
            title_kind="schedule",
        ),
        "weekly_recap": BriefingWorkflowProfile(
            workflow_type="weekly_recap",
            presentation_mode="brief",
            presentation_variant="weekly_recap",
            live_context_kind="watch",
            summary_kind="weekly_recap",
            title_kind="weekly_recap",
        ),
        "meeting_prep": BriefingWorkflowProfile(
            workflow_type="meeting_prep",
            presentation_mode="brief",
            presentation_variant="meeting_prep",
            live_context_kind="watch",
            summary_kind="meeting_prep",
            title_kind="meeting_prep",
        ),
    }

    metadata = AgentMetadata(
        name="briefing_agent",
        description="Generates concise executive briefings for inbox, calendar, and scheduled digests.",
        stage="synthesizer",
        allowed_tools=[
            "structured_completion",
            "write_artifact",
            "read_email_threads",
            "read_calendar_events",
            "google_drive_search",
            "google_drive_read",
            "crm_deal_context",
            "memory_management",
            "get_live_context",
            "write_thread_entry",
            "get_situational_profile",
            "update_situational_profile",
        ],
        tags=["briefing", "email", "calendar", "executive"],
    )

    def __init__(self, tools: ToolRegistry):
        self.tools = tools

    async def run(self, agent_input: AgentInput, **kwargs: Any) -> AgentOutput:
        workflow_type = agent_input.workflow_state.workflow_type
        event_payload = agent_input.workflow_state.metadata.get("event_payload", {})
        context = agent_input.context or {}
        # Merge top-level keys into prepared_context so _briefing_prompt can access
        # retrieval_manifest and entity_context regardless of which layer they land on.
        prepared_context = dict(context.get("prepared_context") or {})
        for _top_key in (
            "retrieval_manifest",
            "entity_context",
            "proactive_observations_block",
            "unified_memory",
            "request_plan",
            "ranked_threads",
            "upcoming_events",
            "structured_watch",
            "planning_context",
        ):
            if _top_key not in prepared_context and _top_key in context:
                prepared_context[_top_key] = context[_top_key]
            if _top_key not in event_payload and _top_key in prepared_context:
                event_payload[_top_key] = prepared_context[_top_key]

        resolved_clarifications: dict[str, str] = dict(agent_input.workflow_state.metadata.get("resolved_clarifications") or {})
        presentation_style_gate = self._presentation_style_gate(
            workflow_type=workflow_type,
            ceo_id=agent_input.workflow_state.ceo_id,
            resolved_clarifications=resolved_clarifications,
        )
        if presentation_style_gate:
            question, clarification_options = presentation_style_gate
            return AgentOutput(
                agent_name=self.metadata.name,
                stage=agent_input.stage,
                success=True,
                summary="Clarifying the preferred presentation style before generating.",
                actions=[],
                structured_output={
                    "presentation": {
                        "preamble": question + (
                            "\n" + "\n".join(f"— {opt['apply_text']}" for opt in clarification_options)
                            if clarification_options else ""
                        ),
                    },
                    "clarification_options": clarification_options,
                },
                metadata={
                    "workflow_type": workflow_type,
                    "response_type": "clarification",
                    "needs_clarification": True,
                    "original_query": agent_input.task_input or "",
                    "clarification_options": clarification_options,
                },
            )

        skip_gate = (
            agent_input.workflow_state.metadata.get("skip_clarification_gate")
            or agent_input.workflow_state.metadata.get("clarification_policy_continue")
            or "briefing_completion" in context
            or workflow_type == "meeting_prep"
        )
        if workflow_type == "meeting_prep" and not skip_gate:
            meeting_questions = self._detect_meeting_prep_gaps(
                event_payload=event_payload,
                task_input=agent_input.task_input or "",
            )
            if meeting_questions:
                meeting_options = self._meeting_prep_clarification_options(meeting_questions, event_payload)
                return AgentOutput(
                    agent_name=self.metadata.name,
                    stage=agent_input.stage,
                    success=True,
                    summary="Meeting prep context needed before generating brief.",
                    actions=[],
                    structured_output={
                        "presentation": {
                            "preamble": meeting_questions[0] + (
                                "\n" + "\n".join(f"— {opt['apply_text']}" for opt in meeting_options)
                                if meeting_options else ""
                            ),
                        },
                        "clarification_options": meeting_options,
                    },
                    metadata={
                        "workflow_type": workflow_type,
                        "response_type": "clarification",
                        "needs_clarification": True,
                        "original_query": agent_input.task_input or "",
                        "clarification_options": meeting_options,
                    },
                )

        if workflow_type == "schedule_planning" and not skip_gate:
            ambiguity_questions = self._detect_schedule_ambiguity(
                event_payload=event_payload,
                task_input=agent_input.task_input or "",
                history=prepared_context.get("history", []),
            )
            if ambiguity_questions:
                clarification_options = self._schedule_clarification_options(ambiguity_questions)
                return AgentOutput(
                    agent_name=self.metadata.name,
                    stage=agent_input.stage,
                    success=True,
                    summary="Schedule intent unclear — asking before generating.",
                    actions=[],
                    structured_output={
                        "presentation": {
                            "preamble": ambiguity_questions[0] + (
                                "\n" + "\n".join(f"— {opt['apply_text']}" for opt in clarification_options)
                                if clarification_options else ""
                            ),
                        },
                        "clarification_options": clarification_options,
                    },
                    metadata={
                        "workflow_type": workflow_type,
                        "response_type": "clarification",
                        "needs_clarification": True,
                        "original_query": agent_input.task_input or "",
                        "clarification_options": clarification_options,
                    },
                )

        has_live_event_context = self._has_live_event_context(workflow_type, event_payload)
        sparse_briefing_workflows = {"morning_brief", "weekly_recap", "calendar_briefing"}
        if "briefing_completion" not in context and not has_live_event_context:
            if workflow_type not in sparse_briefing_workflows:
                return AgentOutput(
                    agent_name=self.metadata.name,
                    stage=agent_input.stage,
                    success=True,
                    summary="Requesting structured briefing completion.",
                    actions=[
                        tool_action(
                            "structured_completion",
                            result_key="briefing_completion",
                            prompt=self._briefing_prompt(workflow_type, event_payload, prepared_context, agent_input.task_input or ""),
                            system_prompt=(
                                (agent_input.system_prompt or "") + "\n\n"
                                "You are a CEO briefing agent. Produce a concise executive briefing payload that is calm, clear, "
                                "and grounded in the supplied context. Focus on what matters, what changed, and what should happen next. "
                                "The retrieval_manifest in the prompt tells you exactly what was found — use it to write a specific, "
                                "first-person preamble that names the actual sources. Never write a generic opener."
                            ).strip(),
                            response_model=BriefPayload,
                            model=self.COMPLETION_MODEL,
                        )
                    ],
                    metadata={"workflow_type": workflow_type, "response_type": "report"},
                )

        payload = self._generate_payload(
            workflow_type=workflow_type,
            event_payload=event_payload,
            prepared_context=prepared_context,
            completion=context.get("briefing_completion"),
            task_input=agent_input.task_input or "",
        )
        payload = self._apply_morning_brief_meeting_fallback(
            payload,
            workflow_type=workflow_type,
            event_payload=event_payload,
        )
        payload = self._normalize_meeting_prep_payload(
            payload,
            workflow_type=workflow_type,
            event_payload=event_payload,
        )
        payload = self._apply_presentation_metadata(
            payload,
            event_payload=event_payload,
            workflow_type=workflow_type,
            ceo_id=agent_input.workflow_state.ceo_id,
            resolved_clarifications=resolved_clarifications,
        )
        presentation_spec, presentation_quality = normalize_and_validate_presentation_spec(
            self._build_presentation_spec(
                payload=payload,
                workflow_type=workflow_type,
                task_input=agent_input.task_input or "",
                ceo_id=agent_input.workflow_state.ceo_id,
                resolved_clarifications=resolved_clarifications,
            )
        )
        markdown = self._to_markdown(payload)
        post_actions = self._extract_memory_save_actions(payload)
        post_actions.append(self._build_thread_entry_action(agent_input=agent_input, workflow_type=workflow_type, payload=payload))
        situational_update = self._extract_situational_updates(
            task_input=agent_input.task_input or "",
            payload=payload,
            workflow_type=workflow_type,
        )
        if situational_update:
            post_actions.append(
                tool_action(
                    "update_situational_profile",
                    updated_by=self.metadata.name,
                    **situational_update,
                )
            )
        return AgentOutput(
            agent_name=self.metadata.name,
            stage=agent_input.stage,
            success=True,
            summary=payload.answer.summary,
            content=markdown,
            structured_output=payload.model_dump(),
            actions=[
                write_artifact_action("synthesizer", "executive_summary.md", markdown, source="briefing_agent", hidden=True),
                *post_actions,
                complete_stage_action(agent_input.stage),
                complete_workflow_action(response_type="report"),
            ],
            metadata={
                "workflow_type": workflow_type,
                "response_type": "report",
                "presentation_spec": presentation_spec.model_dump(mode="json"),
                "presentation_quality": presentation_quality.model_dump(mode="json"),
            },
        )

    def _detect_schedule_ambiguity(
        self,
        *,
        event_payload: Dict[str, Any],
        task_input: str,
        history: List[Dict[str, Any]],
    ) -> List[str]:
        """
        Inspect what we know before generating a schedule and surface any intent
        ambiguities that would materially change the output.
        Returns [] when context is sufficient — gate passes silently.
        Only asks once per session: if recent history already answered these, skip.
        """
        questions: List[str] = []

        planning_context = event_payload.get("planning_context") or {}
        horizon = str(planning_context.get("time_horizon") or "unspecified")
        ranked_threads = event_payload.get("ranked_threads") or []
        structured_watch = self._normalized_structured_watch(event_payload)
        upcoming_events = (event_payload.get("upcoming_events") or [])
        deadlines = [item.get("deadline") for item in structured_watch.get("deadlines", []) if item.get("deadline")]
        asks = [item.get("ask") for item in structured_watch.get("asks", []) if item.get("ask")]

        # Don't re-ask if a schedule was already clarified in this session
        recent_text = " ".join(
            str(item.get("query") or item.get("message") or "")
            for item in (history or [])[-4:]
        ).lower()
        if any(kw in recent_text for kw in ("compact", "relaxed", "one task", "spread", "inbox only", "calendar only")):
            return []

        is_multi_day = horizon in {"next_week", "this_week"}

        # 1. Schedule density ambiguity — multi-day horizon but few items → all fit in a morning
        if is_multi_day:
            actionable = self._actionable_schedule_threads(ranked_threads)
            total_items = len(actionable) + len(deadlines) + len(asks)
            window_days = 5 if horizon == "next_week" else 5  # Mon–Fri
            if total_items <= window_days:
                questions.append(
                    f"I have {total_items or 'very few'} concrete work item{'s' if total_items != 1 else ''} "
                    f"for {'next week' if horizon == 'next_week' else 'this week'} — "
                    "that fits comfortably in a single morning. "
                    "Do you want a compact schedule (batch similar work, fewer days) "
                    "or a relaxed one (one or two focused tasks per day across the full week)?"
                )

        # 2. No calendar coverage for the planning window
        if is_multi_day and not upcoming_events:
            questions.append(
                "I don't see any calendar events pulled in for "
                f"{'next week' if horizon == 'next_week' else 'this week'}. "
                "Should I build the schedule from inbox signals only, "
                "or do you want to sync your calendar first so I can work around your meetings?"
            )

        # 3. Completely sparse — no inbox, no calendar, no deadlines
        if not ranked_threads and not upcoming_events and not deadlines:
            questions.append(
                "I don't have enough context to build a meaningful schedule — "
                "no inbox threads, no calendar events, and no deadlines are loaded. "
                "What's the highest-priority thing you need time blocked for?"
            )

        return questions[:2]

    def _schedule_clarification_options(self, questions: List[str]) -> List[Dict[str, Any]]:
        options: List[Dict[str, Any]] = []
        question_text = " ".join(questions).lower()

        if "compact" in question_text or "relaxed" in question_text or "single morning" in question_text:
            options.append({
                "label": "Compact — batch work, fewer days",
                "value": "compact_schedule",
                "description": "Group similar tasks together. Cover 2–3 focused days rather than spreading thin.",
                "apply_text": "Build me a compact schedule — batch similar work into focused days.",
            })
            options.append({
                "label": "Relaxed — one or two tasks per day",
                "value": "relaxed_schedule",
                "description": "One to two tasks per day across the full week. Space to react and stay flexible.",
                "apply_text": "Build me a relaxed schedule — one or two tasks per day across the week.",
            })

        if "inbox signals only" in question_text or "sync your calendar" in question_text:
            options.append({
                "label": "Inbox only — proceed now",
                "value": "inbox_only",
                "description": "Build the schedule from email threads and deadlines. Skip unsynced calendar.",
                "apply_text": "Build from inbox signals only — skip the calendar for now.",
            })
            options.append({
                "label": "Wait for calendar sync",
                "value": "wait_calendar",
                "description": "Hold the schedule until calendar events are loaded.",
                "apply_text": "Sync my calendar first, then rebuild the schedule.",
            })

        if "highest-priority" in question_text or "not have enough" in question_text:
            options.append({
                "label": "Tell me your top priority",
                "value": "manual_priority",
                "description": "Share the one thing you need time for and I'll build around it.",
                "apply_text": "Let me describe my top priority for the schedule.",
            })

        return options[:3]

    def _presentation_style_gate(
        self,
        *,
        workflow_type: str,
        ceo_id: str,
        resolved_clarifications: dict[str, str],
    ) -> tuple[str, List[Dict[str, Any]]] | None:
        from src.core.database import get_learned_preference

        resolved_style = str(resolved_clarifications.get("presentation_style") or "").strip()
        learned_style = get_learned_preference(ceo_id, "presentation_style")

        presentation_gates: dict[str, tuple[str, list[dict[str, Any]], set[str]]] = {
            "schedule_planning": (
                "Do you want this as a timeline or a compact list?",
                [
                    {
                        "label": "Timeline",
                        "value": "timeline",
                        "description": "Show dated blocks and sequence.",
                        "apply_text": "Render this as a timeline with dated blocks.",
                    },
                    {
                        "label": "Compact list",
                        "value": "list_form",
                        "description": "Keep it as a concise list of priorities and actions.",
                        "apply_text": "Render this as a compact list of priorities and actions.",
                    },
                ],
                {"timeline", "list_form"},
            ),
            "day_schedule_planning": (
                "Do you want this as a timeline or a compact list?",
                [
                    {
                        "label": "Timeline",
                        "value": "timeline",
                        "description": "Show dated blocks and sequence.",
                        "apply_text": "Render this as a timeline with dated blocks.",
                    },
                    {
                        "label": "Compact list",
                        "value": "list_form",
                        "description": "Keep it as a concise list of priorities and actions.",
                        "apply_text": "Render this as a compact list of priorities and actions.",
                    },
                ],
                {"timeline", "list_form"},
            ),
            "week_schedule_planning": (
                "Do you want this as a timeline or a compact list?",
                [
                    {
                        "label": "Timeline",
                        "value": "timeline",
                        "description": "Show dated blocks and sequence.",
                        "apply_text": "Render this as a timeline with dated blocks.",
                    },
                    {
                        "label": "Compact list",
                        "value": "list_form",
                        "description": "Keep it as a concise list of priorities and actions.",
                        "apply_text": "Render this as a compact list of priorities and actions.",
                    },
                ],
                {"timeline", "list_form"},
            ),
            "weekly_recap": (
                "Do you want this as a list recap or a narrative recap?",
                [
                    {
                        "label": "List recap",
                        "value": "list_form",
                        "description": "Keep the recap in bullets and short sections.",
                        "apply_text": "Format this as a concise list recap with clear bullets.",
                    },
                    {
                        "label": "Narrative recap",
                        "value": "narrative_recap",
                        "description": "Use a short prose recap with a steady flow.",
                        "apply_text": "Format this as a narrative recap with prose.",
                    },
                ],
                {"list_form", "narrative_recap"},
            ),
            "morning_brief": (
                "Do you want this as a compact brief or a narrative recap?",
                [
                    {
                        "label": "Compact brief",
                        "value": "list_form",
                        "description": "Keep the brief in bullets and short sections.",
                        "apply_text": "Format this as a compact brief with clear bullets.",
                    },
                    {
                        "label": "Narrative recap",
                        "value": "narrative_recap",
                        "description": "Use a short prose brief with a steady flow.",
                        "apply_text": "Format this as a narrative recap with prose.",
                    },
                ],
                {"list_form", "narrative_recap"},
            ),
            "calendar_briefing": (
                "Do you want this as a compact agenda or a narrative recap?",
                [
                    {
                        "label": "Compact agenda",
                        "value": "list_form",
                        "description": "Keep the calendar brief in bullets and short sections.",
                        "apply_text": "Format this as a compact agenda with clear bullets.",
                    },
                    {
                        "label": "Narrative recap",
                        "value": "narrative_recap",
                        "description": "Use a short prose calendar brief with a steady flow.",
                        "apply_text": "Format this as a narrative recap with prose.",
                    },
                ],
                {"list_form", "narrative_recap"},
            ),
            "email_ingestion": (
                "Do you want this as a compact inbox list or a narrative recap?",
                [
                    {
                        "label": "Compact inbox list",
                        "value": "list_form",
                        "description": "Keep the inbox brief in bullets and short sections.",
                        "apply_text": "Format this as a compact inbox list with clear bullets.",
                    },
                    {
                        "label": "Narrative recap",
                        "value": "narrative_recap",
                        "description": "Use a short prose inbox brief with a steady flow.",
                        "apply_text": "Format this as a narrative recap with prose.",
                    },
                ],
                {"list_form", "narrative_recap"},
            ),
            "email_watcher": (
                "Do you want this as a compact inbox list or a narrative recap?",
                [
                    {
                        "label": "Compact inbox list",
                        "value": "list_form",
                        "description": "Keep the inbox brief in bullets and short sections.",
                        "apply_text": "Format this as a compact inbox list with clear bullets.",
                    },
                    {
                        "label": "Narrative recap",
                        "value": "narrative_recap",
                        "description": "Use a short prose inbox brief with a steady flow.",
                        "apply_text": "Format this as a narrative recap with prose.",
                    },
                ],
                {"list_form", "narrative_recap"},
            ),
            "meeting_prep": (
                "Do you want this as a checklist or a narrative brief?",
                [
                    {
                        "label": "Checklist",
                        "value": "list_form",
                        "description": "Keep the prep in bullets and short sections.",
                        "apply_text": "Format this as a checklist with clear bullets.",
                    },
                    {
                        "label": "Narrative brief",
                        "value": "narrative_recap",
                        "description": "Use a short prose prep brief with a steady flow.",
                        "apply_text": "Format this as a narrative brief with prose.",
                    },
                ],
                {"list_form", "narrative_recap"},
            ),
        }
        gate = presentation_gates.get(workflow_type)
        if not gate:
            return None
        question, options, accepted_values = gate
        if resolved_style in accepted_values or learned_style in accepted_values:
            return None
        return question, options

        return None

    def _detect_meeting_prep_gaps(
        self,
        *,
        event_payload: Dict[str, Any],
        task_input: str,
    ) -> List[str]:
        """
        Inspect meeting context before generating a prep brief.
        Returns [] when enough is known — gate passes silently.
        """
        questions: List[str] = []
        meeting = self._primary_meeting_event(event_payload)
        title = meeting.get("title") or event_payload.get("title")
        attendees = meeting.get("attendees") or event_payload.get("attendee_emails") or []
        agenda = str(meeting.get("agenda") or "").strip()
        ranked_threads = event_payload.get("ranked_threads") or []
        structured_watch = event_payload.get("structured_watch") or {}
        asks = [item.get("ask") for item in structured_watch.get("asks", []) if item.get("ask")]

        # No title, no attendees, no threads — completely blind
        if not title and not attendees and not ranked_threads:
            questions.append(
                "I don't have any meeting details to prep from. "
                "What's the meeting title, who's attending, and what decision or outcome do you need?"
            )
            return questions

        # Has title but no agenda, no asks, no threads — can name the meeting, not the substance
        if title and not agenda and not asks and not ranked_threads:
            questions.append(
                f'I can see the meeting is "{title}" but there\'s no agenda or related threads loaded. '
                "What's the key decision or outcome you need from this meeting?"
            )

        # Has attendees but no agenda and no threads — prep will be completely generic
        if attendees and not agenda and not ranked_threads and not questions:
            questions.append(
                "I have the attendee list but no context on what's been discussed with them. "
                "Is there a specific ask, open item, or prior thread I should factor in?"
            )

        return questions[:2]

    def _meeting_prep_clarification_options(
        self,
        questions: List[str],
        event_payload: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        options: List[Dict[str, Any]] = []
        q_text = " ".join(questions).lower()
        meeting = self._primary_meeting_event(event_payload)
        title = meeting.get("title") or event_payload.get("title") or "the meeting"

        if "decision or outcome" in q_text or "agenda" in q_text:
            options.append({
                "label": "Describe the outcome needed",
                "value": "describe_outcome",
                "description": "Tell me what decision or result you need from this meeting.",
                "apply_text": f'The key outcome I need from "{title}" is:',
            })
            options.append({
                "label": "Build from available context",
                "value": "use_context",
                "description": "Build the prep from whatever context is currently loaded.",
                "apply_text": f'Build the prep for "{title}" from whatever context you have.',
            })
        elif "no meeting details" in q_text:
            options.append({
                "label": "I'll paste the invite",
                "value": "paste_invite",
                "description": "Paste the calendar invite and I'll prep from that.",
                "apply_text": "Here's the meeting invite:",
            })
            options.append({
                "label": "I'll describe the meeting",
                "value": "describe_meeting",
                "description": "Describe the meeting context and I'll build from there.",
                "apply_text": "The meeting is:",
            })
        else:
            options.append({
                "label": "Build from context only",
                "value": "use_context",
                "description": "Use whatever context is available.",
                "apply_text": f'Build the prep for "{title}" from whatever context you have.',
            })

        return options[:3]

    def _workflow_profile(self, workflow_type: str) -> BriefingWorkflowProfile:
        return self.WORKFLOW_PROFILES.get(
            workflow_type,
            BriefingWorkflowProfile(
                workflow_type=workflow_type,
                presentation_mode="report",
                presentation_variant=None,
                live_context_kind="watch",
                summary_kind="default",
                title_kind="default",
            ),
        )

    def _has_live_event_context(self, workflow_type: str, event_payload: Dict[str, Any]) -> bool:
        live_context_kind = self._workflow_profile(workflow_type).live_context_kind
        if live_context_kind == "email":
            return bool(event_payload.get("ranked_threads") or event_payload.get("structured_watch"))
        if live_context_kind == "calendar":
            return bool(event_payload.get("title") or event_payload.get("related_threads"))
        if live_context_kind == "watch":
            return bool(
                event_payload.get("ranked_threads")
                or event_payload.get("upcoming_events")
                or event_payload.get("planning_context")
            )
        return False

    _ACTION_SECTION_LABELS = frozenset(
        {"recommended actions", "next steps", "action items", "recommended next actions", "actions"}
    )
    _DECISION_SECTION_LABELS = frozenset({"decisions", "key decisions", "decision"})
    _MILESTONE_SECTION_LABELS = frozenset({"milestones", "timeline", "key milestones"})

    def _extract_memory_save_actions(self, payload: BriefPayload) -> list:
        """Scan briefing sections for commitments, decisions, and milestones. Return save actions (max 3)."""
        saves: list = []
        for section in payload.answer.sections:
            label_lower = section.label.lower()
            if label_lower in self._ACTION_SECTION_LABELS:
                mem_type = "commitment"
            elif label_lower in self._DECISION_SECTION_LABELS:
                mem_type = "decision"
            elif label_lower in self._MILESTONE_SECTION_LABELS:
                mem_type = "milestone"
            else:
                continue
            items = section.items or ([section.content] if section.content else [])
            for item in items[:2]:
                item_text = str(item).strip()
                if not self._memory_candidate_is_strong(item_text, mem_type):
                    continue
                if len(saves) >= 3:
                    break
                saves.append(
                    tool_action(
                        "memory_management",
                        action="save",
                        auto_save=True,
                        memory_type=mem_type,
                        title=item_text[:80].strip().rstrip("."),
                        content=item_text,
                        confidence=payload.trust.confidence,
                        confidence_score=payload.trust.confidence_score,
                        evidence_state=payload.trust.evidence_state,
                        dedupe_query=item_text[:80],
                        tags=["auto", "briefing"],
                    )
                )
            if len(saves) >= 3:
                break
        return saves

    def _memory_candidate_is_strong(self, item: str, memory_type: str) -> bool:
        normalized = " ".join((item or "").split()).lower()
        if len(normalized) < 24:
            return False
        if memory_type == "decision":
            return any(marker in normalized for marker in ("decide", "approved", "choose", "will ", "commit"))
        if memory_type == "milestone":
            return any(marker in normalized for marker in ("by ", "before ", "deadline", "target", "milestone"))
        return any(
            marker in normalized
            for marker in ("today", "tomorrow", "this week", "next week", "within", "by ", "before ", ":")
        ) or bool(re.search(r"\b(ceo|cfo|finance|engineering|product|operations|sales)\b", normalized))

    def _briefing_prompt(
        self,
        workflow_type: str,
        event_payload: Dict[str, Any],
        prepared_context: Dict[str, Any],
        task_input: str,
    ) -> str:
        retrieved = prepared_context.get("retrieved_documents", [])
        signals = prepared_context.get("signals", [])
        history = prepared_context.get("history", [])

        # Use enriched event_payload from prepared_context (contains merged live_threads + live_events)
        enriched_event_payload = prepared_context.get("event_payload") or event_payload

        sorted_retrieved = sorted(retrieved, key=lambda d: float(d.get("source_authority", 0.5)), reverse=True)
        doc_blocks = self._format_retrieved_docs(sorted_retrieved)
        confidence_warning = self._retrieval_confidence_warning(sorted_retrieved)
        discipline_block = self._workflow_discipline_block(workflow_type, enriched_event_payload)

        # Live connector block
        live_block = ""
        live_threads = enriched_event_payload.get("live_threads", [])
        live_events = enriched_event_payload.get("live_events", [])
        if live_threads:
            thread_lines = []
            for t in live_threads[:5]:
                subject = t.get("subject", t.get("title", ""))
                sender = t.get("sender", t.get("from", ""))
                snippet = t.get("snippet", t.get("body", ""))[:200]
                thread_lines.append(f"  - [{sender}] {subject}: {snippet}")
            live_block += "=== LIVE EMAIL THREADS ===\n" + "\n".join(thread_lines) + "\n\n"
        if live_events:
            event_lines = []
            for e in live_events[:8]:
                title = e.get("title", e.get("summary", ""))
                start = e.get("start", e.get("start_time", ""))
                attendees = ", ".join(str(a) for a in (e.get("attendees", []))[:3])
                event_lines.append(f"  - {start} | {title}" + (f" (with {attendees})" if attendees else ""))
            live_block += "=== LIVE CALENDAR EVENTS ===\n" + "\n".join(event_lines) + "\n\n"

        # CEO memory block
        memory_block = ""
        ceo_memories = prepared_context.get("ceo_memories", [])
        if ceo_memories:
            memory_lines = []
            for mem in ceo_memories[:8]:
                mem_type = mem.get("memory_type", "fact")
                title = mem.get("title", "")
                content = mem.get("content", "")
                memory_lines.append(f"  [{mem_type}] {title}: {content}")
            memory_block = "=== CEO MEMORY CONTEXT (decisions, commitments, preferences) ===\n" + "\n".join(memory_lines) + "\n\n"

        # CRM deal context block (meeting_prep and calendar_briefing only)
        crm_block = ""
        crm_deals = prepared_context.get("crm_deals", [])
        if crm_deals and workflow_type in {"meeting_prep", "calendar_briefing"}:
            deal_lines = []
            for deal in crm_deals[:5]:
                name = deal.get("name", "Unknown Deal")
                stage = deal.get("stage", "")
                amount = deal.get("amount")
                amount_str = f"${amount:,.0f}" if amount else "—"
                close_date = deal.get("close_date", "")
                account = deal.get("account_name", "")
                deal_lines.append(
                    f"  - {name} | {account} | Stage: {stage} | Amount: {amount_str}"
                    + (f" | Close: {close_date}" if close_date else "")
                )
            crm_block = "=== CRM PIPELINE (active deals — use for stakeholder context) ===\n" + "\n".join(deal_lines) + "\n\n"

        unified_memory_block = self._unified_memory_prompt_block(prepared_context.get("unified_memory") or {})
        live_context_block = self._live_context_prompt_block(prepared_context.get("live_context") or {})
        situational_block = self._situational_prompt_block(prepared_context.get("situational_profile") or {})

        # Retrieval manifest block
        manifest_data = prepared_context.get("retrieval_manifest") or {}
        manifest_block = RetrievalManifest(**manifest_data).to_prompt_block() if manifest_data else ""

        # Entity context block
        entity_block = self._entity_context_block(prepared_context.get("entity_context") or [])

        # Proactive observations block
        obs_block = prepared_context.get("proactive_observations_block") or ""

        return (
            f"Workflow type: {workflow_type}\n\n"
            f"Task input: {task_input}\n\n"
            f"{unified_memory_block}"
            f"{manifest_block}"
            f"Event payload: {enriched_event_payload}\n\n"
            f"Company signals: {signals}\n\n"
            f"{self._format_history_block(history)}"
            f"Planning context: {enriched_event_payload.get('planning_context', {})}\n\n"
            f"{live_block}"
            f"{crm_block}"
            f"{memory_block}"
            f"{entity_block}"
            f"{live_context_block}"
            f"{situational_block}"
            f"=== RETRIEVED DOCUMENTS (ranked by authority) ===\n{doc_blocks}\n\n"
            f"{confidence_warning}"
            f"{discipline_block}"
            f"{obs_block}"
            "Return a BriefPayload JSON object with answer, trust, sources, and presentation. "
            "presentation.preamble is REQUIRED: 1–2 sentences, first-person, conversational. "
            "Speak directly to the CEO before handing over the brief — what you looked at and the one thing that stands out. "
            "Examples: 'Scanned the inbox since last night — five threads need attention, two are time-sensitive.' "
            "or 'Put together the week plan from your calendar and open items — Wednesday is heavy, I\\'ve flagged the prep you\\'ll need.' "
            "Do NOT start with 'I have', 'Here is', 'Based on', or 'This brief'. Be specific and direct."
        )

    def _format_history_block(self, history: List[Dict[str, Any]]) -> str:
        if not history:
            return ""
        lines = ["=== RECENT CONVERSATION HISTORY ==="]
        for item in history[-5:]:
            timestamp = str(item.get("timestamp") or "")[:10]
            query = str(item.get("query") or "")[:200]
            response = str(item.get("response") or "")[:350]
            lines.append(f"[{timestamp}] Q: {query}")
            if response:
                lines.append(f"   A: {response}")
        return "\n".join(lines) + "\n\n"

    def _entity_context_block(self, entity_context: List[Dict[str, Any]]) -> str:
        if not entity_context:
            return ""
        lines = ["=== ENTITY CONTEXT (what's known about named entities in this query) ==="]
        for item in entity_context[:6]:
            entity = item.get("entity", "")
            snippet = item.get("snippet", "")
            ts = str(item.get("timestamp", ""))[:10]
            source = item.get("source_type", "")
            lines.append(f"  [{entity}] ({source}, {ts}): {snippet[:180]}")
        return "\n".join(lines) + "\n\n"

    def _unified_memory_prompt_block(self, unified_memory: Dict[str, Any]) -> str:
        if not isinstance(unified_memory, dict) or not unified_memory:
            return ""
        return (
            "=== UNIFIED MEMORY (canonical working/session/long-term state) ===\n"
            f"{json.dumps(unified_memory, ensure_ascii=True)[:5000]}\n\n"
            "Use this as the primary memory contract for continuity, active task state, and durable preferences.\n\n"
        )

    def _format_retrieved_docs(self, retrieved: List[Dict[str, Any]]) -> str:
        if not retrieved:
            return "(no documents retrieved)"
        blocks: List[str] = []
        for idx, doc in enumerate(retrieved[:6]):
            authority = float(doc.get("source_authority", 0.5))
            label = "PRIMARY" if authority >= 0.85 else ("SECONDARY" if authority >= 0.65 else "LOW")
            title = doc.get("title", f"Document {idx + 1}")
            content = doc.get("content", doc.get("snippet", ""))
            snippet = str(content)[:600].strip() if content else "(no content)"
            blocks.append(f"[{idx + 1}] {title} | Authority: {label}\n{snippet}")
        return "\n\n".join(blocks)

    def _retrieval_confidence_warning(self, retrieved: List[Dict[str, Any]]) -> str:
        if not retrieved:
            return (
                "⚠ NO DOCUMENTS RETRIEVED: This briefing has no indexed document support. "
                "Set trust.confidence_score ≤ 0.4 and populate trust.missing_context.\n\n"
            )
        strong = [d for d in retrieved if float(d.get("source_authority", 0.5)) >= 0.65]
        if not strong:
            return (
                "⚠ LOW AUTHORITY SOURCES ONLY: All retrieved documents are low authority. "
                "Set trust.confidence_score ≤ 0.55 and flag in trust.data_quality.\n\n"
            )
        return ""

    def _workflow_discipline_block(self, workflow_type: str, event_payload: Dict[str, Any]) -> str:
        if workflow_type in {"morning_brief", "weekly_recap"}:
            return (
                "=== DAILY BRIEFING DISCIPLINE ===\n"
                "- Lead with the single most important signal the CEO must act on today.\n"
                "- Sections must be: Key Signals, Why It Matters, Recommended Next Actions.\n"
                "- Key Signals: list 3–5 specific, concrete observations (not summaries). Each must state what changed and by how much.\n"
                "- Why It Matters: connect each signal to a business risk, decision, or opportunity. No generic filler.\n"
                "- Recommended Next Actions: each action must be specific, owned, and time-bound (e.g., 'Confirm vendor contract by EOD Thursday').\n"
                "- Cite which document or signal supports each claim using [Source: title].\n"
                "- Do not include signals the CEO cannot act on within the next 7 days.\n\n"
            )
        if workflow_type == "meeting_prep":
            upcoming_events = event_payload.get("upcoming_events") or []
            meeting_title = upcoming_events[0].get("title", "the meeting") if upcoming_events else "the meeting"
            attendees = upcoming_events[0].get("attendees", []) if upcoming_events else []
            attendee_list = ", ".join(str(a) for a in attendees[:5]) if attendees else "unknown attendees"
            return (
                f"=== MEETING PREP DISCIPLINE (call-prep for: {meeting_title}) ===\n"
                f"Attendees: {attendee_list}\n\n"
                "Sections must be exactly:\n"
                "1. Meeting Overview — purpose, attendees, format (internal/external/board), and what a successful outcome looks like.\n"
                "2. Stakeholder Intelligence — for each key attendee: their current priority, any open asks or commitments owed to them, and their likely position on the key agenda item.\n"
                "3. Open Items — unresolved questions or deliverables from prior interactions with these attendees. Cite the source document.\n"
                "4. Suggested Talking Points — 3–5 specific, evidence-backed points. Each must name the source (document or signal) and the business implication.\n"
                "5. Desired Outcomes — the 1–2 concrete decisions or commitments the CEO should leave this meeting with.\n\n"
                "Discipline rules:\n"
                "- Every talking point must be attributable to a retrieved document or signal. Do not fabricate context.\n"
                "- If stakeholder intelligence is missing, flag it explicitly in trust.missing_context.\n"
                "- If there are open financial or legal items with any attendee, surface them in Open Items with HIGH urgency.\n"
                "- trust.confidence_score should reflect how well the retrieved documents cover the meeting's topic.\n\n"
            )
        if workflow_type == "schedule_planning":
            return (
                "=== SCHEDULE PLANNING DISCIPLINE ===\n"
                "- Sections must be: Planning Inputs, Schedule Proposal, Deadlines, Upcoming Meetings, Suggested Follow-Ups.\n"
                "- Planning Inputs: what context drove this plan (inbox signals, calendar load, deadlines).\n"
                "- Schedule Proposal: the proposed time blocks in order. Each item must follow the format 'Day HH:MM-HH:MM AM/PM: Action'. "
                "Use the pre-built blocks from context verbatim — do not reorder or omit them.\n"
                "- Deadlines: only concrete deadlines with named owners or dates. If none exist, omit the section entirely rather than stating 'no deadlines'.\n"
                "- Upcoming Meetings: only meetings that fall in the planning window. Include time and attendees.\n"
                "- Suggested Follow-Ups: 2-3 items the CEO should delegate or decide after working through the schedule.\n"
                "- Do not add generic advice. Ground every item in the provided inbox threads, calendar events, or deadline signals.\n\n"
            )
        if workflow_type == "calendar_briefing":
            return (
                "=== CALENDAR BRIEFING DISCIPLINE ===\n"
                "- Sections must be: Schedule Overview, Prep Required, Conflicts or Gaps.\n"
                "- Schedule Overview: list meetings in time order with attendees and stated purpose.\n"
                "- Prep Required: for each meeting that needs materials or pre-reading, name the specific document or action needed.\n"
                "- Conflicts or Gaps: flag back-to-back meetings with no buffer, missing agendas, or attendees who need pre-reads.\n"
                "- Do not include generic advice. Every item must be grounded in the event payload or retrieved documents.\n\n"
            )
        if workflow_type in {"email_ingestion", "email_watcher"}:
            return (
                "=== EMAIL TRIAGE DISCIPLINE ===\n"
                "- Sections must be: Top Threads, Action Required, Waiting On Reply, FYI Only, Suggested Actions.\n"
                "- Top Threads: rank by importance. For each, state: sender, subject, urgency level, and the single required action.\n"
                "- Action Required: threads where the CEO must reply, decide, or delegate within 48 hours.\n"
                "- Waiting On Reply: threads where the CEO is blocked waiting for an answer and should decide whether to nudge, escalate, or let it sit.\n"
                "- FYI Only: threads that are informational only — no action needed but worth awareness.\n"
                "- Suggested Actions: 3 concrete actions with owner and timing. Prefer 'CEO', 'Delegate to <function>', or 'Reply today'.\n"
                "- Flag any thread involving legal, board, investor, or financial topics as HIGH urgency regardless of tone.\n"
                "- Cite source thread IDs or subjects in the sources list.\n\n"
            )
        # Generic fallback
        return (
            "=== BRIEFING DISCIPLINE ===\n"
            "- Sections must be: Key Signals, Why It Matters, Recommended Next Actions.\n"
            "- Lead every claim with the most authoritative source available.\n"
            "- Cite sources by including them in the sources list with source_id and the claim they support.\n"
            "- Do not include claims that cannot be attributed to a retrieved document or signal.\n\n"
        )

    def _generate_payload(
        self,
        *,
        workflow_type: str,
        event_payload: Dict[str, Any],
        prepared_context: Dict[str, Any],
        completion: Optional[Dict[str, Any]],
        task_input: str = "",
    ) -> BriefPayload:
        if completion:
            try:
                return BriefPayload(**completion)
            except Exception as exc:
                logger.warning(
                    "BriefPayload construction failed from LLM completion; falling back to manual build. "
                    "workflow_type=%r error=%s",
                    workflow_type,
                    exc,
                )

        planning_context = event_payload.get("planning_context", {}) or {}
        title = self._default_title(workflow_type, event_payload, planning_context)
        summary = self._default_summary(workflow_type, event_payload)
        specificity_mode = self._schedule_specificity_mode(
            task_input=task_input,
            history=prepared_context.get("history", []),
        )

        signals = prepared_context.get("signals", [])
        history = prepared_context.get("history", [])
        retrieved = prepared_context.get("retrieved_documents", [])
        ranked_threads = event_payload.get("ranked_threads", [])
        structured_watch = self._normalized_structured_watch(event_payload)
        upcoming_events = event_payload.get("upcoming_events", [])
        items = self._primary_items_for_workflow(
            workflow_type=workflow_type,
            event_payload=event_payload,
            ranked_threads=ranked_threads,
            structured_watch=structured_watch,
            upcoming_events=upcoming_events,
            signals=signals,
        )

        sources = self._build_sources(
            workflow_type=workflow_type,
            signals=signals,
            history=history,
            retrieved=retrieved,
            ranked_threads=ranked_threads,
            related_threads=(event_payload.get("related_threads", []) or []),
            upcoming_events=upcoming_events,
        )

        payload = BriefPayload(
            answer=BriefAnswer(
                title=title,
                summary=self._email_summary(summary, event_payload, ranked_threads)
                if workflow_type in {"email_ingestion", "email_watcher"}
                else self._schedule_summary_for_followup(event_payload, ranked_threads, structured_watch, task_input)
                if workflow_type == "schedule_planning" and specificity_mode
                else summary,
                sections=self._build_sections(
                    workflow_type=workflow_type,
                    items=items,
                    ranked_threads=ranked_threads,
                    structured_watch=structured_watch,
                    event_payload=event_payload,
                    task_input=task_input,
                    history=prepared_context.get("history", []),
                ),
            ),
            trust=BriefTrust(
                confidence="medium",
                confidence_score=0.5,
                assumptions=[],
                open_questions=[],
                data_quality="medium",
            ),
            sources=sources,
        )
        payload.trust = self._derive_trust(workflow_type, event_payload, prepared_context, payload=payload)
        return payload

    def _to_markdown(self, payload: BriefPayload) -> str:
        lines = [f"# {payload.answer.title}", "", payload.answer.summary]
        for section in payload.answer.sections:
            lines.extend(["", f"## {section.label}"])
            if section.content:
                lines.extend(["", section.content])
            if section.items:
                lines.extend(["", *[f"- {item}" for item in section.items]])
        return "\n".join(lines).strip()

    def _email_summary(self, fallback: str, event_payload: Dict[str, Any], ranked_threads: List[Dict[str, Any]]) -> str:
        if not ranked_threads:
            return fallback
        triage = self._email_triage_buckets(ranked_threads)
        top_thread = triage["top"][0] if triage["top"] else ranked_threads[0]
        important_count = len(triage["top"])
        action_count = len(triage["action"])
        waiting_count = len(triage["waiting"])
        fyi_count = len(triage["fyi"])
        return (
            f"{important_count} thread{'s' if important_count != 1 else ''} matter right now: "
            f"{action_count} need action, {waiting_count} are waiting on replies, and {fyi_count} are awareness-only. "
            f"Start with '{top_thread.get('subject', 'Inbox thread')}' from {top_thread.get('latest_sender', 'a sender')}."
        )

    def _why_it_matters(
        self,
        workflow_type: str,
        event_payload: Dict[str, Any],
        ranked_threads: List[Dict[str, Any]],
    ) -> str:
        if workflow_type not in {"email_ingestion", "email_watcher"}:
            return "This briefing combines current company context with the most relevant recent executive information."
        if not ranked_threads:
            return "This inbox brief is based on the most recent connected email thread."
        top_thread = self._email_triage_buckets(ranked_threads)["top"][0] if self._email_triage_buckets(ranked_threads)["top"] else ranked_threads[0]
        reasons = top_thread.get("importance_reasons", [])
        if reasons:
            return " ".join(reasons[:2])
        return "The top thread contains recent signals that may require executive attention."

    @staticmethod
    def _fmt_event_time(starts_at: Optional[str]) -> str:
        """Convert ISO timestamp to readable label like 'Sat 10:30 AM'."""
        if not starts_at:
            return "scheduled"
        try:
            dt = datetime.fromisoformat(starts_at)
            return dt.strftime("%a %-I:%M %p").replace("  ", " ")
        except (ValueError, TypeError):
            return starts_at

    def _next_actions(
        self,
        workflow_type: str,
        ranked_threads: List[Dict[str, Any]],
        semantic_context: SemanticContext | None = None,
    ) -> List[str]:
        thread_candidates = []
        for thread in self._actionable_schedule_threads(ranked_threads)[:5]:
            subject = str(thread.get("subject") or "").strip()
            if not subject:
                continue
            family = str(thread.get("category") or "thread").strip() or "thread"
            importance = str(thread.get("importance_level") or "medium").lower()
            priority = 90.0 if importance == "high" else 65.0 if importance == "medium" else 40.0
            thread_candidates.append(
                build_follow_up_candidate(
                    f"Review {subject} and decide the next step.",
                    family=f"thread:{family}",
                    deadline_at=thread.get("due_at") or thread.get("deadline_at") or thread.get("starts_at"),
                    priority=priority,
                    topic_key=str(thread.get("thread_id") or thread.get("id") or subject),
                )
            )
        selected_threads = select_follow_up_candidates(thread_candidates, limit=3)
        if selected_threads:
            return [f"{candidate.text.rstrip('.')}." for candidate in selected_threads]

        if semantic_context is None:
            semantic_context = self._semantic_context_from_threads(
                workflow_type=workflow_type,
                ranked_threads=ranked_threads,
            )

        semantic_candidates = build_semantic_follow_up_candidates(semantic_context, limit=3)
        if semantic_candidates:
            return [f"{candidate.text.rstrip('.')}." for candidate in semantic_candidates]

        if workflow_type in {"email_ingestion", "email_watcher"}:
            triage = self._email_triage_buckets(ranked_threads)
            return self._email_suggested_actions(triage, {}, {})
        return ["Review the most important item first."]

    def _default_title(
        self,
        workflow_type: str,
        event_payload: Dict[str, Any],
        planning_context: Dict[str, Any],
    ) -> str:
        profile = self._workflow_profile(workflow_type)
        if profile.title_kind == "email":
            return f"Inbox Brief: {event_payload.get('subject', 'New thread')}"
        if profile.title_kind == "calendar":
            upcoming_events = event_payload.get("upcoming_events") or []
            count = len(upcoming_events)
            if count > 1:
                return f"Calendar Brief: {count} Meetings This Week"
            if count == 1:
                return f"Calendar Brief: {upcoming_events[0].get('title', 'Upcoming Meeting')}"
            return "Calendar Brief: This Week"
        if profile.title_kind == "morning_brief":
            return self._morning_brief_title(event_payload, planning_context)
        if profile.title_kind == "weekly_recap":
            return "Week in Review"
        if profile.title_kind == "meeting_prep":
            upcoming_events = event_payload.get("upcoming_events") or []
            meeting_title = upcoming_events[0].get("title") if upcoming_events else event_payload.get("title")
            return f"Meeting Prep: {meeting_title or 'Upcoming Meeting'}"
        if profile.title_kind == "schedule":
            if planning_context.get("target_label"):
                return f"{planning_context['target_label']} Schedule Proposal"
            return self._schedule_title(
                planning_context.get("mode"),
                planning_context.get("time_horizon", "unspecified"),
            )
        return "Executive Brief"

    def _morning_brief_title(self, event_payload: Dict[str, Any], planning_context: Dict[str, Any]) -> str:
        target_date_raw = planning_context.get("target_date")
        target_date = None
        if target_date_raw:
            try:
                target_date = date.fromisoformat(str(target_date_raw))
            except ValueError:
                target_date = None
        if target_date is None:
            planning_window = self._resolved_planning_window(event_payload)
            target_raw = planning_window.get("target_date")
            if target_raw:
                try:
                    target_date = date.fromisoformat(str(target_raw))
                except ValueError:
                    target_date = None
        if target_date is None:
            target_date = datetime.now().astimezone().date()
        return f"Morning Brief • {target_date.strftime('%a, %b')} {target_date.day}"

    def _default_summary(self, workflow_type: str, event_payload: Dict[str, Any]) -> str:
        profile = self._workflow_profile(workflow_type)
        if profile.summary_kind == "email":
            threads = event_payload.get("ranked_threads") or []
            if threads:
                triage = self._email_triage_buckets(threads)
                top = triage["top"][0] if triage["top"] else threads[0]
                sender = top.get("latest_sender") or top.get("sender") or "A sender"
                subject = top.get("subject") or "a thread"
                count = len(triage["top"])
                action_count = len(triage["action"])
                waiting_count = len(triage["waiting"])
                if count > 1:
                    return (
                        f"{count} threads need attention. "
                        f"{action_count} need action and {waiting_count} are waiting on reply. "
                        f"Top: \"{subject}\" from {sender}."
                    )
                return f"\"{subject}\" from {sender} requires attention."
            return "No urgent threads detected in your inbox."
        if profile.summary_kind == "calendar":
            upcoming_events = event_payload.get("upcoming_events") or []
            count = len(upcoming_events)
            if count > 1:
                first = upcoming_events[0].get("title", "a meeting")
                return f"{count} meetings on your calendar this week. First up: {first}."
            if count == 1:
                return f"One meeting on your calendar: {upcoming_events[0].get('title', 'a meeting')}."
            return "Your calendar is clear for the requested window."
        if profile.summary_kind == "morning_brief":
            return self._morning_watch_summary(event_payload)
        if profile.summary_kind == "weekly_recap":
            return self._weekly_recap_summary(event_payload)
        if profile.summary_kind == "meeting_prep":
            return self._meeting_prep_summary(event_payload)
        if profile.summary_kind == "schedule":
            return self._day_schedule_summary(event_payload)
        return "Executive briefing ready."

    def _semantic_context_from_threads(
        self,
        *,
        workflow_type: str,
        ranked_threads: List[Dict[str, Any]],
    ) -> SemanticContext:
        top_thread = ranked_threads[0] if ranked_threads else {}
        topic_hint = str(top_thread.get("subject") or top_thread.get("title") or "").strip() or None
        if not topic_hint:
            topic_hint = "the most important item"
        date_hint = (
            top_thread.get("due_at")
            or top_thread.get("deadline_at")
            or top_thread.get("starts_at")
            or top_thread.get("ends_at")
        )
        importance_hint = None
        importance_raw = top_thread.get("importance_score")
        if isinstance(importance_raw, (int, float)):
            importance_hint = float(importance_raw)
        else:
            importance_level = str(top_thread.get("importance_level") or "").lower()
            if importance_level == "high":
                importance_hint = 92.0
            elif importance_level == "medium":
                importance_hint = 72.0
            elif importance_level == "low":
                importance_hint = 45.0
        if importance_hint is None:
            importance_hint = 50.0
        category = str(top_thread.get("category") or "").strip()
        source_id = str(top_thread.get("thread_id") or top_thread.get("id") or "").strip()
        return build_semantic_context(
            title=topic_hint,
            summary=topic_hint,
            confidence_score=0.5,
            evidence_state=None,
            missing_context=[],
            workflow_type=workflow_type,
            response_type="brief",
            topic_hint=topic_hint,
            date_hint=str(date_hint) if date_hint else None,
            importance_hint=importance_hint,
            families_hint=[category] if category else [],
            source_ids_hint=[source_id] if source_id else [],
        )

    def _primary_items_for_workflow(
        self,
        *,
        workflow_type: str,
        event_payload: Dict[str, Any],
        ranked_threads: List[Dict[str, Any]],
        structured_watch: Dict[str, Any],
        upcoming_events: List[Dict[str, Any]],
        signals: List[Dict[str, Any]],
    ) -> List[str]:
        if workflow_type in {"morning_brief", "weekly_recap"}:
            return [
                f"{thread.get('subject', 'Inbox thread')} ({str(thread.get('importance_level', 'medium')).title()} importance)"
                for thread in ranked_threads[:4]
            ] or [signal.get("subject", "Recent signal") for signal in signals[:3]]
        if workflow_type in {"schedule_planning"}:
            return self._schedule_focus_items(ranked_threads, structured_watch, upcoming_events)
        if workflow_type == "meeting_prep":
            attendee_threads = event_payload.get("attendee_threads") or ranked_threads
            return [
                f"{thread.get('subject', 'Attendee thread')} — {thread.get('latest_sender', 'participant')}"
                for thread in attendee_threads[:4]
            ] or [f"No threads from meeting attendees found."]
        if workflow_type == "calendar_briefing":
            related_threads = event_payload.get("related_threads", [])
            return [
                f"{thread.get('subject', 'Related thread')} ({str(thread.get('importance_level', 'medium')).title()} importance)"
                for thread in related_threads[:3]
            ] or list(event_payload.get("attendees", []))[:4]
        return [
            f"{thread.get('subject', 'New inbound thread')} ({str(thread.get('importance_level', 'medium')).title()} importance)"
            for thread in ranked_threads[:3]
        ] or [event_payload.get("subject", "New inbound thread")]

    def _structured_sections(
        self,
        workflow_type: str,
        structured_watch: Dict[str, Any],
        event_payload: Dict[str, Any],
    ) -> List[BriefSection]:
        sections: List[BriefSection] = []
        if workflow_type == "email_ingestion":
            asks = [item.get("ask") for item in structured_watch.get("asks", []) if item.get("ask")]
            deadlines = [item.get("deadline") for item in structured_watch.get("deadlines", []) if item.get("deadline")]
            meetings = [
                item.get("meeting")
                for item in structured_watch.get("implied_meetings", [])
                if item.get("meeting")
            ]
            documents = [
                item.get("document")
                for item in structured_watch.get("implied_docs", [])
                if item.get("document")
            ]
            if asks:
                sections.append(BriefSection(label="Likely Asks", items=asks[:3]))
            if deadlines:
                sections.append(BriefSection(label="Deadlines", items=deadlines[:3]))
            if meetings:
                sections.append(BriefSection(label="Implied Meetings", items=meetings[:3]))
            if documents:
                sections.append(BriefSection(label="Implied Documents", items=documents[:3]))
        elif workflow_type == "calendar_briefing":
            related_threads = event_payload.get("related_threads", []) or []
            if related_threads:
                sections.append(
                    BriefSection(
                        label="Relevant Threads",
                        items=[
                            f"{thread.get('subject', 'Related thread')}: {', '.join(thread.get('importance_reasons', [])[:2])}"
                            for thread in related_threads[:3]
                        ],
                    )
                )
        elif workflow_type == "morning_brief":
            asks = [item.get("ask") for item in structured_watch.get("asks", []) if item.get("ask")]
            deadlines = [item.get("deadline") for item in structured_watch.get("deadlines", []) if item.get("deadline")]
            if asks:
                sections.append(BriefSection(label="Likely Asks", items=asks[:3]))
            if deadlines:
                sections.append(BriefSection(label="Deadlines", items=deadlines[:3]))
            upcoming_events = event_payload.get("upcoming_events", []) or []
            if upcoming_events:
                sections.append(
                    BriefSection(
                        label="Upcoming Meetings",
                        items=[
                            f"{event.get('title', 'Upcoming meeting')} • {event.get('starts_at', 'scheduled')}"
                            for event in upcoming_events[:3]
                        ],
                    )
                )
        return sections

    def _build_sections(
        self,
        *,
        workflow_type: str,
        items: List[str],
        ranked_threads: List[Dict[str, Any]],
        structured_watch: Dict[str, Any],
        event_payload: Dict[str, Any],
        task_input: str = "",
        history: List[Dict[str, Any]] | None = None,
    ) -> List[BriefSection]:
        if workflow_type in {"email_ingestion", "email_watcher"}:
            triage = self._email_triage_buckets(ranked_threads)
            top_threads = self._email_top_thread_items(triage["top"])
            action_required = self._email_action_required_items(
                triage["action"],
                asks=[item.get("ask") for item in structured_watch.get("asks", []) if item.get("ask")],
                deadlines=[item.get("deadline") for item in structured_watch.get("deadlines", []) if item.get("deadline")],
            )
            waiting_items = self._email_waiting_on_reply_items(triage["waiting"])
            fyi_items = self._email_fyi_items(triage["fyi"])
            suggested_actions = self._email_suggested_actions(
                triage,
                structured_watch,
                event_payload,
            )
            return [
                BriefSection(label="Top Threads", items=top_threads[:4] or ["No executive-relevant threads were detected."]),
                BriefSection(label="Action Required", items=action_required[:4] or ["No immediate reply, decision, or delegation was clearly required."]),
                BriefSection(label="Waiting On Reply", items=waiting_items[:3] or ["No critical threads are currently blocked on someone else's reply."]),
                BriefSection(label="FYI Only", items=fyi_items[:3] or ["No awareness-only threads stood out from the current inbox window."]),
                BriefSection(label="Suggested Actions", items=suggested_actions[:4]),
            ]
        if workflow_type in {"morning_brief"}:
            deadlines = self._filter_text_items_by_window(
                [action_item_text(item, kind="deadline") for item in structured_watch.get("deadlines", []) if action_item_text(item, kind="deadline")],
                event_payload=event_payload,
            )
            documents = [
                item.get("document")
                for item in structured_watch.get("implied_docs", [])
                if item.get("document")
            ]
            asks = self._filter_text_items_by_window(
                [action_item_text(item, kind="ask") for item in structured_watch.get("asks", []) if action_item_text(item, kind="ask")],
                event_payload=event_payload,
            )
            follow_ups = asks[:2] + documents[:2]
            if not follow_ups:
                follow_ups = self._next_actions(
                    workflow_type,
                    ranked_threads,
                    self._semantic_context_from_threads(workflow_type=workflow_type, ranked_threads=ranked_threads),
                )
            if workflow_type == "morning_brief":
                raw_events = event_payload.get("live_events") or event_payload.get("upcoming_events", []) or []
                meetings_in_window = [
                    f"{event.get('title', 'Upcoming meeting')} • {event.get('starts_at', 'scheduled')}"
                    for event in self._filter_events_by_horizon(raw_events, event_payload)[:4]
                ]
                meetings = list(meetings_in_window)
                if not meetings_in_window:
                    next_meetings = self._next_meetings_after_window(raw_events, event_payload, limit=3)
                    if next_meetings:
                        meetings = [
                            "No meetings in today's window. Next up outside that window:",
                            *[self._format_next_meeting(event) for event in next_meetings],
                        ]
            sections = [
                BriefSection(label="Important Threads", items=items[:4]),
                BriefSection(label="Deadlines", items=deadlines[:4] or ["No clear deadlines detected in the current watch window."]),
                BriefSection(label="Upcoming Meetings", items=meetings[:4] or ["No upcoming meetings were linked to the current watch window."]),
                BriefSection(label="Suggested Follow-Ups", items=follow_ups[:4]),
            ]
            unresolved = self._timing_to_confirm_items(structured_watch, event_payload=event_payload)
            if unresolved:
                sections.append(BriefSection(label="Timing To Confirm", items=unresolved[:3]))
            return sections

        if workflow_type in {"schedule_planning"}:
            plan_execution = self._plan_execution(event_payload)
            filtered_structured_watch = self._filter_structured_watch_by_window(structured_watch, event_payload=event_payload)
            filtered_event_payload = dict(event_payload)
            filtered_event_payload["upcoming_events"] = self._filter_events_by_horizon(
                event_payload.get("upcoming_events", []) or [],
                event_payload,
            )
            deadlines = [item.get("deadline") for item in filtered_structured_watch.get("deadlines", []) if item.get("deadline")]
            asks = [item.get("ask") for item in filtered_structured_watch.get("asks", []) if item.get("ask")]
            upcoming_meetings = [
                f"{event.get('title', 'Upcoming meeting')} • {event.get('starts_at', 'scheduled')}"
                for event in (filtered_event_payload.get("upcoming_events", []) or [])[:4]
            ]
            schedule_blocks = self._precomputed_schedule_blocks(event_payload) or self._schedule_blocks(
                ranked_threads, filtered_structured_watch, filtered_event_payload
            )
            weak_evidence = bool(plan_execution.get("sparse_guidance")) or self._has_weak_planning_evidence(
                ranked_threads, structured_watch, event_payload
            )
            planning_items = self._planning_inputs(event_payload)
            specificity_mode = self._schedule_specificity_mode(task_input=task_input, history=history or [])
            if specificity_mode:
                planning_items = self._schedule_followup_planning_inputs(
                    event_payload=event_payload,
                    ranked_threads=ranked_threads,
                    asks=asks,
                    deadlines=deadlines,
                )
                schedule_blocks = self._schedule_task_breakdown(
                    ranked_threads=ranked_threads,
                    structured_watch=structured_watch,
                    event_payload=filtered_event_payload,
                    task_input=task_input,
                )
            follow_up_items = self._schedule_follow_ups(
                asks=asks,
                event_payload=filtered_event_payload,
                ranked_threads=ranked_threads,
                task_input=task_input,
                detailed=specificity_mode,
            )
            horizon = (event_payload.get("planning_context") or {}).get("time_horizon", "today")
            block_cap = 10 if horizon in {"next_week", "this_week"} else 4
            sections = [
                BriefSection(label="Planning Inputs", items=planning_items[:2] or ["No planner metadata was attached to this schedule request."]),
                BriefSection(label="Schedule Proposal", items=schedule_blocks[:block_cap]),
                BriefSection(label="Deadlines", items=deadlines[:2] or [self._planning_no_deadlines_copy(event_payload)]),
                BriefSection(label="Upcoming Meetings", items=upcoming_meetings[:2] or [self._planning_no_meetings_copy(event_payload)]),
                BriefSection(
                    label="Suggested Follow-Ups",
                    items=(
                        self._weak_planning_follow_ups(event_payload)
                        if weak_evidence
                        else follow_up_items
                    )[:3],
                ),
            ]
            unresolved = self._timing_to_confirm_items(self._normalized_structured_watch(event_payload), event_payload=event_payload)
            if unresolved:
                sections.append(BriefSection(label="Timing To Confirm", items=unresolved[:3]))
            return sections

        if workflow_type == "calendar_briefing":
            related_threads = event_payload.get("related_threads", []) or []
            upcoming_events = event_payload.get("upcoming_events") or []
            meeting_items = [
                f"{ev.get('title', 'Meeting')} • {self._fmt_event_time(ev.get('starts_at')) or ev.get('day_label') or 'scheduled'}"
                for ev in upcoming_events[:6]
            ] or ["No upcoming meetings found for this window."]
            thread_items = [
                f"{t.get('subject', 'Thread')} ({str(t.get('importance_level', 'medium')).title()} importance)"
                for t in related_threads[:3]
            ] if related_threads else []
            follow_ups = self._next_actions(
                workflow_type,
                ranked_threads,
                self._semantic_context_from_threads(workflow_type=workflow_type, ranked_threads=ranked_threads),
            )
            sections = [BriefSection(label="Upcoming Meetings", items=meeting_items)]
            if thread_items:
                sections.append(BriefSection(label="Related Threads", items=thread_items))
            sections.append(BriefSection(label="Suggested Follow-Ups", items=follow_ups[:4]))
            return sections

        if workflow_type == "weekly_recap":
            structured_watch = event_payload.get("structured_watch", {}) or {}
            asks = [item.get("ask") for item in structured_watch.get("asks", []) if item.get("ask")]
            deadlines = [item.get("deadline") for item in structured_watch.get("deadlines", []) if item.get("deadline")]
            past_events = event_payload.get("upcoming_events", []) or []
            meetings_held = [
                f"{event.get('title', 'Meeting')} • {event.get('starts_at', 'this week')}"
                for event in past_events[:4]
            ] or ["No meetings were recorded for this week."]
            open_items = asks[:3] or self._next_actions(
                workflow_type,
                ranked_threads,
                self._semantic_context_from_threads(workflow_type=workflow_type, ranked_threads=ranked_threads),
            )
            return [
                BriefSection(label="This Week's Threads", items=items[:4] or ["No significant inbox threads this week."]),
                BriefSection(label="Meetings Held", items=meetings_held),
                BriefSection(label="Deadlines & Commitments", items=deadlines[:4] or ["No tracked deadlines found for this week."]),
                BriefSection(label="Still Open", items=open_items[:4]),
            ]

        if workflow_type == "meeting_prep":
            structured_watch = event_payload.get("structured_watch", {}) or {}
            open_items = self._meeting_open_items(event_payload, ranked_threads)
            talking_points = self._meeting_talking_points(event_payload, ranked_threads)
            return [
                BriefSection(label="Meeting Overview", items=self._meeting_overview_items(event_payload)[:4] or ["No meeting details available."]),
                BriefSection(label="Meeting Objectives", items=self._meeting_objectives(event_payload, ranked_threads)[:4]),
                BriefSection(label="Open Items", items=open_items[:4] or ["No open asks or deadlines detected."]),
                BriefSection(label="Suggested Talking Points", items=talking_points[:4]),
                BriefSection(label="Desired Outcomes", items=self._meeting_desired_outcomes(event_payload, ranked_threads)[:4]),
            ]

        sections = [
            BriefSection(label="Key Signals", items=items),
            BriefSection(
                label="Why It Matters",
                content=self._why_it_matters(workflow_type, event_payload, ranked_threads),
            ),
            BriefSection(
                label="Recommended Next Actions",
                items=self._next_actions(
                    workflow_type,
                    ranked_threads,
                    self._semantic_context_from_threads(workflow_type=workflow_type, ranked_threads=ranked_threads),
                ),
            ),
        ]
        sections.extend(self._structured_sections(workflow_type, structured_watch, event_payload))
        return sections

    def _weekly_recap_summary(self, event_payload: Dict[str, Any]) -> str:
        ranked_threads = event_payload.get("ranked_threads", []) or []
        upcoming_events = event_payload.get("upcoming_events", []) or []
        important_threads = len([t for t in ranked_threads if t.get("importance_level") in {"high", "medium"}])
        if important_threads or upcoming_events:
            return (
                f"{important_threads} notable thread{'s' if important_threads != 1 else ''} "
                f"and {len(upcoming_events)} meeting{'s' if len(upcoming_events) != 1 else ''} "
                "were logged this week. Here is what mattered."
            )
        return "Here is a summary of this week's executive activity based on available signals."

    def _meeting_prep_summary(self, event_payload: Dict[str, Any]) -> str:
        upcoming_events = event_payload.get("upcoming_events") or []
        meeting = upcoming_events[0] if upcoming_events else {}
        meeting_title = meeting.get("title") or event_payload.get("title") or "the upcoming meeting"
        attendee_threads = event_payload.get("attendee_threads") or []
        ranked_threads = event_payload.get("ranked_threads") or []
        thread_count = len(attendee_threads) or len(ranked_threads)
        if thread_count:
            return (
                f"{thread_count} thread{'s' if thread_count != 1 else ''} from meeting participants "
                f"reviewed ahead of {meeting_title}. Objectives, open items, and desired outcomes are called out below."
            )
        return f"Briefing prepared for {meeting_title} with meeting objectives, open items, and desired outcomes based on available company context."

    def _normalize_meeting_prep_payload(
        self,
        payload: BriefPayload,
        *,
        workflow_type: str,
        event_payload: Dict[str, Any],
    ) -> BriefPayload:
        if workflow_type != "meeting_prep":
            return payload

        payload = payload.model_copy(deep=True)
        ranked_threads = event_payload.get("ranked_threads") or []
        meeting = self._primary_meeting_event(event_payload)
        overview = self._meeting_overview_items(event_payload)
        objectives = self._meeting_objectives(event_payload, ranked_threads)
        open_items = self._meeting_open_items(event_payload, ranked_threads)
        talking_points = self._meeting_talking_points(event_payload, ranked_threads)
        desired_outcomes = self._meeting_desired_outcomes(event_payload, ranked_threads)

        payload.answer.sections = [
            BriefSection(label="Meeting Overview", items=overview[:4] or ["No meeting details available."]),
            BriefSection(label="Meeting Objectives", items=objectives[:4]),
            BriefSection(label="Open Items", items=open_items[:4]),
            BriefSection(label="Suggested Talking Points", items=talking_points[:4]),
            BriefSection(label="Desired Outcomes", items=desired_outcomes[:4]),
        ]

        meeting_title = meeting.get("title") or event_payload.get("title") or "the upcoming meeting"
        payload.answer.summary = (
            f"{payload.answer.summary.rstrip('.')} "
            f"Use this prep to align on agenda, objectives, and desired outcomes for {meeting_title}."
        ).strip()
        return payload

    def _primary_meeting_event(self, event_payload: Dict[str, Any]) -> Dict[str, Any]:
        upcoming_events = event_payload.get("upcoming_events") or []
        if upcoming_events:
            return upcoming_events[0]
        return event_payload if isinstance(event_payload, dict) else {}

    def _meeting_overview_items(self, event_payload: Dict[str, Any]) -> List[str]:
        meeting = self._primary_meeting_event(event_payload)
        attendees = meeting.get("attendees") or event_payload.get("attendee_emails") or []
        items: List[str] = []
        title = meeting.get("title") or event_payload.get("title")
        starts_at = meeting.get("starts_at")
        if title:
            items.append(f"{title} • {starts_at or 'scheduled'}")
        if attendees:
            items.append(f"Attendees: {', '.join(str(a) for a in attendees[:4])}")
        if meeting.get("agenda"):
            items.append(f"Agenda: {meeting['agenda']}")
        return items

    def _meeting_objectives(self, event_payload: Dict[str, Any], ranked_threads: List[Dict[str, Any]]) -> List[str]:
        meeting = self._primary_meeting_event(event_payload)
        structured_watch = event_payload.get("structured_watch", {}) or {}
        objectives: List[str] = []
        agenda = str(meeting.get("agenda") or "").strip()
        notes = str(meeting.get("notes") or "").strip()
        asks = [str(item.get("ask")).strip() for item in structured_watch.get("asks", []) if item.get("ask")]

        if agenda:
            objectives.append(f"Objective: cover the core agenda for this meeting: {agenda}")
        if notes:
            objectives.append(f"Objective: resolve the key decision embedded in the prep notes: {notes}")
        for ask in asks[:2]:
            objectives.append(f"Objective: leave the meeting with a clear decision on {ask.rstrip('.')}.")
        if not objectives:
            objectives = [
                "Objective: confirm the most important agenda items before the meeting starts.",
                "Objective: get explicit ownership on any open asks or deliverables.",
            ]
        return objectives[:4]

    def _meeting_open_items(self, event_payload: Dict[str, Any], ranked_threads: List[Dict[str, Any]]) -> List[str]:
        structured_watch = event_payload.get("structured_watch", {}) or {}
        candidates = []
        for item in structured_watch.get("asks", []):
            ask = str(item.get("ask") or "").strip()
            if ask:
                candidates.append(
                    build_follow_up_candidate(
                        f"Open item: {ask}",
                        family="ask",
                        priority=88.0,
                        topic_key=ask,
                    )
                )
        for item in structured_watch.get("deadlines", []):
            deadline = str(item.get("deadline") or "").strip()
            if deadline:
                candidates.append(
                    build_follow_up_candidate(
                        f"Open item: {deadline}",
                        family="deadline",
                        deadline_at=item.get("due_at") or item.get("due_date"),
                        priority=92.0,
                        topic_key=deadline,
                    )
                )
        selected = select_follow_up_candidates(candidates, limit=3)
        if selected:
            return [candidate.text for candidate in selected]
        open_items = [
            f"Open item: {point}"
            for point in self._next_actions(
                "meeting_prep",
                ranked_threads,
                self._semantic_context_from_threads(workflow_type="meeting_prep", ranked_threads=ranked_threads),
            )[:3]
        ]
        return open_items[:4]

    def _meeting_desired_outcomes(self, event_payload: Dict[str, Any], ranked_threads: List[Dict[str, Any]]) -> List[str]:
        meeting = self._primary_meeting_event(event_payload)
        structured_watch = event_payload.get("structured_watch", {}) or {}
        candidates = []
        notes = str(meeting.get("notes") or "").strip()
        asks = [str(item.get("ask")).strip() for item in structured_watch.get("asks", []) if item.get("ask")]
        deadlines = [str(item.get("deadline")).strip() for item in structured_watch.get("deadlines", []) if item.get("deadline")]

        if notes:
            candidates.append(
                build_follow_up_candidate(
                    f"Desired outcome: make the decision implied by the prep notes: {notes}",
                    family="meeting_notes",
                    priority=94.0,
                    topic_key=notes,
                )
            )
        for ask in asks[:2]:
            candidates.append(
                build_follow_up_candidate(
                    f"Desired outcome: confirm the next step and owner for {ask.rstrip('.')}.",
                    family="ask",
                    priority=88.0,
                    topic_key=ask,
                )
            )
        if deadlines:
            candidates.append(
                build_follow_up_candidate(
                    f"Desired outcome: leave with follow-up timing locked against {deadlines[0].rstrip('.')}.",
                    family="deadline",
                    priority=92.0,
                    topic_key=deadlines[0],
                )
            )
        selected = select_follow_up_candidates(candidates, limit=3)
        if selected:
            return [candidate.text for candidate in selected]
        return [
            "Desired outcome: leave with clear next steps and owners.",
            "Desired outcome: confirm what follow-up should happen before the next touchpoint.",
        ][:4]

    def _meeting_talking_points(
        self,
        event_payload: Dict[str, Any],
        ranked_threads: List[Dict[str, Any]],
    ) -> List[str]:
        points: List[str] = []
        for thread in (event_payload.get("attendee_threads") or ranked_threads)[:3]:
            subject = thread.get("subject", "")
            reasons = thread.get("importance_reasons") or []
            if subject and reasons:
                points.append(f"{subject}: {reasons[0]}")
            elif subject:
                points.append(subject)
        if not points:
            points = [
                "Align on current priorities and any blockers.",
                "Confirm outstanding deliverables and ownership.",
                "Identify any follow-up actions before the next touchpoint.",
            ]
        return points

    def _email_triage_buckets(self, ranked_threads: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        actionable = [
            thread for thread in ranked_threads
            if not thread.get("suppressed") and thread.get("category") != "promotional"
        ]
        top = actionable[:4]
        action_required: List[Dict[str, Any]] = []
        waiting_on_reply: List[Dict[str, Any]] = []
        fyi_only: List[Dict[str, Any]] = []

        for thread in actionable:
            subject = str(thread.get("subject") or "").lower()
            reasons = " ".join(str(item) for item in (thread.get("importance_reasons") or [])).lower()
            category = str(thread.get("category") or "").lower()
            level = str(thread.get("importance_level") or "medium").lower()
            combined = " ".join([subject, reasons, category])
            if any(marker in combined for marker in ("awaiting reply", "waiting on", "follow up", "follow-up", "nudge")):
                waiting_on_reply.append(thread)
                continue
            if level == "high" or category in {"finance", "investor", "board", "legal", "customer", "sales"}:
                action_required.append(thread)
                continue
            if any(marker in combined for marker in ("deadline", "approve", "review", "urgent", "decision", "contract")):
                action_required.append(thread)
                continue
            fyi_only.append(thread)

        for thread in top:
            if thread not in action_required and thread not in waiting_on_reply and thread not in fyi_only:
                fyi_only.append(thread)

        return {
            "top": top,
            "action": action_required[:4],
            "waiting": waiting_on_reply[:4],
            "fyi": fyi_only[:4],
        }

    def _email_thread_line(self, thread: Dict[str, Any], *, include_action: bool = False) -> str:
        sender = str(thread.get("latest_sender") or thread.get("sender") or "unknown sender")
        subject = str(thread.get("subject") or "Inbox thread")
        level = str(thread.get("importance_level") or "medium").upper()
        reasons = [str(item).strip() for item in (thread.get("importance_reasons") or []) if str(item).strip()]
        line = f"{sender} — {subject} [{level}]"
        if reasons:
            line += f": {reasons[0]}"
        if include_action:
            action = self._email_required_action(thread)
            if action:
                line += f" Action: {action}"
        return line

    def _email_required_action(self, thread: Dict[str, Any]) -> str:
        subject = str(thread.get("subject") or "this thread")
        category = str(thread.get("category") or "operations").replace("_", " ")
        lowered = f"{subject} {' '.join(str(item) for item in (thread.get('importance_reasons') or []))}".lower()
        if category in {"finance", "board", "investor"}:
            return f"CEO review today; align reply or decision for {subject}."
        if any(marker in lowered for marker in ("awaiting reply", "waiting on")):
            return f"Send a nudge or decide whether to escalate {subject}."
        if any(marker in lowered for marker in ("approve", "review", "deadline", "contract")):
            return f"Review and delegate next step on {subject} today."
        return f"Confirm owner and next step for {subject}."

    def _email_top_thread_items(self, threads: List[Dict[str, Any]]) -> List[str]:
        return [self._email_thread_line(thread, include_action=True) for thread in threads]

    def _email_action_required_items(self, threads: List[Dict[str, Any]], *, asks: List[str], deadlines: List[str]) -> List[str]:
        items = [self._email_thread_line(thread, include_action=True) for thread in threads[:3]]
        if asks:
            items.append(f"CEO or delegate: {str(asks[0]).strip().rstrip('.')} by the next review window.")
        if deadlines:
            items.append(f"Time-bound follow-through: {str(deadlines[0]).strip().rstrip('.')} requires ownership today.")
        return items[:4]

    def _email_waiting_on_reply_items(self, threads: List[Dict[str, Any]]) -> List[str]:
        items: List[str] = []
        for thread in threads[:3]:
            sender = str(thread.get("latest_sender") or thread.get("sender") or "counterparty")
            subject = str(thread.get("subject") or "follow-up")
            items.append(f"{sender} — {subject}: decide whether to nudge today or let it sit until tomorrow.")
        return items

    def _email_fyi_items(self, threads: List[Dict[str, Any]]) -> List[str]:
        items: List[str] = []
        for thread in threads[:3]:
            sender = str(thread.get("latest_sender") or thread.get("sender") or "sender")
            subject = str(thread.get("subject") or "thread")
            items.append(f"{sender} — {subject}: monitor, no direct CEO action needed now.")
        return items

    def _email_suggested_actions(
        self,
        triage: Dict[str, List[Dict[str, Any]]],
        structured_watch: Dict[str, Any],
        event_payload: Dict[str, Any],
    ) -> List[str]:
        candidates = []
        action_required = triage.get("action") or []
        waiting = triage.get("waiting") or []
        asks = [str(item.get("ask")).strip() for item in (structured_watch.get("asks", []) or []) if item.get("ask")]
        deadlines = [str(item.get("deadline")).strip() for item in (structured_watch.get("deadlines", []) or []) if item.get("deadline")]
        documents = [str(item.get("document")).strip() for item in (structured_watch.get("implied_docs", []) or []) if item.get("document")]

        if action_required:
            lead = action_required[0]
            subject = str(lead.get("subject") or "the top thread")
            candidates.append(
                build_follow_up_candidate(
                    f"CEO: reply or delegate {subject} today.",
                    family=f"thread:{str(lead.get('category') or 'thread').strip() or 'thread'}",
                    deadline_at=lead.get("due_at") or lead.get("deadline_at") or lead.get("starts_at"),
                    priority=95.0,
                    topic_key=str(lead.get("thread_id") or lead.get("id") or subject),
                )
            )
        if asks:
            candidates.append(
                build_follow_up_candidate(
                    f"Delegate to the right owner: {asks[0].rstrip('.')} today.",
                    family="ask",
                    priority=88.0,
                    topic_key=asks[0],
                )
            )
        if deadlines:
            candidates.append(
                build_follow_up_candidate(
                    f"Lock ownership against {deadlines[0].rstrip('.')} before end of day.",
                    family="deadline",
                    priority=92.0,
                    topic_key=deadlines[0],
                )
            )
        if waiting:
            subject = str(waiting[0].get("subject") or "the blocked thread")
            candidates.append(
                build_follow_up_candidate(
                    f"Nudge or escalate {subject} if no reply arrives by tomorrow morning.",
                    family=f"waiting:{str(waiting[0].get('category') or 'thread').strip() or 'thread'}",
                    deadline_at=waiting[0].get("due_at") or waiting[0].get("deadline_at") or waiting[0].get("starts_at"),
                    priority=78.0,
                    topic_key=str(waiting[0].get("thread_id") or waiting[0].get("id") or subject),
                )
            )
        if documents:
            candidates.append(
                build_follow_up_candidate(
                    f"Use {documents[0].rstrip('.')} as the supporting document for the response.",
                    family="document",
                    priority=60.0,
                    topic_key=documents[0],
                )
            )
        selected = select_follow_up_candidates(candidates, limit=4)
        if selected:
            return [candidate.text for candidate in selected]
        return [
                "CEO: clear the highest-urgency thread first.",
                "Delegate any operational follow-ups that do not need an executive reply.",
                "Turn the top email into a meeting prep or memo only if the thread warrants it.",
            ]

    def _morning_watch_summary(self, event_payload: Dict[str, Any]) -> str:
        ranked_threads = event_payload.get("ranked_threads", []) or []
        upcoming_events = self._filter_events_by_horizon(event_payload.get("upcoming_events", []) or [], event_payload)
        important_threads = len([thread for thread in ranked_threads if thread.get("importance_level") in {"high", "medium"}])
        planning_label = self._planning_window_label(event_payload)
        if important_threads or upcoming_events:
            return (
                f"{important_threads} important thread{'s' if important_threads != 1 else ''} "
                f"and {len(upcoming_events)} upcoming meeting{'s' if len(upcoming_events) != 1 else ''} "
                f"need attention for {planning_label}."
            )
        return "Here is the latest executive digest based on recent signals and activity."

    def _day_schedule_summary(self, event_payload: Dict[str, Any]) -> str:
        ranked_threads = event_payload.get("ranked_threads", []) or []
        upcoming_events = event_payload.get("upcoming_events", []) or []
        structured_watch = event_payload.get("structured_watch", {}) or {}
        planning_context = event_payload.get("planning_context", {}) or {}
        plan_execution = self._plan_execution(event_payload)
        deadline_count = len(structured_watch.get("deadlines", []) or [])
        actionable_threads = self._actionable_schedule_threads(ranked_threads)
        important_threads = len([thread for thread in actionable_threads if thread.get("importance_level") in {"high", "medium"}])
        if plan_execution.get("sparse_guidance") or self._has_weak_planning_evidence(ranked_threads, structured_watch, event_payload):
            return (
                f"There is not enough actionable inbox and calendar evidence to build a concrete "
                f"{self._planning_window_label(event_payload)} schedule yet."
            )
        if planning_context.get("mode") == "compound_plan":
            return f"{self._planning_window_label(event_payload)} schedule built from current inbox, calendar, and deadline signals."
        return "Today schedule built from current inbox, calendar, and deadline signals."

    def _schedule_specificity_mode(self, *, task_input: str, history: List[Dict[str, Any]]) -> bool:
        lowered = str(task_input or "").lower()
        specificity_markers = (
            "specific",
            "detailed",
            "breakdown",
            "task breakdown",
            "task list",
            "prioritized",
            "prioritised",
            "owners",
            "owner",
            "deadlines",
            "for each meeting",
            "follow-ups",
            "follow ups",
            "impact",
            "critical roles",
            "must happen today",
            "must happen",
            "defer",
            "delegate",
            "sequence",
            "what decisions",
        )
        if any(marker in lowered for marker in specificity_markers):
            return True
        recent_queries = " ".join(str(item.get("query") or item.get("message") or "") for item in (history or [])[:4]).lower()
        return any(marker in recent_queries for marker in ("plan my day", "plan my week", "schedule")) and any(
            marker in lowered for marker in ("what should", "help identify", "provide", "assess", "analyze", "analyse")
        )

    def _schedule_summary_for_followup(
        self,
        event_payload: Dict[str, Any],
        ranked_threads: List[Dict[str, Any]],
        structured_watch: Dict[str, Any],
        task_input: str = "",
    ) -> str:
        lowered = str(task_input or "").lower()
        top_threads = self._actionable_schedule_threads(ranked_threads)[:2]
        deadlines = [str(item.get("deadline")).strip() for item in (structured_watch.get("deadlines", []) or []) if item.get("deadline")]
        meetings = [str(event.get("title") or "meeting").strip() for event in (event_payload.get("upcoming_events", []) or [])[:2]]
        if "must happen" in lowered:
            priorities = [str(thread.get("subject") or "the lead thread") for thread in top_threads[:2]]
            if deadlines:
                priorities.append(deadlines[0].rstrip("."))
            if priorities:
                return f"Three outcomes matter most today: {', then '.join(priorities[:3])}."
        if "delegate" in lowered or "defer" in lowered:
            return "Separate today's work into what only the CEO can do, what can be delegated safely, and what can wait without creating downstream risk."
        if "sequence" in lowered:
            return "Sequence the day around the investor prep, the highest-risk customer thread, and the next hard deadline."
        fragments: List[str] = []
        if top_threads:
            fragments.append(f"Top priority: {top_threads[0].get('subject', 'the lead thread')}.")
        if deadlines:
            fragments.append(f"Hard stop: {deadlines[0].rstrip('.')}.")
        if meetings:
            fragments.append(f"Live constraint: {meetings[0]}.")
        if len(top_threads) > 1:
            fragments.append(f"Second priority: {top_threads[1].get('subject', 'the next thread')}.")
        return " ".join(fragments) or self._day_schedule_summary(event_payload)

    def _schedule_title(self, plan_mode: str | None, time_horizon: str) -> str:
        if plan_mode == "compound_plan":
            if time_horizon == "next_week":
                return "Next Week Executive Plan"
            if time_horizon == "this_week":
                return "This Week Executive Plan"
        return "Today Schedule Proposal"

    def _planning_inputs(self, event_payload: Dict[str, Any]) -> List[str]:
        planning_context = event_payload.get("planning_context", {}) or {}
        if not planning_context:
            return []

        items: List[str] = []
        target_label = planning_context.get("target_label")
        time_horizon = planning_context.get("time_horizon")
        if target_label:
            items.append(f"Planning horizon: {target_label}.")
        elif time_horizon and time_horizon != "unspecified":
            items.append(f"Planning horizon: {str(time_horizon).replace('_', ' ')}.")
        for subtask in (planning_context.get("subtasks", []) or [])[:3]:
            if isinstance(subtask, dict) and subtask.get("description"):
                items.append(str(subtask["description"]))
        evidence_summary = planning_context.get("evidence_summary", {}) or {}
        if evidence_summary:
            items.append(
                f"Evidence: {evidence_summary.get('actionable_thread_count', 0)} threads, {evidence_summary.get('meeting_count', 0)} meetings, {evidence_summary.get('context_source_count', 0)} sources."
            )
            if evidence_summary.get("placed_candidate_count") is not None:
                items.append(
                    f"Placed blocks: {evidence_summary.get('placed_candidate_count', 0)} of {evidence_summary.get('candidate_count', 0)} candidates."
                )
        return items

    def _schedule_followup_planning_inputs(
        self,
        *,
        event_payload: Dict[str, Any],
        ranked_threads: List[Dict[str, Any]],
        asks: List[str],
        deadlines: List[str],
    ) -> List[str]:
        horizon = self._planning_window_label(event_payload)
        priority_count = len(self._actionable_schedule_threads(ranked_threads))
        items = [f"Detailed task breakdown requested for {horizon} with {priority_count} active priority threads."]
        if asks:
            items.append(f"Execution emphasis: {asks[0].rstrip('.')} needs a named owner and timing.")
        elif deadlines:
            items.append(f"Execution emphasis: {deadlines[0].rstrip('.')} is the first time-bound commitment.")
        return items

    @staticmethod
    def _rank_questions_by_impact(questions: List[str], task_input: str = "") -> List[str]:
        """Reorder open questions so the one that most changes the answer comes first."""
        def score(q: str) -> int:
            ql = q.lower()
            s = 0
            if any(kw in ql for kw in ("which option", "which approach", "do you want", "prefer", "should i use", "a or b")):
                s += 4
            if any(kw in ql for kw in ("which period", "anchor to", "quarter", "ytd", "year-to-date", "current month", "rolling", "trailing")):
                s += 3
            if any(kw in ql for kw in ("what are the", "what is the", "share the", "source of truth", "numbers", "baseline")):
                s += 3
            if any(kw in ql for kw in ("board packet", "investor", "framed for", "operating decision")):
                s += 2
            if any(kw in ql for kw in ("compact", "relaxed", "spread", "one task", "batch")):
                s += 2
            if any(kw in ql for kw in ("key decision", "key outcome", "what decision", "what outcome")):
                s += 2
            if any(kw in ql for kw in ("is this correct", "is this assumption", "confirm", "assumption")):
                s += 1
            return s
        return sorted(questions, key=score, reverse=True)

    def _build_question_options_for_workflow(
        self,
        questions: List[str],
        workflow_type: str,
        task_input: str,
        payload: BriefPayload,
        ranked_threads: List[Dict[str, Any]] | None = None,
        company_state: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Build question_options: action offers first (proactive, prominent), then clarifications.
        Action offers fire when high-urgency threads or decision signals are present.
        """
        action_offers = self._build_briefing_action_offers(
            workflow_type=workflow_type,
            payload=payload,
            ranked_threads=ranked_threads or [],
            task_input=task_input,
            company_state=company_state or {},
        )
        clarifications = []
        for q in questions:
            opts = self._options_for_briefing_question(q, workflow_type, task_input)
            clarifications.append({"question": q, "options": opts, "offer_type": "clarification"})
        return action_offers + clarifications

    def _build_briefing_action_offers(
        self,
        workflow_type: str,
        payload: BriefPayload,
        ranked_threads: List[Dict[str, Any]],
        task_input: str,
        company_state: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Proactively offer to do the full work when the brief surfaces a high-stakes decision.
        Returns action offer entries for question_options — rendered prominently in the UI.
        Fires on high-urgency email threads OR high-signal company state conditions (E1).
        """
        semantic_context = self._semantic_context_from_threads(
            workflow_type=workflow_type,
            ranked_threads=ranked_threads,
        )
        return build_semantic_question_options(semantic_context)

        offer_candidates: List[dict[str, Any]] = []
        response_families = self._response_context_families(payload)
        allow_company_state_offers = bool(response_families.intersection({"customer", "finance", "board", "investor"}))

        def add_offer(*, question: str, family: str, priority: float, topic_key: str, offer: Dict[str, Any], deadline_at: Any = None) -> None:
            offer_candidates.append({
                "text": question,
                "family": family,
                "priority": priority,
                "topic_key": topic_key,
                "deadline_at": deadline_at,
                "offer": offer,
            })

        # E1: Company state signal offers — only when the current response already
        # includes a matching executive context family. This keeps next steps tied to
        # the just-generated brief instead of global company_state noise.
        cs = company_state or {}
        capital = cs.get("capital_position") or {}
        runway = capital.get("runway_months")
        thread_categories = {
            str(t.get("category") or "").lower()
            for t in (ranked_threads or [])
            if isinstance(t, dict)
        }
        has_customer_thread_context = bool(thread_categories.intersection({"customer", "legal"}))
        is_customer_execution_flow = workflow_type in {WorkflowType.EMAIL_INGESTION, WorkflowType.CALENDAR_BRIEFING}

        _customer_keywords = (
            "customer", "account", "deal", "renewal", "client", "churn",
            "at-risk", "at risk", "pipeline", "enterprise", "pilot",
        )
        _is_customer_query = any(kw in (task_input or "").lower() for kw in _customer_keywords)
        is_customer_focus = _is_customer_query or has_customer_thread_context or is_customer_execution_flow

        at_risk_initiatives = [
            i for i in (cs.get("strategic_initiatives") or [])
            if isinstance(i, dict) and str(i.get("status") or "").lower() in {"at risk", "at-risk", "risk"}
        ]

        # When the CEO is asking about customers/deals, prioritize a customer-facing offer.
        if allow_company_state_offers and is_customer_focus:
            # First try: at-risk strategic initiative → delegation offer
            if at_risk_initiatives:
                initiative = at_risk_initiatives[0]
                name = initiative.get("name", "the at-risk initiative")
                owner = initiative.get("owner", "the responsible owner")
                add_offer(
                    question=f'"{name}" is at risk — want me to draft the delegation email to {owner}?',
                    family="customer",
                    priority=95.0,
                    topic_key=str(name),
                    offer={
                        "question": f'"{name}" is at risk — want me to draft the delegation email to {owner}?',
                        "offer_type": "action_offer",
                        "options": [
                            {
                                "label": f"Draft delegation email to {owner}",
                                "value": "delegation_email",
                                "description": f"Ready-to-send delegation note scoped to {name}",
                                "apply_text": (
                                    f'Draft a delegation email to {owner} about "{name}". '
                                    "Be specific: what needs to happen, by when, and what CEO escalation looks like."
                                ),
                            },
                            {
                                "label": "Build initiative risk brief",
                                "value": "initiative_risk_brief",
                                "description": "Context, blockers, owners, and recommended recovery plan",
                                "apply_text": (
                                    f'Build a brief on the at-risk initiative "{name}". '
                                    "Include: current status, blockers, recommended recovery actions with owners."
                                ),
                            },
                        ],
                    },
                )
            else:
                # Fallback: knowledge_base may have at-risk accounts even if no formal initiative
                kb_entries = cs.get("knowledge_base") or []
                _risk_keywords = ("at risk", "at-risk", "renewal", "churn", "escalat", "overdue")
                risky_kb = [
                    e for e in kb_entries
                    if isinstance(e, dict) and any(
                        kw in str(e.get("content", "")).lower()
                        or kw in str(e.get("title", "")).lower()
                        for kw in _risk_keywords
                    )
                ]
                if risky_kb:
                    kb_title = risky_kb[0].get("title") or "the urgent customer situation"
                    add_offer(
                        question=f'I can draft the executive response for "{kb_title[:80]}", or pull the full account risk brief. Which should I do first?',
                        family="customer",
                        priority=92.0,
                        topic_key=kb_title,
                        offer={
                            "question": f'I can draft the executive response for "{kb_title[:80]}", or pull the full account risk brief. Which should I do first?',
                            "offer_type": "action_offer",
                            "options": [
                                {
                                    "label": "Draft executive response",
                                    "value": "draft_exec_response",
                                    "description": "Compose a CEO-level message for this situation now",
                                    "apply_text": (
                                        f'Draft an executive response addressing: "{kb_title}". '
                                        "Be direct, decisive, and under 120 words."
                                    ),
                                },
                                {
                                    "label": "Build risk brief",
                                    "value": "account_risk_brief",
                                    "description": "Deal status, risk factors, and recommended CEO action",
                                    "apply_text": (
                                        f'Build a full account risk brief on: "{kb_title}". '
                                        "Include: risk factors, what the CEO should do, and draft outreach options."
                                    ),
                                },
                            ],
                        },
                    )
                else:
                    # Generic customer urgency offer when no specific signal available
                    add_offer(
                        question="I can draft the customer escalation note now, or pull the full customer risk brief. Which should I do first?",
                        family="customer",
                        priority=85.0,
                        topic_key="customer_urgency",
                        offer={
                            "question": "I can draft the customer escalation note now, or pull the full customer risk brief. Which should I do first?",
                            "offer_type": "action_offer",
                            "options": [
                                {
                                    "label": "Draft escalation note",
                                    "value": "draft_customer_escalation_note",
                                    "description": "A ready-to-send executive note for the highest-risk customer issue",
                                    "apply_text": "Draft the executive customer escalation note I should send today, with a clear owner, deadline, and next commitment.",
                                },
                                {
                                    "label": "Build risk brief",
                                    "value": "customer_brief",
                                    "description": "All at-risk accounts, renewal status, and recommended actions",
                                    "apply_text": "Build a full customer situation brief: at-risk accounts, renewal timelines, and CEO action items.",
                                },
                            ],
                        },
                    )
        elif allow_company_state_offers and isinstance(runway, (int, float)) and runway < 12:
            add_offer(
                question=f"Runway is at {runway:.1f} months — want me to build a cost containment brief?",
                family="finance",
                priority=90.0,
                topic_key="runway",
                offer={
                    "question": f"Runway is at {runway:.1f} months — want me to build a cost containment brief?",
                    "offer_type": "action_offer",
                    "options": [
                        {
                            "label": "Build cost containment brief",
                            "value": "cost_containment_brief",
                            "description": f"Specific actions, owners, and runway impact ({runway:.1f} months current)",
                            "apply_text": (
                                "Build a cost containment brief. Include: specific actions this week, "
                                "named owner per action, dollar savings, and weeks of runway added per action."
                            ),
                        },
                        {
                            "label": "Show burn rate breakdown",
                            "value": "burn_rate_breakdown",
                            "description": "Where the monthly burn is going by category",
                            "apply_text": "Break down the monthly burn rate by category with variance vs plan.",
                        },
                    ],
                },
            )

        if allow_company_state_offers and at_risk_initiatives and not is_customer_focus:
            initiative = at_risk_initiatives[0]
            name = initiative.get("name", "the at-risk initiative")
            owner = initiative.get("owner", "the responsible owner")
            add_offer(
                question=f'"{name}" is at risk — want me to draft the delegation email to {owner}?',
                family="customer",
                priority=82.0,
                topic_key=name,
                offer={
                    "question": f'"{name}" is at risk — want me to draft the delegation email to {owner}?',
                    "offer_type": "action_offer",
                    "options": [
                        {
                            "label": f"Draft delegation email to {owner}",
                            "value": "draft_delegation_email",
                            "description": f"Email for {owner} with clear ownership and next steps",
                            "apply_text": (
                                f'Draft a delegation email to {owner} for "{name}". '
                                "Be specific: what they own, what the deadline is, and what escalation looks like."
                            ),
                        },
                        {
                            "label": "Build full risk brief",
                            "value": "initiative_risk_brief",
                            "description": "Context, blockers, owners, and recommended recovery plan",
                            "apply_text": (
                                f'Build a brief on the at-risk initiative "{name}". '
                                "Include: current status, blockers, recommended recovery actions with owners."
                            ),
                        },
                    ],
                },
            )

        if not offer_candidates:
            # Fall back to the most urgent actionable thread as the candidate source.
            high_urgency = [
                t for t in ranked_threads
                if not t.get("suppressed") and t.get("importance_level") in {"high"}
                and t.get("action_required")
            ]
            if high_urgency:
                top = high_urgency[0]
                subject = str(top.get("subject") or "this matter")
                thread_id = str(top.get("thread_id") or top.get("id") or "")
                category = str(top.get("category") or "")
                thread_id_hint = f" (thread ID: {thread_id})" if thread_id else ""

                if category in {"investor", "board"} or any(
                    w in subject.lower() for w in ("series", "funding", "investor", "board", "term sheet")
                ):
                    add_offer(
                        question=f"This looks like it needs a CEO decision: \"{subject}\"",
                        family="investor",
                        priority=98.0,
                        topic_key=subject,
                        deadline_at=top.get("due_at") or top.get("deadline_at") or top.get("starts_at"),
                        offer={
                            "question": f"This looks like it needs a CEO decision: \"{subject}\"",
                            "offer_type": "action_offer",
                            "options": [
                                {
                                    "label": "Build investor brief",
                                    "value": "investor_decision_brief",
                                    "description": "Funding context, timeline, and recommended position",
                                    "apply_text": (
                                        f"Build a decision brief for: {subject}{thread_id_hint}. "
                                        "Include: current funding context, timeline pressure, key terms to evaluate, "
                                        "and a clear CEO recommendation on how to respond."
                                    ),
                                },
                                {
                                    "label": "Draft CEO response",
                                    "value": "draft_investor_reply",
                                    "description": "Compose a reply ready to review",
                                    "apply_text": (
                                        f"Draft a CEO reply to: {subject}{thread_id_hint}. "
                                        "Keep it decisive and under 100 words."
                                    ),
                                },
                            ],
                        },
                    )
                elif category in {"customer", "legal"} or any(
                    w in subject.lower() for w in ("renewal", "risk", "escalat", "contract", "at risk")
                ):
                    add_offer(
                        question=f'I can draft the executive response for "{subject}", or build the full risk brief. Which should I do first?',
                        family="customer",
                        priority=94.0,
                        topic_key=subject,
                        deadline_at=top.get("due_at") or top.get("deadline_at") or top.get("starts_at"),
                        offer={
                            "question": f'I can draft the executive response for "{subject}", or build the full risk brief. Which should I do first?',
                            "offer_type": "action_offer",
                            "options": [
                                {
                                    "label": "Draft response",
                                    "value": "draft_risk_response",
                                    "description": "Compose an executive response",
                                    "apply_text": (
                                        f"Draft an executive response to address: {subject}{thread_id_hint}."
                                    ),
                                },
                                {
                                    "label": "Build risk brief",
                                    "value": "customer_risk_brief",
                                    "description": "Account context, risk factors, recommended action",
                                    "apply_text": (
                                        f"Build a full risk brief for: {subject}{thread_id_hint}. "
                                        "Include: account value, risk factors, relevant email context, "
                                        "recommended CEO action, and a draft response."
                                    ),
                                },
                            ],
                        },
                    )
                elif any(
                    w in subject.lower() for w in ("approve", "sign", "offer", "hire", "headcount", "compensation")
                ):
                    add_offer(
                        question=f"This needs your approval: \"{subject}\"",
                        family="approval",
                        priority=90.0,
                        topic_key=subject,
                        deadline_at=top.get("due_at") or top.get("deadline_at") or top.get("starts_at"),
                        offer={
                            "question": f"This needs your approval: \"{subject}\"",
                            "offer_type": "action_offer",
                            "options": [
                                {
                                    "label": "See full context",
                                    "value": "approval_context_brief",
                                    "description": "Relevant history and what approving commits you to",
                                    "apply_text": (
                                        f"Give me a concise brief on what I'm approving for: {subject}{thread_id_hint}. "
                                        "Include what it commits us to, any risks, and a recommendation."
                                    ),
                                },
                                {
                                    "label": "Draft approval reply",
                                    "value": "draft_approval_reply",
                                    "description": "Compose an approval email ready to send",
                                    "apply_text": (
                                        f"Draft an approval email for: {subject}{thread_id_hint}."
                                    ),
                                },
                            ],
                        },
                    )

        if not offer_candidates:
            return []

        selected = select_follow_up_candidates(offer_candidates, limit=1)
        if not selected:
            return []
        for candidate in offer_candidates:
            if candidate["text"] == selected[0].text and candidate["family"] == selected[0].family:
                return [candidate["offer"]]
        return [offer_candidates[0]["offer"]]

    def _response_context_families(self, payload: BriefPayload) -> set[str]:
        families: set[str] = set()
        for section in payload.answer.sections:
            label = str(section.label or "").lower()
            if any(token in label for token in ("thread", "inbox", "email")):
                families.add("email")
            if any(token in label for token in ("meeting", "calendar", "schedule", "deadline")):
                families.add("calendar")
            if any(token in label for token in ("finance", "revenue", "burn", "runway", "p&l", "profit", "loss", "margin", "cash")):
                families.add("finance")
            if any(token in label for token in ("customer", "deal", "renewal", "churn", "risk", "account")):
                families.add("customer")
            if any(token in label for token in ("board", "investor", "committee", "approval", "decision")):
                families.add("board")
        for source in payload.sources:
            role = str(source.get("role") or "").lower()
            source_type = str(source.get("type") or "").lower()
            if source_type == "artifact" and "finance" in role:
                families.add("finance")
            if "calendar" in role:
                families.add("calendar")
            if "inbox" in role or "email" in role:
                families.add("email")
            if any(token in role for token in ("board", "investor")):
                families.add("board")
        return families

    def _options_for_briefing_question(
        self,
        question: str,
        workflow_type: str,
        task_input: str,
    ) -> List[Dict[str, Any]]:
        ql = question.lower()

        # Schedule density
        if "compact" in ql or "relaxed" in ql or "single morning" in ql:
            return [
                {"label": "Compact — batch into fewer days", "value": "compact_schedule",
                 "apply_text": "Build me a compact schedule — batch similar work into focused days.",
                 "description": "Group similar tasks. Cover 2–3 days rather than spreading thin."},
                {"label": "Relaxed — one or two tasks per day", "value": "relaxed_schedule",
                 "apply_text": "Build me a relaxed schedule — one or two tasks per day across the week.",
                 "description": "Space to react and stay flexible across the full week."},
            ]

        # Calendar sync
        if "inbox" in ql and ("calendar" in ql or "sync" in ql):
            return [
                {"label": "Inbox only — proceed now", "value": "inbox_only",
                 "apply_text": "Build from inbox signals only — skip the calendar for now.",
                 "description": "Use email threads and deadlines. Ignore unsynced calendar."},
                {"label": "Wait for calendar sync", "value": "wait_calendar",
                 "apply_text": "Sync my calendar first, then rebuild the schedule.",
                 "description": "Hold the schedule until calendar events are loaded."},
            ]

        # Meeting outcome
        if "decision" in ql or "outcome" in ql or "agenda" in ql:
            return [
                {"label": "I'll describe the outcome", "value": "describe_outcome",
                 "apply_text": "The key outcome I need from this meeting is:",
                 "description": "Tell me what decision or result the meeting needs."},
                {"label": "Build from available context", "value": "use_context",
                 "apply_text": "Build the prep from whatever context you have.",
                 "description": "Use whatever is loaded — email threads, company state."},
            ]

        # Expand into fuller report
        if "expanded" in ql or "fuller report" in ql or "follow-up brief" in ql:
            return [
                {"label": "Expand to full report", "value": "expand_report",
                 "apply_text": "Expand this into a full executive report with sources and recommendations.",
                 "description": "Detailed report with supporting data."},
                {"label": "Keep it as a brief", "value": "keep_brief",
                 "apply_text": "Keep this as a concise brief — no expansion needed.",
                 "description": "Stay at the current level of detail."},
            ]

        # Universal fallback — always 2 tappable options, never open-ended
        return [
            {"label": "My decision", "value": "personal_decision",
             "apply_text": "Format this for my own operating decision — direct and concise.",
             "description": "Internal operating view."},
            {"label": "Board presentation", "value": "board_presentation",
             "apply_text": "Format this for a board presentation — structured and polished.",
             "description": "Board-ready language and format."},
        ]

    def _derive_trust(
        self,
        workflow_type: str,
        event_payload: Dict[str, Any],
        prepared_context: Dict[str, Any],
        *,
        payload: BriefPayload,
    ) -> BriefTrust:
        ranked_threads = event_payload.get("ranked_threads", []) or []
        structured_watch = event_payload.get("structured_watch", {}) or {}
        upcoming_events = event_payload.get("upcoming_events", []) or []
        planning_context = event_payload.get("planning_context", {}) or {}
        plan_execution = self._plan_execution(event_payload)
        signals = prepared_context.get("signals", []) or []

        actionable_threads = [thread for thread in ranked_threads if not thread.get("suppressed")]
        top_thread = ranked_threads[0] if ranked_threads else {}
        deadline_count = len(structured_watch.get("deadlines", []) or [])
        meeting_count = len(upcoming_events)
        context_source_count = self._context_source_count(event_payload, planning_context, signals)
        placed_candidate_count = int((plan_execution.get("evidence_summary", {}) or {}).get("placed_candidate_count", 0))
        sparse_guidance = bool(plan_execution.get("sparse_guidance"))
        evidence_score = 0.15
        assumptions: List[str] = []
        open_questions = ["Expand to a full report or keep it as a brief?"]
        missing_context: List[str] = []

        # Thread scoring — skip for calendar-only workflows where threads are never present
        if workflow_type != "calendar_briefing":
            if len(actionable_threads) >= 2:
                evidence_score += 0.24
            elif len(actionable_threads) == 1:
                evidence_score += 0.12
            else:
                missing_context.append("Inbox context is thin.")

        if meeting_count >= 2:
            evidence_score += 0.2
        elif meeting_count == 1:
            # Calendar_briefing lives or dies by its meetings — weight them more heavily
            evidence_score += 0.35 if workflow_type == "calendar_briefing" else 0.12
        elif workflow_type in {"calendar_briefing", "schedule_planning", "morning_brief", "weekly_recap", "meeting_prep"}:
            missing_context.append("Calendar context is thin.")

        if deadline_count >= 2:
            evidence_score += 0.16
        elif deadline_count == 1:
            evidence_score += 0.1

        if context_source_count >= 3:
            evidence_score += 0.18
        elif context_source_count == 2:
            evidence_score += 0.1
        else:
            missing_context.append("Supporting context is narrow.")

        if placed_candidate_count >= 3:
            evidence_score += 0.12
        elif placed_candidate_count >= 1:
            evidence_score += 0.06
        elif workflow_type in {"schedule_planning"}:
            missing_context.append("Planning candidates need more structure before they can be placed cleanly.")

        if signals:
            evidence_score += 0.05

        if top_thread and (top_thread.get("suppressed") or top_thread.get("category") == "promotional"):
            evidence_score -= 0.22
            missing_context.append("Top inbox evidence is suppressed or promotional.")

        if planning_context.get("mode") == "compound_plan":
            execution_steps = planning_context.get("execution_steps", []) or []
            completed_steps = [
                step for step in execution_steps if isinstance(step, dict) and step.get("status") == "completed"
            ]
            if len(completed_steps) >= 3:
                evidence_score += 0.12
            else:
                missing_context.append("Compound planning evidence is only partially assembled.")
            assumptions.append("The schedule proposal is generated through a planner-executed compound evidence path inside the planning workflow.")
        else:
            assumptions.append("The latest stored signals and company context are current enough for a briefing view.")

        if sparse_guidance:
            evidence_score -= 0.08
            missing_context.append("Weekly evidence was thin, so the plan used a lighter guidance path.")

        # Calendar briefing is valid with meetings alone — floor at medium if any meetings present
        if workflow_type == "calendar_briefing" and meeting_count > 0:
            evidence_score = max(evidence_score, 0.5)

        confidence_score = max(0.1, min(round(evidence_score, 2), 0.95))
        confidence = "high" if confidence_score >= 0.78 else "medium" if confidence_score >= 0.48 else "low"
        data_quality = "high" if confidence_score >= 0.75 else "medium" if confidence_score >= 0.45 else "low"
        evidence_reasons: List[str] = []
        if len(actionable_threads) == 0 and workflow_type != "calendar_briefing":
            evidence_reasons.append("Inbox context is thin.")
        if meeting_count == 0 and workflow_type in {"calendar_briefing", "schedule_planning", "morning_brief", "weekly_recap", "meeting_prep"}:
            evidence_reasons.append("Calendar context is thin.")
        if context_source_count <= 1:
            evidence_reasons.append("Supporting context is narrow.")
        if sparse_guidance:
            evidence_reasons.append("Weekly evidence was thin, so the planner used a lighter guidance path.")

        task_input = prepared_context.get("task_input") or prepared_context.get("query") or ""
        ranked_questions = self._rank_questions_by_impact(open_questions, task_input)
        semantic_seed = self._semantic_context_from_threads(
            workflow_type=workflow_type,
            ranked_threads=ranked_threads,
        )
        semantic_context = build_semantic_context(
            title=payload.answer.title,
            summary=payload.answer.summary,
            sections=[section.model_dump(mode="json") for section in payload.answer.sections],
            sources=payload.sources,
            confidence_score=confidence_score,
            evidence_state="sparse" if confidence == "low" or sparse_guidance else "mixed" if evidence_reasons else "strong",
            missing_context=missing_context,
            workflow_type=workflow_type,
            response_type="brief",
            topic_hint=semantic_seed.topic,
            date_hint=semantic_seed.date,
            importance_hint=semantic_seed.importance,
            families_hint=semantic_seed.families,
            source_ids_hint=semantic_seed.source_ids,
        )
        return BriefTrust(
            confidence=confidence,
            confidence_score=confidence_score,
            assumptions=assumptions,
            open_questions=ranked_questions,
            data_quality=data_quality,
            calculation_used=False,
            missing_context=missing_context,
            evidence_state="sparse" if confidence == "low" or sparse_guidance else "mixed" if evidence_reasons else "strong",
            evidence_reasons=evidence_reasons,
            safe_to_act=confidence == "high" and not missing_context,
            question_options=build_semantic_question_options(semantic_context),
            semantic_context=semantic_context,
        )

    def _context_source_count(
        self,
        event_payload: Dict[str, Any],
        planning_context: Dict[str, Any],
        signals: List[Dict[str, Any]],
    ) -> int:
        evidence_summary = planning_context.get("evidence_summary", {}) or {}
        if evidence_summary.get("context_source_count"):
            return int(evidence_summary["context_source_count"])

        count = 0
        if event_payload.get("ranked_threads"):
            count += 1
        if event_payload.get("upcoming_events"):
            count += 1
        if event_payload.get("document_context") or event_payload.get("attachments"):
            count += 1
        if signals:
            count += 1
        return count

    def _schedule_focus_items(
        self,
        ranked_threads: List[Dict[str, Any]],
        structured_watch: Dict[str, Any],
        upcoming_events: List[Dict[str, Any]],
    ) -> List[str]:
        if self._has_weak_planning_evidence(
            ranked_threads,
            structured_watch,
            {"upcoming_events": upcoming_events},
        ):
            return [self._insufficient_planning_copy({"upcoming_events": upcoming_events})]
        focus_items: List[str] = []
        for thread in self._actionable_schedule_threads(ranked_threads)[:2]:
            focus_items.append(
                f"Review {thread.get('subject', 'top thread')} from {thread.get('latest_sender', 'a sender')}."
            )
        for deadline in (structured_watch.get("deadlines", []) or [])[:2]:
            if deadline.get("deadline"):
                focus_items.append(f"Account for deadline: {deadline.get('deadline')}.")
        for event in upcoming_events[:2]:
            focus_items.append(f"Prepare for {event.get('title', 'upcoming meeting')} at {event.get('starts_at', 'scheduled')}.")
        return focus_items[:5] or ["Start with the highest-signal item from your inbox and calendar."]

    def _schedule_blocks(
        self,
        ranked_threads: List[Dict[str, Any]],
        structured_watch: Dict[str, Any],
        event_payload: Dict[str, Any],
    ) -> List[str]:
        upcoming_events = (event_payload.get("upcoming_events", []) or [])[:3]
        asks = [item.get("ask") for item in (structured_watch.get("asks", []) or []) if item.get("ask")]
        deadlines = [item.get("deadline") for item in (structured_watch.get("deadlines", []) or []) if item.get("deadline")]
        documents = [item.get("document") for item in (structured_watch.get("implied_docs", []) or []) if item.get("document")]
        actionable_threads = self._actionable_schedule_threads(ranked_threads)

        if self._has_weak_planning_evidence(ranked_threads, structured_watch, event_payload):
            return [self._insufficient_planning_copy(event_payload)]

        action_specs = self._build_ranked_schedule_actions(
            ranked_threads=actionable_threads,
            upcoming_events=upcoming_events,
            asks=asks,
            deadlines=deadlines,
            documents=documents,
            event_payload=event_payload,
        )
        if not action_specs:
            return [self._insufficient_planning_copy(event_payload)]
        window = self._resolved_planning_window(event_payload)
        reference_dates: List[date] | None = None
        start_raw = window.get("start_date")
        end_raw = window.get("end_date")
        if start_raw and end_raw:
            try:
                w_start = date.fromisoformat(str(start_raw))
                w_end = date.fromisoformat(str(end_raw))
                reference_dates = [
                    w_start + timedelta(days=i)
                    for i in range((w_end - w_start).days + 1)
                    if (w_start + timedelta(days=i)).weekday() < 5
                ]
            except ValueError:
                reference_dates = None

        max_per_day = 2 if reference_dates and len(reference_dates) > 1 else None
        free_slots = self._compute_free_slots(
            upcoming_events,
            slot_lengths=[int(action["duration_minutes"]) for action in action_specs],
            reference_dates=reference_dates or None,
            max_per_day=max_per_day,
        )
        if not free_slots:
            return [
                self._no_free_slots_copy(event_payload)
            ]

        is_multi_day = reference_dates is not None and len(reference_dates) > 1
        blocks: List[str] = []
        for index, action in enumerate(action_specs):
            if index >= len(free_slots):
                break
            slot_start, slot_end = free_slots[index]
            time_str = (
                f"{slot_start.strftime('%a')} {self._format_time_range(slot_start, slot_end)}"
                if is_multi_day
                else self._format_time_range(slot_start, slot_end)
            )
            blocks.append(
                f"{time_str}: {self._compact_schedule_text(action['content'])}"
            )
        return blocks

    def _schedule_task_breakdown(
        self,
        *,
        ranked_threads: List[Dict[str, Any]],
        structured_watch: Dict[str, Any],
        event_payload: Dict[str, Any],
        task_input: str = "",
    ) -> List[str]:
        lowered = str(task_input or "").lower()
        actionable_threads = self._actionable_schedule_threads(ranked_threads)
        deadlines = [str(item.get("deadline")).strip() for item in (structured_watch.get("deadlines", []) or []) if item.get("deadline")]
        asks = [str(item.get("ask")).strip() for item in (structured_watch.get("asks", []) or []) if item.get("ask")]
        meetings = event_payload.get("upcoming_events", []) or []
        documents = [str(item.get("document")).strip() for item in (structured_watch.get("implied_docs", []) or []) if item.get("document")]

        if self._has_weak_planning_evidence(ranked_threads, structured_watch, event_payload):
            return [self._insufficient_planning_copy(event_payload)]

        tasks: List[str] = []
        if "must happen" in lowered:
            if actionable_threads:
                top = actionable_threads[0]
                tasks.append(f"P1 CEO — decide the next move on {top.get('subject', 'the lead thread')} before lunch.")
            if deadlines:
                tasks.append(f"P1 Delegate to Finance — lock ownership and final delivery against {deadlines[0].rstrip('.')} by end of day.")
            if meetings:
                meeting = meetings[0]
                tasks.append(f"P2 CEO — enter {meeting.get('title', 'the next meeting')} with one decision, one risk, and one ask prepared.")
            return tasks[:4]
        if "delegate" in lowered or "defer" in lowered:
            if actionable_threads:
                tasks.append(f"DO NOW | CEO — personally handle {actionable_threads[0].get('subject', 'the top priority')} because it needs an executive decision today.")
            if asks:
                tasks.append(f"DELEGATE | Operations — own {asks[0].rstrip('.')} and report back by end of day.")
            if documents:
                tasks.append(f"DEFER | Chief of Staff — move {documents[0].rstrip('.')} until after the live investor and escalation work is locked.")
            if deadlines:
                tasks.append(f"DO NOT DEFER | Finance — {deadlines[0].rstrip('.')} needs a named owner now.")
            return tasks[:4]
        if "sequence" in lowered:
            if deadlines:
                tasks.append(f"1. Before 10 AM — confirm owner and narrative for {deadlines[0].rstrip('.')}.")
            if meetings:
                tasks.append(f"2. Before {meetings[0].get('starts_at') or 'the next meeting'} — prep decision points for {meetings[0].get('title', 'the next meeting')}.")
            if actionable_threads:
                tasks.append(f"3. After the first meeting — convert {actionable_threads[0].get('subject', 'the lead thread')} into a same-day decision or delegated task.")
            if len(meetings) > 1:
                tasks.append(f"4. Before {meetings[1].get('starts_at') or 'the later meeting'} — finalize investor-facing numbers and open risks.")
            return tasks[:4]
        if actionable_threads:
            top = actionable_threads[0]
            tasks.append(
                f"P1 CEO — {top.get('subject', 'Priority thread')}: decide the next move today"
                + (f" before {deadlines[0].rstrip('.')}" if deadlines else ".")
            )
        if asks:
            tasks.append(f"P1 Delegate to Operations — {asks[0].rstrip('.')} by end of day.")
        if meetings:
            meeting = meetings[0]
            meeting_title = str(meeting.get("title") or "the next meeting").strip()
            meeting_time = meeting.get("starts_at") or "the scheduled start"
            tasks.append(f"P2 CEO — prep for {meeting_title} before {meeting_time}.")
        if deadlines:
            tasks.append(f"P2 Finance or owner — lock deliverable against {deadlines[0].rstrip('.')} with explicit ownership.")
        if documents:
            tasks.append(f"P3 Delegate to Chief of Staff — update {documents[0].rstrip('.')} before external follow-up.")
        return tasks[:4]

    def _build_ranked_schedule_actions(
        self,
        *,
        ranked_threads: List[Dict[str, Any]],
        upcoming_events: List[Dict[str, Any]],
        asks: List[str],
        deadlines: List[str],
        documents: List[str],
        event_payload: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []

        for index, thread in enumerate(ranked_threads[:3]):
            score = int(thread.get("importance_score", 0))
            if index == 0:
                score += 20
            topic = self._clean_thread_subject_for_schedule(thread.get("subject", "priority thread"))
            actions.append(
                {
                    "duration_minutes": 30,
                    "urgency": score,
                    "content": f"Address {topic} — decision or delegate.",
                }
            )

        for index, deadline in enumerate(deadlines[:2]):
            actions.append(
                {
                    "duration_minutes": 30 if index == 0 else 15,
                    "urgency": self._deadline_urgency(deadline),
                    "content": f"Work items tied to {deadline}.",
                }
            )

        for index, ask in enumerate(asks[:2]):
            actions.append(
                {
                    "duration_minutes": 30 if index == 0 else 15,
                    "urgency": 78 - (index * 8),
                    "content": f"Handle ask: {ask}.",
                }
            )

        for index, event in enumerate(upcoming_events[:2]):
            actions.append(
                {
                    "duration_minutes": 30 if index == 0 else 15,
                    "urgency": self._meeting_prep_urgency(event),
                    "content": f"Prep for {event.get('title', 'the upcoming meeting')}.",
                }
            )

        for index, document in enumerate(documents[:2]):
            actions.append(
                {
                    "duration_minutes": 15,
                    "urgency": 46 - (index * 6),
                    "content": f"Outline {document}.",
                }
            )

        if actions:
            actions.append(
                {
                    "duration_minutes": 15,
                    "urgency": 24,
                    "content": f"Close {self._planning_window_label(event_payload)} with final approvals.",
                }
            )

        actions.sort(key=lambda item: (int(item["urgency"]), int(item["duration_minutes"])), reverse=True)
        return actions[:10]

    def _clean_thread_subject_for_schedule(self, subject: str) -> str:
        """Strip urgency/time-relative suffixes from email subjects for use in schedule blocks."""
        cleaned = re.sub(r"^Re:\s*", "", str(subject).strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*—\s*.+$", "", cleaned)  # strip " — everything after em-dash"
        cleaned = re.sub(r"\s*-\s*(needs|please|asap|today|urgent).+$", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip() or subject

    def _actionable_schedule_threads(self, ranked_threads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            thread
            for thread in ranked_threads
            if not thread.get("suppressed") and thread.get("category") != "promotional"
        ]

    def _has_weak_planning_evidence(
        self,
        ranked_threads: List[Dict[str, Any]],
        structured_watch: Dict[str, Any],
        event_payload: Dict[str, Any],
    ) -> bool:
        actionable_threads = self._actionable_schedule_threads(ranked_threads)
        asks = [item.get("ask") for item in (structured_watch.get("asks", []) or []) if item.get("ask")]
        deadlines = [item.get("deadline") for item in (structured_watch.get("deadlines", []) or []) if item.get("deadline")]
        documents = [item.get("document") for item in (structured_watch.get("implied_docs", []) or []) if item.get("document")]
        meetings = event_payload.get("upcoming_events", []) or []
        return not any([actionable_threads, asks, deadlines, documents, meetings])

    def _planning_window_label(self, event_payload: Dict[str, Any]) -> str:
        planning_window = self._resolved_planning_window(event_payload)
        if planning_window.get("target_label"):
            return str(planning_window.get("target_label"))
        horizon = planning_window.get("horizon", "today")
        if horizon == "next_week":
            return "next week"
        if horizon == "this_week":
            return "this week"
        if horizon == "tomorrow":
            return "tomorrow"
        return "today"

    def _resolved_planning_window(self, event_payload: Dict[str, Any]) -> Dict[str, Any]:
        plan_execution = self._plan_execution(event_payload)
        planning_window = plan_execution.get("planning_window", {}) if plan_execution else {}
        if planning_window:
            return planning_window if isinstance(planning_window, dict) else {}

        planning_context = event_payload.get("planning_context", {}) or {}
        target_date_raw = planning_context.get("target_date")
        target_date = None
        if target_date_raw:
            try:
                target_date = date.fromisoformat(str(target_date_raw))
            except ValueError:
                target_date = None
        window = build_planning_window(
            str(planning_context.get("time_horizon", "unspecified")),
            target_date=target_date,
            target_label=planning_context.get("target_label"),
        )
        return window.model_dump()

    def _plan_execution(self, event_payload: Dict[str, Any]) -> Dict[str, Any]:
        value = event_payload.get("plan_execution", {}) or {}
        return value if isinstance(value, dict) else {}

    def _precomputed_schedule_blocks(self, event_payload: Dict[str, Any]) -> List[str]:
        plan_execution = self._plan_execution(event_payload)
        blocks = plan_execution.get("schedule_blocks", []) or []
        return [self._compact_schedule_block(str(block)) for block in blocks if str(block).strip()]

    def _compact_schedule_block(self, block: str) -> str:
        normalized = " ".join(str(block).strip().split())
        match = re.match(
            r"^(?:[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+)?"
            r"(\d{1,2}:\d{2}\s*(?:AM|PM)\s*-\s*\d{1,2}:\d{2}\s*(?:AM|PM)):\s*(.+)$",
            normalized,
            re.IGNORECASE,
        )
        if not match:
            return self._compact_schedule_text(normalized)
        return f"{match.group(1)}: {self._compact_schedule_text(match.group(2))}".strip()

    def _compact_schedule_text(self, text: str) -> str:
        normalized = " ".join(str(text).strip().split())
        normalized = re.sub(r"\bat\s+\d{1,2}:\d{2}\s*(?:AM|PM)\b\.?", "", normalized, flags=re.IGNORECASE)
        normalized = normalized.replace(" and decide next step.", "")
        normalized = normalized.replace(" and decide the next step.", "")
        normalized = normalized.replace("Work items tied to ", "Work ")
        normalized = normalized.replace("Prepare for", "Prep for")
        normalized = normalized.replace(" so nothing slips.", "")
        return normalized.strip().rstrip(".")

    def _insufficient_planning_copy(self, event_payload: Dict[str, Any]) -> str:
        return (
            f"There is not enough actionable inbox or calendar evidence to build a concrete "
            f"schedule for {self._planning_window_label(event_payload)}."
        )

    def _planning_no_deadlines_copy(self, event_payload: Dict[str, Any]) -> str:
        return f"No clear deadlines were detected for {self._planning_window_label(event_payload)}."

    def _planning_no_meetings_copy(self, event_payload: Dict[str, Any]) -> str:
        return f"No meetings were pulled into the proposed plan for {self._planning_window_label(event_payload)}."

    def _no_free_slots_copy(self, event_payload: Dict[str, Any]) -> str:
        return (
            f"No clear free slots were found in the calendar window for {self._planning_window_label(event_payload)}. "
            "Review the strongest remaining item manually."
        )

    def _weak_planning_follow_ups(self, event_payload: Dict[str, Any]) -> List[str]:
        return [
            f"Review calendar coverage for {self._planning_window_label(event_payload)}.",
            "Confirm whether there are any real priority threads worth planning around.",
            "Rerun the plan after fresher inbox or calendar context is available.",
        ]

    def _schedule_follow_ups(
        self,
        *,
        asks: List[str],
        event_payload: Dict[str, Any],
        ranked_threads: List[Dict[str, Any]],
        task_input: str = "",
        detailed: bool = False,
    ) -> List[str]:
        candidates = []
        for ask in asks[:2]:
            ask_text = str(ask).strip()
            if ask_text:
                candidates.append(
                    build_follow_up_candidate(
                        f"Delegate to the right owner: {ask_text.rstrip('.')}.",
                        family="ask",
                        priority=90.0,
                        topic_key=ask_text,
                    )
                )
        for event in (event_payload.get("upcoming_events", []) or [])[:2]:
            title = str(event.get("title") or "").strip()
            if title:
                candidates.append(
                    build_follow_up_candidate(
                        f"Prep for {title}.",
                        family=f"meeting:{title}",
                        deadline_at=event.get("starts_at"),
                        priority=88.0,
                        topic_key=str(event.get("meeting_id") or event.get("event_id") or title),
                    )
                )
        for thread in self._actionable_schedule_threads(ranked_threads)[:4]:
            subject = str(thread.get("subject") or "").strip()
            if not subject:
                continue
            family = str(thread.get("category") or "thread").strip() or "thread"
            candidates.append(
                build_follow_up_candidate(
                    f"Convert {subject} into a same-day decision or delegated task.",
                    family=f"thread:{family}",
                    deadline_at=thread.get("due_at") or thread.get("deadline_at") or thread.get("starts_at"),
                    priority=85.0 if str(thread.get("importance_level") or "").lower() == "high" else 72.0,
                    topic_key=str(thread.get("thread_id") or thread.get("id") or subject),
                )
            )
        for item in ((event_payload.get("structured_watch", {}) or {}).get("deadlines", []) or []):
            deadline = str(item.get("deadline") or "").strip()
            if deadline:
                candidates.append(
                    build_follow_up_candidate(
                        f"Lock ownership against {deadline.rstrip('.')}.",
                        family="deadline",
                        deadline_at=item.get("due_at") or item.get("due_date"),
                        priority=92.0,
                        topic_key=deadline,
                    )
                )
        for item in ((event_payload.get("structured_watch", {}) or {}).get("implied_docs", []) or []):
            document = str(item.get("document") or "").strip()
            if document:
                candidates.append(
                    build_follow_up_candidate(
                        f"Use {document.rstrip('.')} as the supporting document for the response.",
                        family="document",
                        priority=60.0,
                        topic_key=document,
                    )
                )

        selected = select_follow_up_candidates(candidates, limit=3)
        items = [candidate.text for candidate in selected] if selected else []
        if detailed:
            detailed_items: List[str] = []
            if items:
                detailed_items.append(f"Name the owner and deadline for {items[0].rstrip('.')}.")
            top_threads = self._actionable_schedule_threads(ranked_threads)
            if top_threads:
                detailed_items.append(
                    f"Convert {top_threads[0].get('subject', 'the top thread')} into a same-day decision or delegated task."
                )
            if "meeting" in str(task_input or "").lower() or event_payload.get("upcoming_events"):
                detailed_items.append("Tie each upcoming meeting to one prep task and one post-meeting follow-up.")
            if detailed_items:
                return detailed_items[:3]
        if not items:
            items = self._next_actions(
                "schedule_planning",
                ranked_threads,
                self._semantic_context_from_threads(workflow_type="schedule_planning", ranked_threads=ranked_threads),
            )
        return items[:3]

    def _join_schedule_actions(self, items: List[str]) -> str:
        cleaned = [str(item).strip().rstrip(".") for item in items if str(item).strip()]
        if not cleaned:
            return ""

    def _live_context_prompt_block(self, live_context: Dict[str, Any]) -> str:
        if not isinstance(live_context, dict):
            return ""
        if not any(
            [
                live_context.get("current_schedule"),
                live_context.get("open_decisions"),
                live_context.get("open_commitments"),
                live_context.get("entities_in_play"),
                live_context.get("last_agent_contributions"),
            ]
        ):
            return ""
        lines = ["=== THIS CONVERSATION SO FAR ==="]
        schedule = live_context.get("current_schedule") or {}
        if isinstance(schedule, dict) and schedule:
            lines.append(f"Schedule built (turn {schedule.get('turn', '?')}): {len(schedule.get('blocks') or [])} blocks")
            blocks = schedule.get("blocks") or []
            if blocks:
                block_labels = []
                for block in blocks[:5]:
                    if isinstance(block, dict):
                        label = str(block.get("title") or "Untitled block")
                        window = str(block.get("time_window") or block.get("starts_at") or "").strip()
                        block_labels.append(f"{window} {label}".strip())
                if block_labels:
                    lines.append("Schedule blocks: " + "; ".join(block_labels))
            meetings = schedule.get("meetings") or []
            if meetings:
                meeting_labels = []
                for meeting in meetings[:4]:
                    if isinstance(meeting, dict):
                        meeting_labels.append(
                            f"{meeting.get('title', 'Meeting')} @ {meeting.get('starts_at', '')}".strip()
                        )
                if meeting_labels:
                    lines.append("Scheduled meetings: " + "; ".join(meeting_labels))
            deadlines = schedule.get("deadlines") or []
            if deadlines:
                lines.append("Scheduled deadlines: " + "; ".join(str(item) for item in deadlines[:4]))
        decisions = live_context.get("open_decisions") or []
        if decisions:
            lines.append("Open decisions: " + "; ".join(str(item) for item in decisions[:3]))
        commitments = live_context.get("open_commitments") or []
        if commitments:
            lines.append("Open commitments: " + "; ".join(str(item) for item in commitments[:3]))
        entities = live_context.get("entities_in_play") or {}
        if isinstance(entities, dict) and entities:
            entity_lines = [f"{key}: {value}" for key, value in list(entities.items())[:5]]
            lines.append("Entities in play:\n  " + "\n  ".join(entity_lines))
        contributions = live_context.get("last_agent_contributions") or []
        for contribution in contributions[-2:]:
            if isinstance(contribution, dict):
                lines.append(
                    f"  [{contribution.get('actor')} — turn {contribution.get('turn')}]: "
                    f"{str(contribution.get('content_summary') or '')[:150]}"
                )
        return "\n".join(lines) + "\n\n"

    def _situational_prompt_block(self, situational: Dict[str, Any]) -> str:
        if not isinstance(situational, dict) or not situational:
            return ""
        lines = ["=== CEO CURRENT SITUATION ===", f"Operating mode: {situational.get('operating_mode', 'standard')}"]
        pressures = situational.get("active_pressures") or []
        if pressures:
            lines.append("Active pressures: " + "; ".join(str(item) for item in pressures[:3]))
        recurring = [topic for topic in (situational.get("recurring_topics") or []) if isinstance(topic, dict) and not topic.get("resolved")]
        if recurring:
            topics_str = ", ".join(f"{topic.get('topic')} (x{topic.get('mention_count', 1)})" for topic in recurring[:4])
            lines.append(f"Recurring unresolved topics: {topics_str}")
        obligations = situational.get("relationship_obligations") or []
        if obligations:
            lines.append("Relationship obligations: " + "; ".join(str(item) for item in obligations[:3]))
        return "\n".join(lines) + "\n\n"

    def _extract_entities(self, payload: BriefPayload) -> List[str]:
        text = payload.answer.summary + " " + " ".join(
            item
            for section in payload.answer.sections
            for item in section.items
        )
        candidates = re.findall(
            r"\b[A-Z][A-Za-z0-9&.-]+(?: [A-Z][A-Za-z0-9&.-]+){0,3} (?:deal|call|review|pack|plan|project|meeting)\b",
            text,
        )
        return list(dict.fromkeys(candidates))[:8]

    def _build_thread_entry_action(self, *, agent_input: AgentInput, workflow_type: str, payload: BriefPayload):
        schedule_payload = None
        weekly_plan = payload.presentation.weekly_plan if payload.presentation else None
        if weekly_plan:
            schedule_payload = {
                "blocks": [block.model_dump() for block in weekly_plan.blocks],
                "meetings": [meeting.model_dump() for meeting in weekly_plan.meetings],
                "deadlines": list(weekly_plan.deadlines or []),
                "planning_window": weekly_plan.planning_window.model_dump() if weekly_plan.planning_window else None,
            }
        entry_type = "schedule" if schedule_payload else "contribution"
        contribution_payload = schedule_payload or {
            "key_findings": [
                f"{section.label}: {section.content or '; '.join(section.items[:3])}"
                for section in payload.answer.sections[:3]
            ]
        }
        return tool_action(
            "write_thread_entry",
            entry_type=entry_type,
            actor=self.metadata.name,
            content=payload.answer.summary[:300],
            structured_payload=contribution_payload,
            entities=self._extract_entities(payload),
            conversation_id=agent_input.metadata.get("conversation_id"),
            turn=agent_input.workflow_state.metadata.get("turn_count", 0),
            workflow_type=workflow_type,
        )

    def _extract_situational_updates(self, *, task_input: str, payload: BriefPayload, workflow_type: str) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        lowered = task_input.lower()
        payload_text = " ".join(
            [payload.answer.summary]
            + [item for section in payload.answer.sections for item in section.items]
        ).lower()
        if len(task_input.split()) < 8 and any(word in lowered for word in ("now", "today", "quick", "fast")):
            updates["operating_mode"] = "reactive"
        elif any(word in lowered for word in ("plan", "strategy", "next quarter", "roadmap", "next week")):
            updates["operating_mode"] = "strategic"
        elif workflow_type in {"morning_brief", "meeting_prep"}:
            updates["operating_mode"] = "execution"
        elif any(marker in payload_text for marker in ("by end of day", "before", "deadline", "owner:")):
            updates["operating_mode"] = "execution"

        if any(word in f"{lowered} {payload_text}" for word in ("board", "investor", "deadline", "renewal", "covenant")):
            if "board" in lowered or "board" in payload_text:
                updates["add_pressure"] = f"Board-related deadline raised {datetime.now().strftime('%b %d')}"
            elif "investor" in lowered or "investor" in payload_text:
                updates["add_pressure"] = f"Investor-related deadline raised {datetime.now().strftime('%b %d')}"
            elif "renewal" in payload_text:
                updates["add_pressure"] = f"Renewal risk active {datetime.now().strftime('%b %d')}"

        for topic in ("aws", "burn", "runway", "variance", "forecast", "board", "apex", "redwood", "kepler", "northstar"):
            if topic in lowered or topic in payload_text:
                updates["topic_mention"] = topic
                break

        obligation_item = next(
            (
                item for section in payload.answer.sections for item in section.items
                if any(marker in item.lower() for marker in ("today by", "tomorrow", "before ", "owner:", "follow-up", "follow up"))
            ),
            None,
        )
        if obligation_item:
            updates.setdefault("add_obligation", obligation_item[:140])
        return updates
        if len(cleaned) == 1:
            return f"{cleaned[0]}."
        return f"{', then '.join(cleaned[:2])}."

    def _compute_free_slots(
        self,
        upcoming_events: List[Dict[str, Any]],
        *,
        slot_lengths: List[int],
        reference_dates: List[date] | None = None,
        max_per_day: int | None = None,
    ) -> List[tuple[datetime, datetime]]:
        if not reference_dates:
            reference_dates = [datetime.now().astimezone().date()]

        tz = datetime.now().astimezone().tzinfo
        free_slots: List[tuple[datetime, datetime]] = []
        action_index = 0

        for ref_date in reference_dates:
            if action_index >= len(slot_lengths):
                break
            day_start = datetime(ref_date.year, ref_date.month, ref_date.day, 8, 30, 0, 0, tzinfo=tz)
            day_end = datetime(ref_date.year, ref_date.month, ref_date.day, 17, 0, 0, 0, tzinfo=tz)
            busy_intervals = self._normalize_busy_intervals(upcoming_events, day_start, day_end)
            cursor = day_start
            slots_this_day = 0
            while action_index < len(slot_lengths):
                if max_per_day is not None and slots_this_day >= max_per_day:
                    break
                duration = timedelta(minutes=slot_lengths[action_index])
                slot = self._next_free_slot(cursor, duration, busy_intervals, day_end)
                if not slot:
                    break
                free_slots.append(slot)
                cursor = slot[1]
                action_index += 1
                slots_this_day += 1

        return free_slots

    def _deadline_urgency(self, deadline: str) -> int:
        lowered = deadline.lower()
        parsed_date = self._parse_deadline_date(deadline)
        if parsed_date:
            days_until = (parsed_date - datetime.now().date()).days
            if days_until <= 0:
                return 96
            if days_until == 1:
                return 92
            if days_until <= 3:
                return 88
            if days_until <= 7:
                return 80
            if days_until <= 14:
                return 70
            return 58
        if "today" in lowered or "eod" in lowered or "end of day" in lowered:
            return 96
        if "tomorrow" in lowered:
            return 90
        if "this week" in lowered:
            return 82
        if "next week" in lowered:
            return 68
        return 74

    def _meeting_prep_urgency(self, event: Dict[str, Any]) -> int:
        start = self._parse_datetime(event.get("starts_at"))
        if not start:
            return 58
        hours_until = max((start.astimezone() - datetime.now().astimezone()).total_seconds() / 3600, 0)
        if hours_until <= 2:
            return 88
        if hours_until <= 6:
            return 76
        if hours_until <= 12:
            return 66
        return 54

    def _normalize_busy_intervals(
        self,
        upcoming_events: List[Dict[str, Any]],
        day_start: datetime,
        day_end: datetime,
    ) -> List[tuple[datetime, datetime]]:
        intervals: List[tuple[datetime, datetime]] = []
        for event in upcoming_events:
            start = self._parse_datetime(event.get("starts_at"))
            end = self._parse_datetime(event.get("ends_at"))
            if not start:
                continue
            if not end or end <= start:
                end = start + timedelta(minutes=30)
            start_local = start.astimezone(day_start.tzinfo)
            end_local = end.astimezone(day_start.tzinfo)
            if end_local <= day_start or start_local >= day_end:
                continue
            intervals.append((max(start_local, day_start), min(end_local, day_end)))
        intervals.sort(key=lambda item: item[0])
        return intervals

    def _next_free_slot(
        self,
        cursor: datetime,
        duration: timedelta,
        busy_intervals: List[tuple[datetime, datetime]],
        day_end: datetime,
    ) -> tuple[datetime, datetime] | None:
        slot_start = cursor
        while slot_start + duration <= day_end:
            overlapping = next(
                (
                    (busy_start, busy_end)
                    for busy_start, busy_end in busy_intervals
                    if slot_start < busy_end and slot_start + duration > busy_start
                ),
                None,
            )
            if not overlapping:
                return slot_start, slot_start + duration
            slot_start = overlapping[1]
        return None

    def _parse_datetime(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            normalized = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            return parsed if parsed.tzinfo else parsed.astimezone()
        except ValueError:
            return None

    def _parse_deadline_date(self, value: str) -> date | None:
        text = str(value or "").strip()
        if not text:
            return None
        iso_match = re.search(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", text)
        if iso_match:
            try:
                return date.fromisoformat(iso_match.group(1))
            except ValueError:
                return None
        lowered = text.lower()
        today = datetime.now().date()
        if "today" in lowered or "eod" in lowered or "end of day" in lowered:
            return today
        if "tomorrow" in lowered:
            return today + timedelta(days=1)
        if "this week" in lowered:
            return today + timedelta(days=max(0, 4 - today.weekday()))
        if "next week" in lowered:
            return today + timedelta(days=(7 - today.weekday()) + 4)

        weekday_map = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        for name, weekday_index in weekday_map.items():
            if name in lowered:
                days_ahead = (weekday_index - today.weekday()) % 7
                if "next" in lowered and days_ahead == 0:
                    days_ahead = 7
                elif days_ahead == 0:
                    days_ahead = 7
                return today + timedelta(days=days_ahead)

        cleaned = text.replace(",", " ").replace(".", " ").replace("  ", " ").strip()
        month_map = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        month_day_match = re.search(
            r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})\b",
            cleaned,
            re.IGNORECASE,
        )
        if month_day_match:
            try:
                month = month_map[month_day_match.group(1)[:3].lower()]
                day = int(month_day_match.group(2))
                year = today.year
                candidate = date(year, month, day)
                if candidate < today:
                    candidate = date(year + 1, month, day)
                return candidate
            except ValueError:
                return None

        slash_match = re.search(r"\b(\d{1,2})/(\d{1,2})\b", cleaned)
        if slash_match:
            try:
                month = int(slash_match.group(1))
                day = int(slash_match.group(2))
                candidate = date(today.year, month, day)
                if candidate < today:
                    candidate = date(today.year + 1, month, day)
                return candidate
            except ValueError:
                return None

        return None

    def _format_time_range(self, start: datetime, end: datetime) -> str:
        return f"{start.strftime('%-I:%M')}-{end.strftime('%-I:%M %p')}"

    def _format_schedule_time(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "the scheduled time"
        try:
            normalized = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            return parsed.astimezone().strftime("%-I:%M %p")
        except ValueError:
            return text

    def _build_sources(
        self,
        *,
        workflow_type: str,
        signals: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        retrieved: List[Dict[str, Any]],
        ranked_threads: List[Dict[str, Any]],
        related_threads: List[Dict[str, Any]],
        upcoming_events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if workflow_type in {"email_ingestion", "calendar_briefing", "morning_brief", "schedule_planning"}:
            sources: List[Dict[str, Any]] = []
            for index, thread in enumerate([thread for thread in ranked_threads if not thread.get("suppressed")][:4]):
                source: Dict[str, Any] = {
                    "source_id": f"email_thread_{index}",
                    "title": thread.get("subject", "Inbox Thread"),
                    "type": "artifact",
                    "snippet": "; ".join(thread.get("importance_reasons", [])[:2]) or thread.get("snippet"),
                    "role": "inbox_signal",
                    "relevance_reason": "Used to identify executive-relevant threads and likely asks.",
                    "used_for": ["priorities", "follow_ups"],
                    "confidence_impact": "medium",
                }
                if thread.get("thread_id"):
                    source["thread_id"] = thread["thread_id"]
                sources.append(source)
            for index, event in enumerate(upcoming_events[:4]):
                sources.append(
                    {
                        "source_id": f"calendar_event_{index}",
                        "title": event.get("title", "Upcoming Meeting"),
                        "type": "artifact",
                        "snippet": event.get("starts_at"),
                        "role": "calendar_context",
                        "relevance_reason": "Used to anchor timing and meeting-related preparation.",
                        "used_for": ["priorities", "weekly_plan"],
                        "confidence_impact": "high",
                    }
                )
            for index, thread in enumerate(related_threads[:3]):
                sources.append(
                    {
                        "source_id": f"calendar_thread_{index}",
                        "title": thread.get("subject", "Related Thread"),
                        "type": "artifact",
                        "snippet": "; ".join(thread.get("importance_reasons", [])[:2]) or thread.get("snippet"),
                        "role": "meeting_context",
                        "relevance_reason": "Provides surrounding discussion relevant to the meeting or brief.",
                        "used_for": ["risks", "follow_ups"],
                        "confidence_impact": "medium",
                    }
                )
            if not sources:
                sources.extend(
                    {
                        "source_id": f"signal_{index}",
                        "title": signal.get("subject", "Signal"),
                        "type": "artifact",
                        "role": "fallback_signal",
                        "relevance_reason": "Used because richer event evidence was not available.",
                        "used_for": ["summary"],
                        "confidence_impact": "low",
                    }
                    for index, signal in enumerate(signals[:3])
                )
            return sources

        return [
            {
                "source_id": "company_state",
                "title": "Company State",
                "type": "state",
                "role": "operating_context",
                "relevance_reason": "Provides the baseline company context for the briefing.",
                "used_for": ["summary", "priorities"],
                "confidence_impact": "high",
            },
            *[
                {
                    "source_id": f"signal_{index}",
                    "title": signal.get("subject", "Signal"),
                    "type": "artifact",
                    "role": "recent_signal",
                    "relevance_reason": "Adds recent operating signal context.",
                    "used_for": ["priorities"],
                    "confidence_impact": "medium",
                }
                for index, signal in enumerate(signals[:3])
            ],
            *[
                {
                    "source_id": f"history_{index}",
                    "title": f"Recent Interaction {index + 1}",
                    "type": "artifact",
                    "role": "conversation_history",
                    "relevance_reason": "Adds short-term memory from recent assistant interactions.",
                    "used_for": ["summary"],
                    "confidence_impact": "low",
                }
                for index, _ in enumerate(history[:2])
            ],
            *[
                {
                    "source_id": f"retrieval_{index}",
                    "title": doc.get("title", "Retrieved Document"),
                    "type": "document",
                    "role": "reference_document",
                    "relevance_reason": "Supplies supporting company material for the briefing.",
                    "used_for": ["risks", "follow_ups"],
                    "confidence_impact": "medium",
                }
                for index, doc in enumerate(retrieved[:2])
            ],
        ]

    def _presentation_section(self, section: BriefSection) -> PresentationSection:
        return PresentationSection(title=section.label, content=section.content, items=section.items)

    def _filter_events_by_horizon(
        self,
        upcoming_events: List[Dict[str, Any]],
        event_payload: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Filter upcoming_events to the requested time horizon.

        Reads time_horizon from planning_context (set by the router) and derives
        a start/end date window.  Falls back to a 7-day rolling window when no
        horizon is available so we never dump the entire multi-week feed.
        """
        if not self._plan_execution(event_payload) and not (event_payload.get("planning_context") or {}):
            return upcoming_events

        planning_window = self._resolved_planning_window(event_payload)
        start_raw = planning_window.get("start_date")
        end_raw = planning_window.get("end_date")
        if not start_raw or not end_raw:
            today = date.today()
            start, end = today, today + timedelta(days=6)
        else:
            start = date.fromisoformat(str(start_raw))
            end = date.fromisoformat(str(end_raw))

        filtered: List[Dict[str, Any]] = []
        for ev in upcoming_events:
            starts_at = self._parse_datetime(ev.get("starts_at"))
            if not starts_at:
                continue
            ev_date = starts_at.date()
            if start <= ev_date <= end:
                filtered.append(ev)
        return filtered

    def _next_meetings_after_window(
        self,
        upcoming_events: List[Dict[str, Any]],
        event_payload: Dict[str, Any],
        *,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        planning_window = self._resolved_planning_window(event_payload)
        end_raw = planning_window.get("end_date")
        if not end_raw:
            return []
        try:
            window_end = date.fromisoformat(str(end_raw))
        except ValueError:
            return []
        future_events: List[tuple[datetime, Dict[str, Any]]] = []
        for event in upcoming_events:
            starts_at = self._parse_datetime(event.get("starts_at"))
            if not starts_at:
                continue
            if starts_at.date() <= window_end:
                continue
            future_events.append((starts_at, event))
        if not future_events:
            return []
        future_events.sort(key=lambda item: item[0])
        return [event for _, event in future_events[:limit]]

    def _format_next_meeting(self, event: Dict[str, Any]) -> str:
        title = str(event.get("title") or "Upcoming meeting").strip()
        starts_at = self._fmt_event_time(event.get("starts_at")) or str(event.get("starts_at") or "scheduled")
        return f"Next meeting: {title} • {starts_at}"

    def _filter_text_items_by_window(
        self,
        items: List[str],
        *,
        event_payload: Dict[str, Any],
    ) -> List[str]:
        if not self._plan_execution(event_payload) and not (event_payload.get("planning_context") or {}):
            return [str(item).strip() for item in items if str(item).strip()]

        planning_window = self._resolved_planning_window(event_payload)
        start_raw = planning_window.get("start_date")
        end_raw = planning_window.get("end_date")
        if not start_raw or not end_raw:
            return [str(item).strip() for item in items if str(item).strip()]

        start = date.fromisoformat(str(start_raw))
        end = date.fromisoformat(str(end_raw))
        filtered: List[str] = []
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            parsed_date = self._parse_deadline_date(text)
            if parsed_date is None or start <= parsed_date <= end:
                filtered.append(text)
        return filtered

    def _normalized_structured_watch(self, event_payload: Dict[str, Any]) -> Dict[str, Any]:
        return normalize_structured_watch(
            dict(event_payload.get("structured_watch", {}) or {}),
            upcoming_events=list(event_payload.get("upcoming_events", []) or []),
            reference_dt=datetime.now().astimezone(),
        )

    def _filter_structured_watch_by_window(
        self,
        structured_watch: Dict[str, Any],
        *,
        event_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        planning_window = self._resolved_planning_window(event_payload)
        start_raw = planning_window.get("start_date")
        end_raw = planning_window.get("end_date")
        normalized = normalize_structured_watch(
            structured_watch,
            upcoming_events=list(event_payload.get("upcoming_events", []) or []),
            reference_dt=datetime.now().astimezone(),
        )
        if not start_raw or not end_raw:
            return normalized
        return filter_structured_watch_for_window(
            normalized,
            planning_window=PlanningWindow(
                horizon=planning_window.get("horizon", "unspecified"),
                start_date=date.fromisoformat(str(start_raw)),
                end_date=date.fromisoformat(str(end_raw)),
                timezone=planning_window.get("timezone") or str(datetime.now().astimezone().tzinfo or "UTC"),
                workday_start=planning_window.get("workday_start") or "08:30",
                workday_end=planning_window.get("workday_end") or "17:00",
                target_date=date.fromisoformat(str(planning_window.get("target_date"))) if planning_window.get("target_date") else None,
                target_label=planning_window.get("target_label"),
            ),
        )

    def _timing_to_confirm_items(
        self,
        structured_watch: Dict[str, Any],
        *,
        event_payload: Dict[str, Any],
    ) -> List[str]:
        if not self._plan_execution(event_payload) and not (event_payload.get("planning_context") or {}):
            return []
        items = []
        for item in unresolved_action_items(structured_watch):
            text = action_item_text(item, kind=str(item.get("kind") or "ask"))
            if text:
                items.append(f"{text} (timing to confirm)")
        return items

    def _structured_calendar(
        self,
        payload: BriefPayload,
        event_payload: Dict[str, Any],
        workflow_type: str,
    ) -> Optional[CalendarPresentation]:
        if workflow_type != "calendar_briefing":
            return None
        raw_events = event_payload.get("upcoming_events") or []
        upcoming_events = self._filter_events_by_horizon(raw_events, event_payload)
        events: List[CalendarEvent] = []
        for ev in upcoming_events[:10]:
            attendees = ev.get("attendees") or []
            events.append(CalendarEvent(
                title=ev.get("title") or "Meeting",
                starts_at=ev.get("starts_at"),
                ends_at=ev.get("ends_at"),
                day_label=ev.get("day_label"),
                attendees=[str(a) for a in attendees[:6]],
                location=ev.get("location"),
                kind="meeting",
            ))
        follow_ups = self._next_actions(
            workflow_type,
            [],
            self._semantic_context_from_threads(workflow_type=workflow_type, ranked_threads=[]),
        )
        return CalendarPresentation(events=events, follow_ups=follow_ups)

    def _structured_weekly_plan(
        self,
        payload: BriefPayload,
        event_payload: Dict[str, Any],
        workflow_type: str,
    ) -> Optional[WeeklyPlanPresentation]:
        if workflow_type != "schedule_planning":
            return None

        plan_execution = self._plan_execution(event_payload)
        sections = {section.label: section for section in payload.answer.sections}
        planning_window = self._resolved_planning_window(event_payload)
        scheduled_candidates = plan_execution.get("scheduled_candidates", []) or []
        blocks = [self._scheduled_candidate_to_weekly_block(candidate, payload) for candidate in scheduled_candidates if isinstance(candidate, dict)]
        if not blocks:
            schedule_items = (sections.get("Schedule Proposal").items if sections.get("Schedule Proposal") else [])[:5]
            ref_dates: List[date] | None = None
            try:
                w_start_raw = planning_window.get("start_date")
                w_end_raw = planning_window.get("end_date")
                if w_start_raw and w_end_raw:
                    w_start = date.fromisoformat(str(w_start_raw))
                    w_end = date.fromisoformat(str(w_end_raw))
                    ref_dates = [
                        w_start + timedelta(days=i)
                        for i in range((w_end - w_start).days + 1)
                        if (w_start + timedelta(days=i)).weekday() < 5
                    ]
            except (ValueError, TypeError):
                ref_dates = None
            blocks = [self._schedule_item_to_weekly_block(item, payload, ref_dates) for item in schedule_items if item]
        raw_events = event_payload.get("upcoming_events", []) or []
        window_filtered_events = self._filter_events_by_horizon(raw_events, event_payload)
        meetings = [
            WeeklyPlanMeeting(
                title=str(event.get("title") or "Upcoming meeting"),
                starts_at=event.get("starts_at"),
                ends_at=event.get("ends_at"),
                attendees=[str(item) for item in (event.get("attendees") or []) if item],
            )
            for event in window_filtered_events[:6]
            if isinstance(event, dict)
        ]
        return WeeklyPlanPresentation(
            planning_window=WeeklyPlanWindow(
                horizon=planning_window.get("horizon"),
                start_date=str(planning_window.get("start_date")) if planning_window.get("start_date") else None,
                end_date=str(planning_window.get("end_date")) if planning_window.get("end_date") else None,
                timezone=planning_window.get("timezone"),
                workday_start=planning_window.get("workday_start"),
                workday_end=planning_window.get("workday_end"),
                span_days=int(planning_window.get("span_days")) if planning_window.get("span_days") is not None else None,
            ) if planning_window else None,
            blocks=blocks,
            deadlines=(sections.get("Deadlines").items if sections.get("Deadlines") else [])[:4],
            meetings=meetings,
            follow_ups=(sections.get("Suggested Follow-Ups").items if sections.get("Suggested Follow-Ups") else [])[:4],
        )

    _WEEKDAY_ABBR = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

    def _schedule_item_to_weekly_block(
        self,
        item: str,
        payload: BriefPayload,
        reference_dates: List[date] | None = None,
    ) -> WeeklyPlanBlock:
        normalized = str(item).strip()
        title = normalized
        time_window = None
        starts_at: str | None = None
        ends_at: str | None = None
        day_label: str | None = None

        # "Mon 9:00-9:30 AM: title" — weekday-prefixed block from multi-day schedule
        weekday_match = re.match(
            r"^\s*([A-Za-z]{3})\s+(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2}\s*(?:AM|PM)):\s*(.+?)\s*$",
            normalized,
            re.IGNORECASE,
        )
        if weekday_match:
            day_abbr = weekday_match.group(1).lower()
            start_str = weekday_match.group(2)
            end_str = weekday_match.group(3)
            title = weekday_match.group(4).rstrip(".")
            time_window = f"{start_str}-{end_str}"
            day_label = weekday_match.group(1).capitalize()
            target_weekday = self._WEEKDAY_ABBR.get(day_abbr)
            if target_weekday is not None and reference_dates:
                ref = next((d for d in reference_dates if d.weekday() == target_weekday), None)
                if ref:
                    try:
                        tz = datetime.now().astimezone().tzinfo
                        h, m = (int(x) for x in start_str.split(":"))
                        starts_at = datetime(ref.year, ref.month, ref.day, h, m, tzinfo=tz).isoformat()
                        end_clean = re.sub(r"\s*(AM|PM)\s*$", "", end_str, flags=re.IGNORECASE).strip()
                        eh, em = (int(x) for x in end_clean.split(":"))
                        ends_at = datetime(ref.year, ref.month, ref.day, eh, em, tzinfo=tz).isoformat()
                    except (ValueError, AttributeError):
                        pass
        else:
            match = re.match(
                r"^\s*(\d{1,2}(?::\d{2})?\s*-\s*\d{1,2}:\d{2}\s*(?:AM|PM)):\s*(.+?)\s*$",
                normalized,
                re.IGNORECASE,
            )
            if match:
                time_window = match.group(1)
                title = match.group(2).rstrip(".")
            elif re.match(r"^\s*\d{1,2}(?::\d{2})?\s*-\s*\d{1,2}:\d{2}\s*(?:AM|PM)\s*:", normalized, re.IGNORECASE):
                prefix, suffix = normalized.split(":", 1)
                time_window = prefix.strip()
                title = suffix.strip().rstrip(".") or normalized

        return WeeklyPlanBlock(
            title=self._compact_schedule_text(title),
            time_window=time_window,
            starts_at=starts_at,
            ends_at=ends_at,
            day_label=day_label,
            reason=self._schedule_reason_for_title(title),
            confidence=payload.trust.confidence,
            source_refs=[source.get("source_id", "") for source in payload.sources[:2] if source.get("source_id")],
        )

    def _schedule_reason_for_title(self, title: str) -> str:
        lowered = self._compact_schedule_text(title).lower()
        if "prep for" in lowered:
            return "Prep before live meeting."
        if "handle ask" in lowered:
            return "Resolve a high-priority ask."
        if "work " in lowered:
            return "Protect a live deadline."
        if "outline" in lowered or "board packet" in lowered:
            return "Move a required document forward."
        return "Keep the day focused on active priorities."

    def _apply_presentation_metadata(
        self,
        payload: BriefPayload,
        *,
        event_payload: Dict[str, Any],
        workflow_type: str,
        ceo_id: str = "",
        resolved_clarifications: dict[str, str] | None = None,
    ) -> BriefPayload:
        sections = payload.answer.sections
        n = len(sections)
        if workflow_type == "schedule_planning":
            weekly_plan = self._structured_weekly_plan(payload, event_payload, workflow_type)
            block_count = len(weekly_plan.blocks) if weekly_plan else 0
            meeting_count = len(weekly_plan.meetings) if weekly_plan else 0
            horizon = (weekly_plan.planning_window.horizon or "").replace("_", " ") if weekly_plan and weekly_plan.planning_window else ""
            preamble_parts = []
            if horizon:
                preamble_parts.append(f"Put together the {horizon} from your calendar, open tasks, and deadline signals.")
            else:
                preamble_parts.append("Built this from your calendar, open tasks, and deadline signals.")
            detail_parts = []
            if block_count:
                detail_parts.append(f"{block_count} focused block{'s' if block_count != 1 else ''}")
            if meeting_count:
                detail_parts.append(f"{meeting_count} meeting{'s' if meeting_count != 1 else ''} on the books")
            if detail_parts:
                preamble_parts.append(" and ".join(detail_parts) + ".")
            preamble = " ".join(preamble_parts)
            payload.presentation = BriefPresentation(
                mode=self._presentation_mode(workflow_type),
                variant=self._presentation_variant(
                    workflow_type,
                    ceo_id=ceo_id,
                    event_payload=event_payload,
                    resolved_clarifications=resolved_clarifications,
                ),
                preamble=preamble,
                summary=payload.answer.summary,
                priorities=[],
                recommended_actions=[],
                risks=[],
                details=[],
                weekly_plan=weekly_plan,
                calendar=self._structured_calendar(payload, event_payload, workflow_type),
            )
            return payload
        # Section layout (by position):
        #   s[0]      → priorities
        #   s[1]      → risks         (only when a distinct second section exists)
        #   s[2:-1]   → details       (middle sections, only when 4+)
        #   s[-1]     → recommended_actions (only when distinct from s[0])
        payload.presentation = BriefPresentation(
            mode=self._presentation_mode(workflow_type),
            variant=self._presentation_variant(
                workflow_type,
                ceo_id=ceo_id,
                event_payload=event_payload,
                resolved_clarifications=resolved_clarifications,
            ),
            summary=payload.answer.summary,
            priorities=[self._presentation_section(sections[0])] if n >= 1 else [],
            recommended_actions=[self._presentation_section(sections[-1])] if n >= 2 else [],
            risks=[self._presentation_section(sections[1])] if n >= 3 else [],
            details=[self._presentation_section(s) for s in sections[2:-1]] if n >= 4 else [],
            weekly_plan=self._structured_weekly_plan(payload, event_payload, workflow_type),
            calendar=self._structured_calendar(payload, event_payload, workflow_type),
        )
        return payload

    def _apply_morning_brief_meeting_fallback(
        self,
        payload: BriefPayload,
        *,
        workflow_type: str,
        event_payload: Dict[str, Any],
    ) -> BriefPayload:
        if workflow_type != "morning_brief":
            return payload

        raw_events = event_payload.get("live_events") or event_payload.get("upcoming_events", []) or []
        meetings_in_window = [
            f"{event.get('title', 'Upcoming meeting')} • {event.get('starts_at', 'scheduled')}"
            for event in self._filter_events_by_horizon(raw_events, event_payload)[:4]
        ]
        meeting_items = list(meetings_in_window)
        if not meeting_items:
            next_meetings = self._next_meetings_after_window(raw_events, event_payload, limit=3)
            if not next_meetings:
                return payload
            meeting_items = [
                "No meetings in today's window. Next up outside that window:",
                *[self._format_next_meeting(event) for event in next_meetings],
            ]
            important_threads = next(
                (section for section in payload.answer.sections if section.label == "Important Threads"),
                None,
            )
            thread_count = len(important_threads.items) if important_threads else 0
            payload.answer.summary = (
                f"{thread_count} important threads and {len(next_meetings)} upcoming meetings outside today's window need attention for today."
            )

        updated_sections: List[BriefSection] = []
        replaced = False
        for section in payload.answer.sections:
            if section.label == "Upcoming Meetings":
                updated_sections.append(BriefSection(label=section.label, content=section.content, items=meeting_items[:4]))
                replaced = True
            else:
                updated_sections.append(section)
        if not replaced:
            updated_sections.append(BriefSection(label="Upcoming Meetings", items=meeting_items[:4]))
        payload.answer.sections = updated_sections
        return payload

    def _build_presentation_spec(
        self,
        *,
        payload: BriefPayload,
        workflow_type: str,
        task_input: str,
        ceo_id: str,
        resolved_clarifications: dict[str, str] | None = None,
    ) -> PresentationSpec:
        lowered = task_input.lower()
        artifact_kind = "brief"
        if workflow_type == "schedule_planning":
            artifact_kind = "report"
        audience = "board" if "board" in lowered else "ceo"
        intent = "plan" if workflow_type == "schedule_planning" else "inform"
        blocks: list[PresentationBlock] = []
        for index, section in enumerate(payload.answer.sections):
            label_lower = section.label.lower()
            kind = "analysis"
            if "priority" in label_lower or "top line" in label_lower:
                kind = "headline"
            elif "risk" in label_lower or "gap" in label_lower:
                kind = "risks"
            elif "action" in label_lower or "next step" in label_lower:
                kind = "actions"
            blocks.append(
                PresentationBlock(
                    kind=kind,  # type: ignore[arg-type]
                    title=section.label,
                    summary=section.content,
                    bullets=[str(item) for item in section.items[:5]],
                    priority=index,
                )
            )
        return PresentationSpec(
            artifact_kind=artifact_kind,  # type: ignore[arg-type]
            audience=audience,  # type: ignore[arg-type]
            intent=intent,  # type: ignore[arg-type]
            title=payload.answer.title,
            executive_summary=payload.answer.summary,
            recommendation=(payload.answer.sections[-1].items[0] if payload.answer.sections and payload.answer.sections[-1].items else None),
            variant=self._presentation_variant(
                workflow_type,
                ceo_id=ceo_id,
                event_payload=None,
                resolved_clarifications=resolved_clarifications,
            ),
            assumptions=[str(item) for item in payload.trust.assumptions[:3]],
            sensitivities=[str(item) for item in payload.trust.missing_context[:3]],
            blocks=blocks,
            metadata={
                "workflow_type": workflow_type,
                **(
                    {"debug_source_version": BRIEFING_AGENT_SOURCE_VERSION}
                    if workflow_type == "morning_brief"
                    else {}
                ),
            },
        )

    def _scheduled_candidate_to_weekly_block(self, scheduled_candidate: Dict[str, Any], payload: BriefPayload) -> WeeklyPlanBlock:
        candidate = scheduled_candidate.get("candidate", {}) if isinstance(scheduled_candidate.get("candidate"), dict) else {}
        slot = scheduled_candidate.get("slot", {}) if isinstance(scheduled_candidate.get("slot"), dict) else {}
        starts_at = slot.get("starts_at")
        ends_at = slot.get("ends_at")
        day_label = None
        if starts_at and (parsed_start := self._parse_datetime(starts_at)):
            day_label = parsed_start.astimezone().strftime("%a %b %d")
        constraints = [str(item) for item in (candidate.get("constraints") or []) if item]
        return WeeklyPlanBlock(
            title=str(candidate.get("title") or slot.get("label") or "Planned block"),
            kind=self._schedule_block_kind(constraints),
            starts_at=starts_at,
            ends_at=ends_at,
            day_label=day_label,
            time_window=slot.get("label"),
            reason=str(candidate.get("content") or ""),
            source_refs=[str(item) for item in (candidate.get("source_refs") or []) if item],
            confidence=payload.trust.confidence,
        )

    def _schedule_block_kind(self, constraints: List[str]) -> str:
        if "meeting_prep" in constraints:
            return "meeting_prep"
        if "deadline" in constraints:
            return "deadline"
        if "document_prep" in constraints:
            return "document_prep"
        if "ask" in constraints:
            return "follow_up"
        return "focus"

    def _presentation_mode(self, workflow_type: str) -> str:
        return self._workflow_profile(workflow_type).presentation_mode

    def _presentation_variant(
        self,
        workflow_type: str,
        *,
        ceo_id: str,
        event_payload: Dict[str, Any] | None = None,
        resolved_clarifications: dict[str, str] | None = None,
    ) -> Optional[str]:
        from src.core.database import get_learned_preference

        resolved_style = str((resolved_clarifications or {}).get("presentation_style") or "").strip()
        if resolved_style in {"list_form", "narrative_recap", "timeline"}:
            return resolved_style

        if ceo_id:
            learned_style = get_learned_preference(ceo_id, "presentation_style")
            if learned_style in {"list_form", "narrative_recap", "timeline"}:
                return learned_style

        if workflow_type == "schedule_planning":
            planning_context = event_payload.get("planning_context") if isinstance(event_payload, dict) else {}
            horizon = str((planning_context or {}).get("time_horizon") or "").strip().lower()
            if horizon in {"this_week", "week", "weekly"}:
                return "week_timeline"
            return "timeline"
        return self._workflow_profile(workflow_type).presentation_variant

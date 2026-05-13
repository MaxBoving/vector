from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_serializer


PlanMode = Literal["direct_workflow", "compound_plan"]
PlanningTimeHorizon = Literal["today", "tomorrow", "this_week", "next_week", "week_after_next", "unspecified"]
PlanStepStatus = Literal["pending", "completed", "skipped"]
ActionItemKind = Literal["ask", "deadline"]
ActionItemInferenceKind = Literal["explicit", "derived_relative_time", "derived_from_event", "unresolved"]
RetrievalSourceKind = Literal["email", "calendar", "documents", "signals", "session_history", "crm", "company_state", "project_context"]


class PlannedSubtask(BaseModel):
    key: str
    kind: str
    description: str
    context_sources: List[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    key: str
    kind: str
    description: str
    context_sources: List[str] = Field(default_factory=list)


class PlanExecutionStepResult(BaseModel):
    key: str
    status: PlanStepStatus = "pending"
    details: Dict[str, Any] = Field(default_factory=dict)


class RetrievalSourceRequest(BaseModel):
    source: RetrievalSourceKind
    required: bool = True
    priority: int = 0
    rationale: str = ""


class RetrievalPlan(BaseModel):
    sources: List[RetrievalSourceRequest] = Field(default_factory=list)
    time_horizon: PlanningTimeHorizon = "unspecified"
    target_date: date | None = None
    target_label: str | None = None
    rationale: str = ""
    planner_version: str = ""
    execution_model: str = ""

    @field_serializer("target_date")
    def _serialize_target_date(self, value: date | None) -> str | None:
        return value.isoformat() if value else None

    @property
    def source_names(self) -> List[str]:
        return [source.source for source in self.sources]


class PlanningWindow(BaseModel):
    horizon: PlanningTimeHorizon = "unspecified"
    start_date: date
    end_date: date
    timezone: str
    workday_start: str = "08:30"
    workday_end: str = "17:00"
    target_date: date | None = None
    target_label: str | None = None
    span_days: int | None = None

    @field_serializer("start_date", "end_date", "target_date")
    def _serialize_dates(self, value: date | None) -> str | None:
        return value.isoformat() if value else None


class StructuredWatchActionItem(BaseModel):
    kind: ActionItemKind
    text: str
    due_at: str | None = None
    due_date: date | None = None
    time_window: str | None = None
    related_event_id: str | None = None
    related_event_title: str | None = None
    source_thread_id: str | None = None
    owner: str | None = None
    confidence: float = 0.3
    inference_kind: ActionItemInferenceKind = "unresolved"
    unresolved_reason: str | None = None

    @field_serializer("due_date")
    def _serialize_due_date(self, value: date | None) -> str | None:
        return value.isoformat() if value else None


class BusyInterval(BaseModel):
    starts_at: str
    ends_at: str
    title: str | None = None


class AvailableSlot(BaseModel):
    starts_at: str
    ends_at: str
    label: str


class PlanningCandidate(BaseModel):
    title: str
    content: str
    urgency: int
    duration_minutes: int
    constraints: List[str] = Field(default_factory=list)
    source_refs: List[str] = Field(default_factory=list)
    rationale: str = ""


class ScheduledCandidate(BaseModel):
    candidate: PlanningCandidate
    slot: AvailableSlot


class PlanExecutionResult(BaseModel):
    planning_window: PlanningWindow
    execution_steps: List[PlanExecutionStepResult] = Field(default_factory=list)
    ranked_threads: List[Dict[str, Any]] = Field(default_factory=list)
    structured_watch: Dict[str, Any] = Field(default_factory=dict)
    upcoming_events: List[Dict[str, Any]] = Field(default_factory=list)
    document_context: Dict[str, Any] = Field(default_factory=dict)
    candidates: List[PlanningCandidate] = Field(default_factory=list)
    available_slots: List[AvailableSlot] = Field(default_factory=list)
    scheduled_candidates: List[ScheduledCandidate] = Field(default_factory=list)
    schedule_blocks: List[str] = Field(default_factory=list)
    evidence_summary: Dict[str, Any] = Field(default_factory=dict)
    sparse_guidance: bool = False
    fallback_reasons: List[str] = Field(default_factory=list)


class RequestPlan(BaseModel):
    mode: PlanMode
    target_workflow: str
    direct_workflow: Optional[str] = None
    subtasks: List[PlannedSubtask] = Field(default_factory=list)
    execution_steps: List[PlanStep] = Field(default_factory=list)
    needed_context_sources: List[str] = Field(default_factory=list)
    retrieval_plan: RetrievalPlan = Field(default_factory=RetrievalPlan)
    time_horizon: PlanningTimeHorizon = "unspecified"
    target_date: date | None = None
    target_label: str | None = None
    rationale: str = ""
    planning_metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_serializer("target_date")
    def _serialize_target_date(self, value: date | None) -> str | None:
        return value.isoformat() if value else None

    @property
    def is_compound(self) -> bool:
        return self.mode == "compound_plan"

    @property
    def requested_context_sources(self) -> List[str]:
        if self.retrieval_plan.sources:
            return self.retrieval_plan.source_names
        return list(self.needed_context_sources)

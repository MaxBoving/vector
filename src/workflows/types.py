from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class WorkflowType:
    CONVERSATIONAL = "conversational"
    REPORT_GENERATION = "report_generation"
    DOCUMENT_EXPLANATION = "document_explanation"
    EMAIL_INGESTION = "email_ingestion"
    EMAIL_WATCHER = "email_watcher"
    EMAIL_ACTION = "email_action"
    CALENDAR_BRIEFING = "calendar_briefing"
    CALENDAR_ACTION = "calendar_action"
    MORNING_BRIEF = "morning_brief"
    # Deprecated aliases — kept for _WORKFLOW_REGISTRY backward-compat and
    # read-time normalization of old persisted records only.
    # New writes must use SCHEDULE_PLANNING instead.
    DAY_SCHEDULE_PLANNING = "day_schedule_planning"
    WEEK_SCHEDULE_PLANNING = "week_schedule_planning"
    MEETING_PREP = "meeting_prep"
    WEEKLY_RECAP = "weekly_recap"
    SCHEDULE_PLANNING = "schedule_planning"

DEFAULT_REPORT_GENERATION_STAGES = [
    "planning",
    "synthesizer",
    "complete",
]

DEFAULT_DOCUMENT_EXPLANATION_STAGES = [
    "planning",
    "synthesizer",
    "complete",
]


class WorkflowStepDefinition(BaseModel):
    name: str
    agent_name: Optional[str] = None
    next_steps: List[str] = Field(default_factory=list)
    retry_limit: int = 0
    retry_backoff_seconds: int = 0
    gate_types: List[str] = Field(default_factory=list)
    approval_required: bool = False
    approval_gate_type: Optional[str] = None
    failure_step: Optional[str] = None
    artifact_name: Optional[str] = None
    optional: bool = False
    metadata: Dict[str, str] = Field(default_factory=dict)


class WorkflowDefinition(BaseModel):
    workflow_type: str
    entry_step: str
    steps: List[WorkflowStepDefinition]
    terminal_steps: List[str] = Field(default_factory=list)
    supports_retry: bool = True
    approval_required: bool = False
    metadata: Dict[str, str] = Field(default_factory=dict)

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from src.workflows.semantic_followups import SemanticContext


ResponseMode = Literal["auto", "concise", "report", "explanation"]
WorkflowType = Literal[
    "conversational",
    "report_generation",
    "document_explanation",
    "email_ingestion",
    "email_watcher",
    "email_action",
    "calendar_briefing",
    "calendar_action",
    "morning_brief",
    "schedule_planning",
    "meeting_prep",
    "weekly_recap",
]
ResponseType = Literal["conversational", "report", "explanation", "brief", "schedule", "clarification"]
MessageStatus = Literal["pending", "completed", "failed"]
ApprovalDecision = Literal["approve", "reject"]
ApprovalMode = Literal["draft", "send"]
ConfidenceLevel = Literal["low", "medium", "high"]
DataQuality = Literal["low", "medium", "high"]
SourceType = Literal["document", "state", "artifact"]
EvidenceState = Literal["strong", "mixed", "sparse"]
PresentationMode = Literal["brief", "report", "schedule", "decision", "draft", "finance", "artifact", "media", "calendar", "canvas", "spreadsheet", "clarification"]


class AttachmentRef(BaseModel):
    document_id: str
    filename: str


class AssistantQueryOptions(BaseModel):
    response_mode: ResponseMode = "auto"
    time_range: Optional[str] = None
    include_sources: bool = True


class ClarificationFollowUpContext(BaseModel):
    source_interaction_id: Optional[int] = None
    source_response_type: Optional[ResponseType] = None
    selected_option_label: Optional[str] = None
    selected_option_value: Optional[str] = None
    selected_option_apply_text: Optional[str] = None
    source_context: Optional[str] = None


class AssistantQueryRequest(BaseModel):
    message: str
    conversation_id: str
    project_id: Optional[str] = None
    workflow_hint: Optional[WorkflowType] = None
    attachments: List[AttachmentRef] = Field(default_factory=list)
    options: AssistantQueryOptions = Field(default_factory=AssistantQueryOptions)
    follow_up_context: Optional[ClarificationFollowUpContext] = None


SectionType = Literal["priority", "upcoming", "risk", "action", "detail"]


class AnswerSection(BaseModel):
    label: str
    content: Optional[str] = None
    items: List[str] = Field(default_factory=list)
    section_type: Optional[SectionType] = None


class ChartDataPoint(BaseModel):
    label: str
    value: float


class ChartSpec(BaseModel):
    type: str = "bar"                  # bar | line | pie
    title: str
    subtitle: Optional[str] = None
    data: List[ChartDataPoint]
    value_format: str = "number"       # currency | percent | number
    color_scheme: str = "neutral"      # pipeline | finance | neutral


class FollowUpChip(BaseModel):
    label: str
    prompt: str


class AnswerPayload(BaseModel):
    title: str
    summary: str
    sections: List[AnswerSection] = Field(default_factory=list)
    chart: Optional[ChartSpec] = None
    follow_ups: List[FollowUpChip] = Field(default_factory=list)


class QuestionOption(BaseModel):
    label: str
    value: str
    apply_text: str
    description: Optional[str] = None
    capability_requires: List[str] = Field(default_factory=list)


class QuestionWithOptions(BaseModel):
    question: str
    options: List[QuestionOption] = Field(default_factory=list)
    offer_type: Optional[str] = None  # "action_offer" | "clarification" | None
    priority_score: Optional[float] = None


class TrustMetadata(BaseModel):
    confidence: ConfidenceLevel = "medium"
    confidence_score: float = 0.5
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    data_quality: DataQuality = "medium"
    calculation_used: bool = False
    missing_context: List[str] = Field(default_factory=list)
    evidence_state: Optional[EvidenceState] = None
    evidence_reasons: List[str] = Field(default_factory=list)
    safe_to_act: Optional[bool] = None
    question_options: List[QuestionWithOptions] = Field(default_factory=list)
    semantic_context: Optional[SemanticContext] = None


class SourceRef(BaseModel):
    source_id: str
    title: str
    type: SourceType
    snippet: Optional[str] = None
    role: Optional[str] = None
    relevance_reason: Optional[str] = None
    used_for: List[str] = Field(default_factory=list)
    confidence_impact: Optional[str] = None


class ArtifactRef(BaseModel):
    artifact_type: str
    artifact_id: str
    label: str
    format: Optional[str] = None
    status: Optional[str] = None
    purpose: Optional[str] = None
    ready_when: Optional[str] = None
    blocking_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


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
    confidence: Optional[ConfidenceLevel] = None


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


class DecisionOption(BaseModel):
    label: str
    decision: Optional[ApprovalDecision] = None
    mode: Optional[ApprovalMode] = None
    description: Optional[str] = None


class DecisionPresentation(BaseModel):
    decision_summary: Optional[str] = None
    recommended_option: Optional[str] = None
    impact_if_approved: Optional[str] = None
    impact_if_rejected: Optional[str] = None
    required_by: Optional[str] = None
    options: List[DecisionOption] = Field(default_factory=list)


class DraftPresentation(BaseModel):
    channel: Optional[str] = None
    status: Optional[str] = None
    to: Optional[str] = None
    cc: List[str] = Field(default_factory=list)
    subject: Optional[str] = None
    body: Optional[str] = None
    call_to_action: Optional[str] = None


class FinanceMetricChip(BaseModel):
    label: str
    value: str


class FinanceVisualPresentation(BaseModel):
    title: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None


class FinancePresentation(BaseModel):
    template: Optional[str] = None
    headline: Optional[str] = None
    takeaways: List[str] = Field(default_factory=list)
    implications: List[str] = Field(default_factory=list)
    recommendation: Optional[str] = None
    next_steps: List[str] = Field(default_factory=list)
    threshold_events: List[str] = Field(default_factory=list)
    key_metrics: List[FinanceMetricChip] = Field(default_factory=list)
    primary_visual: Optional[FinanceVisualPresentation] = None


class CalendarEventPresentation(BaseModel):
    title: str
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    day_label: Optional[str] = None
    attendees: List[str] = Field(default_factory=list)
    location: Optional[str] = None
    kind: Optional[str] = None


class CalendarPresentation(BaseModel):
    events: List[CalendarEventPresentation] = Field(default_factory=list)
    follow_ups: List[str] = Field(default_factory=list)


class CanvasHeroMetric(BaseModel):
    label: str
    value: str
    delta: Optional[str] = None


class CanvasSectionPresentation(BaseModel):
    label: str
    bullets: List[str] = Field(default_factory=list)
    content: Optional[str] = None
    highlight: bool = False


class CanvasPresentation(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    hero_metric: Optional[CanvasHeroMetric] = None
    sections: List[CanvasSectionPresentation] = Field(default_factory=list)
    source_credit: Optional[str] = None
    theme_id: Optional[str] = None


class SpreadsheetCell(BaseModel):
    value: Optional[str] = None
    kind: Optional[str] = None
    align: Optional[str] = None


class SpreadsheetColumn(BaseModel):
    key: str
    label: str
    width: Optional[int] = None
    align: Optional[str] = None


class SpreadsheetRow(BaseModel):
    label: Optional[str] = None
    cells: List[SpreadsheetCell] = Field(default_factory=list)


class SpreadsheetPresentation(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    columns: List[SpreadsheetColumn] = Field(default_factory=list)
    rows: List[SpreadsheetRow] = Field(default_factory=list)
    frozen_columns: int = 0
    total_rows: Optional[int] = None
    source_artifact_id: Optional[str] = None


class MessagePresentation(BaseModel):
    mode: Optional[PresentationMode] = None
    variant: Optional[str] = None
    preamble: Optional[str] = None
    summary: Optional[str] = None
    priorities: List[PresentationSection] = Field(default_factory=list)
    recommended_actions: List[PresentationSection] = Field(default_factory=list)
    risks: List[PresentationSection] = Field(default_factory=list)
    details: List[PresentationSection] = Field(default_factory=list)
    weekly_plan: Optional[WeeklyPlanPresentation] = None
    decision: Optional[DecisionPresentation] = None
    draft: Optional[DraftPresentation] = None
    finance: Optional[FinancePresentation] = None
    calendar: Optional[CalendarPresentation] = None
    canvas: Optional[CanvasPresentation] = None
    spreadsheet: Optional[SpreadsheetPresentation] = None


class ArtifactPreviewResponse(BaseModel):
    artifact_id: str
    artifact_type: str
    label: str
    format: Optional[str] = None
    status: Optional[str] = None
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkbookMetricViewResponse(BaseModel):
    label: str
    value: str


class WorkbookTableViewResponse(BaseModel):
    title: str
    columns: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    row_provenance: List[Dict[str, Any]] = Field(default_factory=list)


class WorkbookChartViewResponse(BaseModel):
    title: str
    chart_type: str
    x_axis: str
    y_axis: str
    series_label: str
    source_sheet: Optional[str] = None
    source_table: Optional[str] = None


class WorkbookPivotRowResponse(BaseModel):
    label: str
    value: float


class WorkbookPivotSnapshotResponse(BaseModel):
    title: str
    dimension: str
    measure: str
    rows: List[WorkbookPivotRowResponse] = Field(default_factory=list)


class WorkbookSheetViewResponse(BaseModel):
    name: str
    kind: str
    metrics: List[WorkbookMetricViewResponse] = Field(default_factory=list)
    tables: List[WorkbookTableViewResponse] = Field(default_factory=list)
    charts: List[WorkbookChartViewResponse] = Field(default_factory=list)
    pivot_snapshots: List[WorkbookPivotSnapshotResponse] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkbookViewResponse(BaseModel):
    artifact_id: str
    title: str
    tabs: List[WorkbookSheetViewResponse] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AssistantMessageResponse(BaseModel):
    conversation_id: str
    message_id: str
    workflow_type: WorkflowType
    response_type: ResponseType
    status: MessageStatus
    answer: AnswerPayload
    trust: TrustMetadata
    sources: List[SourceRef] = Field(default_factory=list)
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    presentation: Optional[MessagePresentation] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConversationResponse(BaseModel):
    conversation_id: str
    messages: List[AssistantMessageResponse] = Field(default_factory=list)


class ConversationListItemResponse(BaseModel):
    conversation_id: str
    title: str
    pinned: bool = False
    archived: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: int = 0
    latest_query: Optional[str] = None
    latest_timestamp: Optional[str] = None


class ConversationUpdateRequest(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None


class ProjectCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    document_ids: Optional[List[str]] = None
    conversation_ids: Optional[List[str]] = None


class ProjectResponse(BaseModel):
    project_id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    document_ids: List[str] = Field(default_factory=list)
    conversation_ids: List[str] = Field(default_factory=list)


class ApprovalResolutionRequest(BaseModel):
    decision: ApprovalDecision
    mode: Optional[ApprovalMode] = None
    note: Optional[str] = None
    conversation_id: Optional[str] = None


class DocumentUploadResponse(BaseModel):
    document_id: str
    title: str
    status: str
    intel_summary: Optional[str] = None
    purpose: str = "reference"
    identity_role: Optional[str] = None


class DocumentSummaryResponse(BaseModel):
    document_id: str
    title: str
    status: str = "indexed"
    intel_summary: Optional[str] = None
    domains: List[str] = Field(default_factory=list)
    purpose: str = "reference"
    identity_role: Optional[str] = None


class CompanyIdentityProfileResponse(BaseModel):
    company_name: str
    has_examples: bool = False
    tone: Optional[str] = None
    preferred_formats: List[str] = Field(default_factory=list)
    section_patterns: List[str] = Field(default_factory=list)
    reference_titles: List[str] = Field(default_factory=list)


class CompanyProfileResponse(BaseModel):
    ceo_id: str
    company_name: str
    last_updated: str
    readiness_summary: Dict[str, bool] = Field(default_factory=dict)
    authoritative_coverage_ratio: float = 0.0
    profile_data: Dict[str, Any] = Field(default_factory=dict)


class EmailIngestionRequest(BaseModel):
    sender: Optional[str] = None
    subject: Optional[str] = None
    content: Optional[str] = None
    thread_id: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    received_at: Optional[str] = None



class CalendarBriefingRequest(BaseModel):
    meeting_id: Optional[str] = None
    title: Optional[str] = None
    starts_at: Optional[str] = None
    attendees: List[str] = Field(default_factory=list)
    agenda: Optional[str] = None
    notes: Optional[str] = None


class MorningBriefRequest(BaseModel):
    scheduled_for: str
    timezone: str


class IntegrationStatusResponse(BaseModel):
    provider: str
    service: str
    connected: bool
    account_email: Optional[str] = None
    expires_at: Optional[str] = None


WatcherPreferenceAction = Literal["prioritize_sender", "prioritize_domain", "ignore_sender", "ignore_domain"]


class WatcherPreferenceUpdateRequest(BaseModel):
    action: WatcherPreferenceAction
    sender: Optional[str] = None
    domain: Optional[str] = None


class WatcherPreferenceResponse(BaseModel):
    priority_senders: List[str] = Field(default_factory=list)
    priority_domains: List[str] = Field(default_factory=list)
    ignored_senders: List[str] = Field(default_factory=list)
    ignored_domains: List[str] = Field(default_factory=list)


class IntegrationConnectResponse(BaseModel):
    service: str
    provider: str
    auth_url: str

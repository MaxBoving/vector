from typing import Dict, List, Optional, Any, TypedDict
from enum import Enum
from datetime import datetime
from pydantic import BaseModel
from sqlmodel import Field, SQLModel, Column, JSON

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    hashed_password: str
    ceo_id: str = Field(unique=True)
    company_name: str

class RiskTolerance(str, Enum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"

class TonePreference(str, Enum):
    CONCISE = "concise"
    DETAILED = "detailed"
    ANALYTIC = "analytic"
    DIRECT = "direct"

class CEOPreferences(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ceo_id: str = Field(index=True, unique=True)

    # Tone and Brevity
    preferred_tone: TonePreference = Field(default=TonePreference.CONCISE)
    tone: Optional[str] = Field(default=None)  # plain-string alias for preferred_tone
    max_summary_length: int = Field(default=500)  # chars/words target
    
    # Risk Tolerance by Domain
    risk_profile: Dict[str, str] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )
    
    # Decision Velocity (Low to High 1-10)
    decision_velocity: int = Field(default=5, ge=1, le=10)
    
    # Persistent Agent Traits (STORY-055)
    # Format: {"librarian": {"depth": 80, ...}, "quant": {...}}
    agent_traits: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )
    
    # Meeting Prioritization Behavior
    meeting_logic: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON)
    )

    # Watcher ranking overrides
    priority_senders: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON)
    )
    priority_domains: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON)
    )
    ignored_senders: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON)
    )
    ignored_domains: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON)
    )

    # Executive Blind Spots (STORY-094)
    # Metrics the CEO trusts the LEAST (e.g., ["Sales Pipeline", "Cloud Spend"])
    low_trust_metrics: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON)
    )
    # Adaptive counters (Learning Loop)
    approval_count: int = Field(default=0)
    rejection_count: int = Field(default=0)
    edit_distance_avg: float = Field(default=0.0)

    # Learned behavioral defaults — accumulated from clarification choices.
    # Schema: {"output_format": {"personal_decision": 5, "board_presentation": 2},
    #          "framing": {"operator": 3}, "depth": {"brief": 4, "detailed": 1}}
    # Once any value reaches PREFERENCE_CONFIDENCE_THRESHOLD (3+) and >60% share,
    # the system defaults to it and skips the clarifying question.
    learned_defaults: Dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )

class SessionInteraction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ceo_id: str = Field(index=True)
    query: str
    response: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())
    intent: Optional[str] = None
    status: str = Field(default="COMPLETED") # PENDING, AWAITING_INPUT, COMPLETED, FAILED
    current_stage: Optional[str] = Field(default=None)
    gate_type: Optional[str] = Field(default=None) # DATA_GAP, STRATEGIC_CONFLICT, GOVERNANCE_VIOLATION
    missing_data_context: Optional[str] = Field(default=None, sa_column=Column(JSON))
    
    # Feedback Loop (STORY-089)
    complexity_rating: Optional[int] = Field(default=None) # 0 (too simple) to 10 (too complex)


class AssistantConversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True, unique=True)
    ceo_id: str = Field(index=True)
    title: str = Field(default="New conversation")
    pinned: bool = Field(default=False)
    archived: bool = Field(default=False)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    interaction_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON))


class AssistantProject(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: str = Field(index=True, unique=True)
    ceo_id: str = Field(index=True)
    name: str
    description: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    document_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    conversation_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON))


class WorkflowRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    workflow_id: str = Field(index=True, unique=True)
    interaction_id: Optional[int] = Field(default=None, index=True)
    ceo_id: str = Field(index=True)
    workflow_type: str = Field(index=True)
    status: str = Field(default="PENDING")
    current_stage: Optional[str] = Field(default=None)
    started_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None
    state_data: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    event_log: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    response_data: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    error: Optional[str] = None


class ConnectedAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ceo_id: str = Field(index=True)
    provider: str = Field(index=True)  # google, microsoft
    service: str = Field(index=True)  # gmail, google_calendar, outlook_mail, outlook_calendar
    access_token: str
    refresh_token: Optional[str] = None
    token_type: Optional[str] = None
    expires_at: Optional[str] = None
    account_email: Optional[str] = None
    scopes: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    provider_metadata: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ceo_id: str = Field(index=True)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    
    # Routing Details
    intent: str
    router_model: str
    
    # Specialist Details
    specialist_model: str
    specialist_prompt: str
    specialist_response: str
    ground_truth_checklist: Optional[str] = None
    
    # Audit Details
    audit_report: str
    audit_passed: bool
    
    # Feedback Details (STORY-009)
    approval: Optional[bool] = None
    user_edits: Optional[str] = None
    rejection_reason: Optional[str] = None
    
    # Resource Usage
    total_tokens: int = Field(default=0)
    estimated_cost: float = Field(default=0.0)

class ApprovedDecision(SQLModel, table=True):
    """
    STORY-018: Strategic Decision Ledger.
    Persistent record of board-approved strategies to ensure future consistency.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    ceo_id: str = Field(index=True)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    decision_title: str
    decision_summary: str
    status: str = Field(default="ACTIVE") # ACTIVE, ARCHIVED, REVERSED

class IncomingSignal(SQLModel, table=True):
    """
    STORY-021: Executive Signal Adapter (Emails).
    Stores ingested communication and the Brain's strategic recommendation.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    ceo_id: str = Field(index=True)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    source: str  # Email, Slack, etc.
    sender: str
    subject: str
    content: str
    
    # Brain Analysis
    importance: str = Field(default="LOW") # LOW, MEDIUM, HIGH
    strategic_concepts: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    talking_points: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="UNREAD") # UNREAD, ACTIONED, ARCHIVED

class StrategicInitiative(BaseModel):
    name: str
    owner: str
    status: str
    priority: int

class CompanyState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_name: str = Field(index=True)
    ceo_id: Optional[str] = Field(default=None, index=True)
    last_updated: str  # ISO format

    # Flat financial metrics (for quick access)
    arr: Optional[float] = Field(default=None)
    mrr: Optional[float] = Field(default=None)
    headcount: Optional[int] = Field(default=None)
    burn_monthly: Optional[float] = Field(default=None)
    runway_months: Optional[float] = Field(default=None)

    # Decision Primitives
    revenue_segmentation: Dict[str, float] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )
    cost_structure: Dict[str, float] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )
    capital_position: Dict[str, float] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )
    strategic_initiatives: List[Dict[str, Any]] = Field(
        default_factory=list, 
        sa_column=Column(JSON)
    )
    org_structure: Dict[str, str] = Field(
        default_factory=dict, 
        sa_column=Column(JSON)
    )
    regulatory_footprint: List[str] = Field(
        default_factory=list, 
        sa_column=Column(JSON)
    )
    knowledge_base: List[Dict[str, Any]] = Field(
        default_factory=list, 
        sa_column=Column(JSON)
    ) # [{title: "...", content: "..."}]


class WorldState(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ceo_id: str = Field(index=True, unique=True)
    world_version: str = Field(default="world_sim_v1", index=True)
    simulation_day: str = Field(index=True)
    last_tick_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    snapshot_data: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    mutation_log: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    derived_state: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class CompanyIdentityProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_name: str = Field(index=True, unique=True)
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())
    profile_data: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    example_material_ids: List[str] = Field(default_factory=list, sa_column=Column(JSON))


class CompanyProfileRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ceo_id: str = Field(index=True, unique=True)
    company_name: str = Field(index=True)
    last_updated: str = Field(default_factory=lambda: datetime.now().isoformat())
    profile_data: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    readiness_summary: Dict[str, bool] = Field(default_factory=dict, sa_column=Column(JSON))
    authoritative_coverage_ratio: float = Field(default=0.0)


class ConversationThreadEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    ceo_id: str = Field(index=True)
    turn: int = Field(default=0)
    actor: str
    entry_type: str
    content: str
    structured_payload: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    entities: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="open", index=True)
    parent_entry_id: Optional[int] = None
    resolution_note: Optional[str] = None
    resolved_at: Optional[str] = None
    workflow_type: Optional[str] = None
    interaction_id: Optional[int] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class ConversationLiveContext(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True, unique=True)
    ceo_id: str = Field(index=True)
    turn_count: int = Field(default=0)
    current_schedule: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    open_decisions: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    open_commitments: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    pending_actions: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    entities_in_play: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    last_agent_contributions: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    intent_state: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    unified_memory: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    resolved_clarifications: Dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON))
    clarification_resolutions: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ClarificationSelectedOptionRecord(TypedDict, total=False):
    label: str
    value: str
    apply_text: str
    description: str


class ClarificationResolutionRecord(TypedDict, total=False):
    ceo_id: str
    conversation_id: str
    source_interaction_id: int
    source_response_type: str
    gate_type: str
    question: str
    selected_option: ClarificationSelectedOptionRecord
    signal_type: str
    signal_value: str
    answer_text: str
    match_strategy: str
    recorded_at: str


class CEOSituationalProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ceo_id: str = Field(index=True, unique=True)
    operating_mode: str = Field(default="standard")
    active_pressures: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    recurring_topics: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    open_threads: List[Dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    relationship_obligations: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    inferred_blind_spots: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_by: str = Field(default="system")


class CEOMemory(SQLModel, table=True):
    """
    Persistent long-term memory for a CEO — decisions, commitments, preferences,
    facts, and milestones that should survive across sessions.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    memory_id: str = Field(index=True, unique=True)
    ceo_id: str = Field(index=True)
    memory_type: str = Field(default="fact", index=True)
    # Valid types: decision, commitment, preference, fact, milestone
    title: str
    content: str
    tags: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    source_interaction_id: Optional[int] = Field(default=None)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    expires_at: Optional[str] = Field(default=None)


def normalize_preferences_payload(preferences: CEOPreferences | Dict[str, Any] | None) -> Dict[str, Any]:
    if preferences is None:
        return {}

    raw = preferences if isinstance(preferences, dict) else preferences.model_dump()
    normalized = dict(raw)
    risk_profile = normalized.get("risk_profile", {})
    if isinstance(risk_profile, dict):
        normalized["risk_profile"] = {str(key): str(value) for key, value in risk_profile.items()}
    for field_name in ("priority_senders", "priority_domains", "ignored_senders", "ignored_domains"):
        value = normalized.get(field_name, [])
        if isinstance(value, list):
            normalized[field_name] = [str(item).strip().lower() for item in value if str(item).strip()]
        else:
            normalized[field_name] = []
    return normalized


def normalize_company_state_payload(state: CompanyState | Dict[str, Any] | None) -> Dict[str, Any]:
    if state is None:
        return {}

    raw = state if isinstance(state, dict) else state.model_dump()
    normalized = dict(raw)
    knowledge_base = normalized.get("knowledge_base", [])
    if isinstance(knowledge_base, list):
        normalized["knowledge_base"] = [
            item if isinstance(item, dict) else {"title": str(item), "content": ""}
            for item in knowledge_base
        ]
    return normalized

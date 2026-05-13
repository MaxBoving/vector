from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


EvidenceTier = Literal["authoritative", "derived", "inferred", "unverified"]
FreshnessTier = Literal["live", "daily", "weekly", "monthly", "quarterly", "ad_hoc"]


class SourceMetadata(BaseModel):
    source: str
    owner: str | None = None
    observed_at: str | None = None
    effective_date: str | None = None
    last_verified_at: str | None = None
    freshness: FreshnessTier = "ad_hoc"
    evidence_tier: EvidenceTier = "unverified"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class SourcedValue(BaseModel):
    value: str
    metadata: SourceMetadata


class CompanyIdentity(BaseModel):
    legal_name: str
    operating_name: str | None = None
    industry: str
    business_model: str
    company_stage: str
    headquarters: str | None = None
    primary_geographies: list[str] = Field(default_factory=list)
    fiscal_year_definition: str
    reporting_cadence: list[str] = Field(default_factory=list)
    strategic_priorities: list[str] = Field(default_factory=list)
    key_constraints: list[str] = Field(default_factory=list)
    metadata: SourceMetadata


class ExecutivePreference(BaseModel):
    preferred_tone: str | None = None
    preferred_artifacts: list[str] = Field(default_factory=list)
    decision_focus_areas: list[str] = Field(default_factory=list)
    high_attention_metrics: list[str] = Field(default_factory=list)
    low_trust_metrics: list[str] = Field(default_factory=list)
    preferred_report_length: str | None = None
    metadata: SourceMetadata


class ExecutiveRole(BaseModel):
    name: str
    title: str
    function: str | None = None
    reports_to: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    decision_scope: list[str] = Field(default_factory=list)
    metadata: SourceMetadata


class DecisionRule(BaseModel):
    domain: str
    owner_role: str
    approver_role: str | None = None
    escalation_path: list[str] = Field(default_factory=list)
    approval_threshold: str | None = None
    metadata: SourceMetadata


class KPIRecord(BaseModel):
    name: str
    definition: str
    owner_role: str
    reporting_frequency: str
    source_of_truth: str
    metadata: SourceMetadata


class ActiveProject(BaseModel):
    name: str
    owner_role: str
    status: str
    target_date: str | None = None
    budget_status: str | None = None
    milestones: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    metadata: SourceMetadata


class OpenDecision(BaseModel):
    title: str
    owner_role: str
    due_date: str | None = None
    impact_area: str
    options: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    metadata: SourceMetadata


class TimeBoundEvent(BaseModel):
    title: str
    event_type: Literal["board", "investor", "customer", "hiring", "finance", "operating", "compliance", "other"]
    starts_at: str | None = None
    ends_at: str | None = None
    owner_role: str | None = None
    notes: str | None = None
    metadata: SourceMetadata


class CanonicalMemoryItem(BaseModel):
    category: Literal["product", "customer", "segment", "workflow", "constraint", "term", "ritual", "document"]
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    metadata: SourceMetadata


class SourceSystemRecord(BaseModel):
    system_name: str
    function: str
    source_of_truth: bool = False
    reporting_owner: str | None = None
    export_formats: list[str] = Field(default_factory=list)
    update_frequency: str | None = None
    integration_status: Literal["manual", "file_drop", "planned", "integrated"] = "manual"
    metadata: SourceMetadata


class CompanyProfile(BaseModel):
    schema_version: str = "company_profile_v1"
    company_identity: CompanyIdentity
    executive_preferences: ExecutivePreference | None = None
    executive_team: list[ExecutiveRole] = Field(default_factory=list)
    decision_rules: list[DecisionRule] = Field(default_factory=list)
    kpis: list[KPIRecord] = Field(default_factory=list)
    active_projects: list[ActiveProject] = Field(default_factory=list)
    open_decisions: list[OpenDecision] = Field(default_factory=list)
    time_bound_events: list[TimeBoundEvent] = Field(default_factory=list)
    canonical_memory: list[CanonicalMemoryItem] = Field(default_factory=list)
    source_systems: list[SourceSystemRecord] = Field(default_factory=list)
    onboarding_assumptions: list[str] = Field(default_factory=list)
    onboarding_open_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_minimum_operating_context(self) -> "CompanyProfile":
        if not self.company_identity.strategic_priorities:
            raise ValueError("company_identity.strategic_priorities must include at least one priority")
        if not self.executive_team:
            raise ValueError("executive_team must include at least one executive role")
        if not self.kpis:
            raise ValueError("kpis must include at least one KPI definition")
        return self

    @property
    def authoritative_coverage_ratio(self) -> float:
        metadata_records = [
            self.company_identity.metadata,
            *(record.metadata for record in self.executive_team),
            *(record.metadata for record in self.decision_rules),
            *(record.metadata for record in self.kpis),
            *(record.metadata for record in self.active_projects),
            *(record.metadata for record in self.open_decisions),
            *(record.metadata for record in self.time_bound_events),
            *(record.metadata for record in self.canonical_memory),
            *(record.metadata for record in self.source_systems),
        ]
        if self.executive_preferences:
            metadata_records.append(self.executive_preferences.metadata)
        if not metadata_records:
            return 0.0
        authoritative = [
            item for item in metadata_records if item.evidence_tier in {"authoritative", "derived"}
        ]
        return round(len(authoritative) / len(metadata_records), 3)

    def minimum_readiness_summary(self) -> dict[str, bool]:
        return {
            "has_identity": bool(self.company_identity.legal_name and self.company_identity.business_model),
            "has_executive_team": bool(self.executive_team),
            "has_kpis": bool(self.kpis),
            "has_time_bound_events": bool(self.time_bound_events),
            "has_source_system_map": bool(self.source_systems),
        }

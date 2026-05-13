from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


ArtifactKind = Literal["board_deck", "memo", "brief", "financial_analysis", "report", "canvas"]
AudienceKind = Literal["ceo", "board", "exec_team", "customer", "internal"]
IntentKind = Literal["inform", "decide", "approve", "review", "plan"]
BlockKind = Literal["headline", "context", "analysis", "decision", "risks", "actions", "appendix"]


class ChartIntentKind(str, Enum):
    NONE = "none"
    TREND = "trend"
    COMPARISON = "comparison"
    MIX = "mix"
    VARIANCE = "variance"
    FORECAST = "forecast"
    DISTRIBUTION = "distribution"


class ChartRequest(BaseModel):
    kind: ChartIntentKind = ChartIntentKind.NONE
    title: str
    purpose: str | None = None
    x_axis: str | None = None
    y_axis: str | None = None
    group_by: str | None = None
    series: list[str] = Field(default_factory=list)
    required: bool = False
    rationale: str | None = None


class PresentationBlock(BaseModel):
    kind: BlockKind = "analysis"
    title: str
    summary: str | None = None
    bullets: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    priority: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class PresentationSpec(BaseModel):
    artifact_kind: ArtifactKind = "report"
    audience: AudienceKind = "ceo"
    intent: IntentKind = "inform"
    title: str
    executive_summary: str
    recommendation: str | None = None
    decision_required: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    sensitivities: list[str] = Field(default_factory=list)
    blocks: list[PresentationBlock] = Field(default_factory=list)
    charts: list[ChartRequest] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChartValidationResult(BaseModel):
    supported: bool
    reasons: list[str] = Field(default_factory=list)
    available_series: list[str] = Field(default_factory=list)


class PresentationQualityResult(BaseModel):
    profile: ArtifactKind = "report"
    presentation_ready: bool = True
    score: float = 1.0
    hard_failures: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    repaired: bool = False
    chart_validation: ChartValidationResult | None = None

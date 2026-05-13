from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


MetricCategory = Literal["revenue", "cost", "capital", "budget", "project", "profitability", "general"]
MetricUnit = Literal["currency", "percent", "months", "count", "ratio", "generic"]
MetricSourceType = Literal["company_state", "document", "artifact", "derived", "fallback"]
PeriodGranularity = Literal["week", "month", "quarter", "year", "custom"]


class MetricPeriod(BaseModel):
    label: str
    granularity: PeriodGranularity = "custom"
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_forecast: bool = False


class CanonicalMetric(BaseModel):
    metric_key: str
    metric_label: str
    category: MetricCategory = "general"
    period: MetricPeriod
    value: float
    unit: MetricUnit = "currency"
    source_ref: str = ""
    source_type: MetricSourceType = "derived"
    confidence: float = 0.5
    tags: Dict[str, str] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MetricCollection(BaseModel):
    metrics: List[CanonicalMetric] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def find(self, metric_key: str) -> List[CanonicalMetric]:
        return [metric for metric in self.metrics if metric.metric_key == metric_key]

    def periods(self) -> List[str]:
        return sorted({metric.period.label for metric in self.metrics})

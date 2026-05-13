from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .models import MetricCategory, MetricUnit


class CanonicalMetricDefinition(BaseModel):
    metric_key: str
    metric_label: str
    category: MetricCategory
    unit: MetricUnit
    description: str


CANONICAL_METRIC_DEFINITIONS = [
    CanonicalMetricDefinition(
        metric_key="cash_on_hand",
        metric_label="Cash on Hand",
        category="capital",
        unit="currency",
        description="Current unrestricted cash balance available to operate the business.",
    ),
    CanonicalMetricDefinition(
        metric_key="burn_rate_monthly",
        metric_label="Monthly Burn Rate",
        category="cost",
        unit="currency",
        description="Normalized monthly burn rate.",
    ),
    CanonicalMetricDefinition(
        metric_key="burn_rate_weekly",
        metric_label="Weekly Burn Rate",
        category="cost",
        unit="currency",
        description="Weekly burn rate or net weekly burn.",
    ),
    CanonicalMetricDefinition(
        metric_key="cloud_cost_weekly",
        metric_label="Weekly Cloud Cost",
        category="cost",
        unit="currency",
        description="Weekly cloud or infrastructure spend.",
    ),
    CanonicalMetricDefinition(
        metric_key="cloud_cost_monthly",
        metric_label="Monthly Cloud Cost",
        category="cost",
        unit="currency",
        description="Monthly cloud or infrastructure spend.",
    ),
    CanonicalMetricDefinition(
        metric_key="approved_budget",
        metric_label="Approved Budget",
        category="budget",
        unit="currency",
        description="Approved budget for a team, project, or initiative.",
    ),
    CanonicalMetricDefinition(
        metric_key="committed_spend",
        metric_label="Committed Spend",
        category="project",
        unit="currency",
        description="Committed spend to date for a project or initiative.",
    ),
    CanonicalMetricDefinition(
        metric_key="remaining_budget",
        metric_label="Remaining Budget",
        category="project",
        unit="currency",
        description="Budget remaining after committed spend.",
    ),
    CanonicalMetricDefinition(
        metric_key="forecast_spend",
        metric_label="Forecast Spend",
        category="project",
        unit="currency",
        description="Projected spend over a forward period.",
    ),
    CanonicalMetricDefinition(
        metric_key="forecast_remaining_budget",
        metric_label="Forecast Remaining Budget",
        category="project",
        unit="currency",
        description="Projected remaining budget over a forward period.",
    ),
    CanonicalMetricDefinition(
        metric_key="runway_months",
        metric_label="Runway Months",
        category="capital",
        unit="months",
        description="Estimated runway in months given current cash and burn.",
    ),
    CanonicalMetricDefinition(
        metric_key="revenue",
        metric_label="Revenue",
        category="revenue",
        unit="currency",
        description="Revenue for a defined reporting period.",
    ),
    CanonicalMetricDefinition(
        metric_key="opex",
        metric_label="Operating Expense",
        category="cost",
        unit="currency",
        description="Operating expense for a defined reporting period.",
    ),
]

CANONICAL_METRIC_KEYS = [definition.metric_key for definition in CANONICAL_METRIC_DEFINITIONS]


def find_metric_definition(metric_key: str) -> Optional[CanonicalMetricDefinition]:
    return next((definition for definition in CANONICAL_METRIC_DEFINITIONS if definition.metric_key == metric_key), None)

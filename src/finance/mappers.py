from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .metrics import find_metric_definition
from .models import CanonicalMetric, MetricCollection, MetricPeriod


class MapperContext(BaseModel):
    company_state: Dict[str, Any] = Field(default_factory=dict)
    retrieved_documents: List[Dict[str, Any]] = Field(default_factory=list)
    task_input: str = ""
    project_context: Dict[str, Any] = Field(default_factory=dict)


class FinanceMapper:
    def map(self, context: MapperContext) -> MetricCollection:
        return map_company_context_to_metrics(context)


def _parse_numeric_value(raw_value: str) -> Optional[float]:
    normalized = raw_value.replace("$", "").replace(",", "").strip()
    multiplier = 1.0
    if normalized.endswith("%"):
        normalized = normalized[:-1]
    if normalized.endswith("M"):
        multiplier = 1_000_000.0
        normalized = normalized[:-1]
    elif normalized.endswith("K"):
        multiplier = 1_000.0
        normalized = normalized[:-1]
    try:
        return round(float(normalized) * multiplier, 2)
    except ValueError:
        return None


def _find_document_metric(
    documents: List[Dict[str, Any]],
    *,
    pattern: str,
    metric_key: str,
    period_label: str,
    granularity: str,
    confidence: float,
    tags: Optional[Dict[str, str]] = None,
) -> Optional[CanonicalMetric]:
    return _find_document_metric_any(
        documents,
        patterns=[pattern],
        metric_key=metric_key,
        period_label=period_label,
        granularity=granularity,
        confidence=confidence,
        tags=tags,
    )


def _find_document_metric_any(
    documents: List[Dict[str, Any]],
    *,
    patterns: List[str],
    metric_key: str,
    period_label: str,
    granularity: str,
    confidence: float,
    tags: Optional[Dict[str, str]] = None,
) -> Optional[CanonicalMetric]:
    """Try each pattern in order; return the first match found across all documents."""
    definition = find_metric_definition(metric_key)
    if not definition:
        return None
    regexes = [re.compile(p, re.IGNORECASE) for p in patterns]
    for document in documents:
        content = str(document.get("content", ""))
        for raw_line in content.splitlines():
            line = raw_line.strip(" -\t")
            if not line:
                continue
            for regex in regexes:
                match = regex.search(line)
                if not match:
                    continue
                numeric_value = _parse_numeric_value(match.group(1))
                if numeric_value is None:
                    continue
                return CanonicalMetric(
                    metric_key=definition.metric_key,
                    metric_label=definition.metric_label,
                    category=definition.category,
                    period=MetricPeriod(label=period_label, granularity=granularity),
                    value=numeric_value,
                    unit=definition.unit,
                    source_ref=str(document.get("title", "Retrieved document")),
                    source_type="document",
                    confidence=confidence,
                    tags=tags or {},
                    metadata={"source_excerpt": line[:240]},
                )
    return None


def _state_metric(
    value: Any,
    *,
    metric_key: str,
    period_label: str,
    granularity: str,
    source_ref: str,
    confidence: float,
    tags: Optional[Dict[str, str]] = None,
) -> Optional[CanonicalMetric]:
    """Build a CanonicalMetric from a company_state scalar value."""
    if not isinstance(value, (int, float)):
        try:
            parsed = _parse_numeric_value(str(value))
            if parsed is None:
                return None
            value = parsed
        except Exception:
            return None
    definition = find_metric_definition(metric_key)
    if not definition:
        return None
    return CanonicalMetric(
        metric_key=definition.metric_key,
        metric_label=definition.metric_label,
        category=definition.category,
        period=MetricPeriod(label=period_label, granularity=granularity),
        value=float(value),
        unit=definition.unit,
        source_ref=source_ref,
        source_type="company_state",
        confidence=confidence,
        tags=tags or {},
    )


def _coerce_state_value(section: Dict[str, Any], *keys: str) -> Optional[Any]:
    """Try multiple key variants (CamelCase and snake_case) from a state section."""
    for key in keys:
        val = section.get(key)
        if val is not None:
            return val
    return None


def map_company_context_to_metrics(context: MapperContext) -> MetricCollection:
    metrics: List[CanonicalMetric] = []
    state = context.company_state
    docs = context.retrieved_documents

    # ── Company state: capital_position ───────────────────────────────────────
    capital = state.get("capital_position") or {}
    if isinstance(capital, dict):
        _append_if(metrics, _state_metric(
            _coerce_state_value(capital, "Cash at Bank", "cash_at_bank", "cash_on_hand"),
            metric_key="cash_on_hand", period_label="Current Quarter", granularity="quarter",
            source_ref="CompanyState.capital_position.cash", confidence=0.85,
        ))
        _append_if(metrics, _state_metric(
            _coerce_state_value(capital, "Runway Months", "runway_months"),
            metric_key="runway_months", period_label="Current Quarter", granularity="quarter",
            source_ref="CompanyState.capital_position.runway_months", confidence=0.80,
        ))

    # ── Company state: cost_structure ─────────────────────────────────────────
    costs = state.get("cost_structure") or {}
    if isinstance(costs, dict):
        _append_if(metrics, _state_metric(
            _coerce_state_value(costs, "Burn Rate", "burn_rate", "burn_rate_monthly", "monthly_burn"),
            metric_key="burn_rate_monthly", period_label="Current Month", granularity="month",
            source_ref="CompanyState.cost_structure.burn_rate", confidence=0.82,
            tags={"scope": "cash"},
        ))
        _append_if(metrics, _state_metric(
            _coerce_state_value(costs, "Weekly Burn", "burn_rate_weekly", "weekly_burn"),
            metric_key="burn_rate_weekly", period_label="Current Week", granularity="week",
            source_ref="CompanyState.cost_structure.burn_rate_weekly", confidence=0.80,
            tags={"scope": "cash"},
        ))
        _append_if(metrics, _state_metric(
            _coerce_state_value(costs, "AWS Cost", "cloud_cost_weekly", "aws_cost_weekly", "weekly_cloud_cost"),
            metric_key="cloud_cost_weekly", period_label="Current Week", granularity="week",
            source_ref="CompanyState.cost_structure.cloud_cost_weekly", confidence=0.80,
            tags={"scope": "cloud"},
        ))
        _append_if(metrics, _state_metric(
            _coerce_state_value(costs, "Monthly Cloud Cost", "cloud_cost_monthly", "monthly_cloud_cost"),
            metric_key="cloud_cost_monthly", period_label="Current Month", granularity="month",
            source_ref="CompanyState.cost_structure.cloud_cost_monthly", confidence=0.78,
            tags={"scope": "cloud"},
        ))
        _append_if(metrics, _state_metric(
            _coerce_state_value(costs, "OpEx", "opex", "operating_expense", "total_opex"),
            metric_key="opex", period_label="Current Quarter", granularity="quarter",
            source_ref="CompanyState.cost_structure.opex", confidence=0.78,
        ))

    # ── Company state: revenue_segmentation ───────────────────────────────────
    revenue_seg = state.get("revenue_segmentation") or {}
    if isinstance(revenue_seg, dict):
        # Prefer an explicit total; otherwise sum all numeric segment values
        total_rev = _coerce_state_value(revenue_seg, "Total Revenue", "total_revenue", "revenue", "ARR", "arr")
        if total_rev is None:
            segment_vals = [v for v in revenue_seg.values() if isinstance(v, (int, float))]
            total_rev = sum(segment_vals) if segment_vals else None
        _append_if(metrics, _state_metric(
            total_rev,
            metric_key="revenue", period_label="Current Quarter", granularity="quarter",
            source_ref="CompanyState.revenue_segmentation", confidence=0.78,
        ))

    # ── Documents: project budget / spend (original narrow patterns kept) ─────
    _append_if(metrics, _find_document_metric_any(
        docs,
        patterns=[
            r"FY2026 Approved Budget:\s*\$?([0-9.,]+[MK]?)",
            r"Approved Budget[:\s]+\$?([0-9.,]+[MK]?)",
            r"Total Budget[:\s]+\$?([0-9.,]+[MK]?)",
            r"Budget[:\s]+\$?([0-9.,]+[MK]?)",
        ],
        metric_key="approved_budget", period_label="FY 2026", granularity="year",
        confidence=0.90, tags={"scope": "project"},
    ))
    _append_if(metrics, _find_document_metric_any(
        docs,
        patterns=[
            r"Spend Committed to Date:\s*\$?([0-9.,]+[MK]?)",
            r"Committed Spend[:\s]+\$?([0-9.,]+[MK]?)",
            r"Spend to Date[:\s]+\$?([0-9.,]+[MK]?)",
            r"Actual Spend[:\s]+\$?([0-9.,]+[MK]?)",
        ],
        metric_key="committed_spend", period_label="FY 2026", granularity="year",
        confidence=0.90, tags={"scope": "project"},
    ))
    _append_if(metrics, _find_document_metric_any(
        docs,
        patterns=[
            r"Estimated Remaining Budget:\s*\$?([0-9.,]+[MK]?)",
            r"Remaining Budget[:\s]+\$?([0-9.,]+[MK]?)",
            r"Budget Remaining[:\s]+\$?([0-9.,]+[MK]?)",
        ],
        metric_key="remaining_budget", period_label="FY 2026", granularity="year",
        confidence=0.88, tags={"scope": "project"},
    ))

    # ── Documents: cloud / infrastructure cost ────────────────────────────────
    _append_if(metrics, _find_document_metric_any(
        docs,
        patterns=[
            r"Current Week AWS Spend:\s*\$?([0-9.,]+[MK]?)",
            r"Weekly AWS[:\s]+\$?([0-9.,]+[MK]?)",
            r"AWS Cost[:\s]+\$?([0-9.,]+[MK]?)",
            r"Cloud Cost \(Weekly\)[:\s]+\$?([0-9.,]+[MK]?)",
            r"Weekly Cloud Spend[:\s]+\$?([0-9.,]+[MK]?)",
            r"Infrastructure Cost[:\s]+\$?([0-9.,]+[MK]?)",
        ],
        metric_key="cloud_cost_weekly", period_label="Current Week", granularity="week",
        confidence=0.88, tags={"scope": "cloud"},
    ))
    _append_if(metrics, _find_document_metric_any(
        docs,
        patterns=[
            r"Monthly AWS[:\s]+\$?([0-9.,]+[MK]?)",
            r"AWS Cost \(Monthly\)[:\s]+\$?([0-9.,]+[MK]?)",
            r"Monthly Cloud Spend[:\s]+\$?([0-9.,]+[MK]?)",
            r"Cloud Cost \(Monthly\)[:\s]+\$?([0-9.,]+[MK]?)",
        ],
        metric_key="cloud_cost_monthly", period_label="Current Month", granularity="month",
        confidence=0.85, tags={"scope": "cloud"},
    ))

    # ── Documents: burn rate ──────────────────────────────────────────────────
    _append_if(metrics, _find_document_metric_any(
        docs,
        patterns=[
            r"Normalized Monthly Burn Run-Rate:\s*\$?([0-9.,]+[MK]?)",
            r"Monthly Burn[:\s]+\$?([0-9.,]+[MK]?)",
            r"Burn Rate[:\s]+\$?([0-9.,]+[MK]?)",
            r"Net Burn[:\s]+\$?([0-9.,]+[MK]?)",
            r"Cash Burn[:\s]+\$?([0-9.,]+[MK]?)",
        ],
        metric_key="burn_rate_monthly", period_label="Current Month", granularity="month",
        confidence=0.86, tags={"scope": "cash"},
    ))
    _append_if(metrics, _find_document_metric_any(
        docs,
        patterns=[
            r"Weekly Burn[:\s]+\$?([0-9.,]+[MK]?)",
            r"Weekly Net Burn[:\s]+\$?([0-9.,]+[MK]?)",
        ],
        metric_key="burn_rate_weekly", period_label="Current Week", granularity="week",
        confidence=0.82, tags={"scope": "cash"},
    ))

    # ── Documents: revenue ────────────────────────────────────────────────────
    _append_if(metrics, _find_document_metric_any(
        docs,
        patterns=[
            r"Total Revenue[:\s]+\$?([0-9.,]+[MK]?)",
            r"Revenue[:\s]+\$?([0-9.,]+[MK]?)",
            r"ARR[:\s]+\$?([0-9.,]+[MK]?)",
            r"Annual Recurring Revenue[:\s]+\$?([0-9.,]+[MK]?)",
            r"Net Revenue[:\s]+\$?([0-9.,]+[MK]?)",
            r"Gross Revenue[:\s]+\$?([0-9.,]+[MK]?)",
        ],
        metric_key="revenue", period_label="Current Quarter", granularity="quarter",
        confidence=0.82,
    ))

    # ── Documents: opex ───────────────────────────────────────────────────────
    _append_if(metrics, _find_document_metric_any(
        docs,
        patterns=[
            r"Total OpEx[:\s]+\$?([0-9.,]+[MK]?)",
            r"Operating Expenses?[:\s]+\$?([0-9.,]+[MK]?)",
            r"OpEx[:\s]+\$?([0-9.,]+[MK]?)",
            r"Total Operating Cost[:\s]+\$?([0-9.,]+[MK]?)",
        ],
        metric_key="opex", period_label="Current Quarter", granularity="quarter",
        confidence=0.82,
    ))

    # ── Documents: runway ─────────────────────────────────────────────────────
    _append_if(metrics, _find_document_metric_any(
        docs,
        patterns=[
            r"Cash Runway[:\s]+([0-9.,]+)\s*(?:months?)?",
            r"Runway[:\s]+([0-9.,]+)\s*(?:months?)?",
            r"([0-9.,]+)\s*months?\s+(?:of\s+)?runway",
        ],
        metric_key="runway_months", period_label="Current Quarter", granularity="quarter",
        confidence=0.80, tags={"scope": "cash"},
    ))

    # ── Derived: remaining_budget from approved - committed ───────────────────
    approved = next((m for m in metrics if m.metric_key == "approved_budget"), None)
    committed = next((m for m in metrics if m.metric_key == "committed_spend"), None)
    existing_remaining = next((m for m in metrics if m.metric_key == "remaining_budget"), None)
    if approved and committed and existing_remaining is None:
        definition = find_metric_definition("remaining_budget")
        if definition:
            remaining_value = round(max(approved.value - committed.value, 0.0), 2)
            metrics.append(CanonicalMetric(
                metric_key=definition.metric_key,
                metric_label=definition.metric_label,
                category=definition.category,
                period=MetricPeriod(label=approved.period.label, granularity=approved.period.granularity),
                value=remaining_value,
                unit=definition.unit,
                source_ref=f"{approved.source_ref}|{committed.source_ref}",
                source_type="derived",
                confidence=min(approved.confidence, committed.confidence),
                tags={"scope": "project"},
                metadata={"derivation": "approved_budget - committed_spend"},
            ))

    # ── Derived: runway from cash / burn ──────────────────────────────────────
    cash_m = next((m for m in metrics if m.metric_key == "cash_on_hand"), None)
    burn_m = next((m for m in metrics if m.metric_key == "burn_rate_monthly"), None)
    existing_runway = next((m for m in metrics if m.metric_key == "runway_months"), None)
    if cash_m and burn_m and burn_m.value > 0 and existing_runway is None:
        definition = find_metric_definition("runway_months")
        if definition:
            runway_value = round(cash_m.value / burn_m.value, 1)
            metrics.append(CanonicalMetric(
                metric_key=definition.metric_key,
                metric_label=definition.metric_label,
                category=definition.category,
                period=MetricPeriod(label="Current Quarter", granularity="quarter"),
                value=runway_value,
                unit=definition.unit,
                source_ref=f"{cash_m.source_ref}|{burn_m.source_ref}",
                source_type="derived",
                confidence=min(cash_m.confidence, burn_m.confidence),
                tags={"scope": "cash"},
                metadata={"derivation": "cash_on_hand / burn_rate_monthly"},
            ))

    return MetricCollection(
        metrics=metrics,
        metadata={
            "status": "mapped_from_company_context",
            "task_input": context.task_input,
            "metric_count": len(metrics),
            "state_keys_found": _summarize_state_keys(state),
        },
    )


def _append_if(metrics: List[CanonicalMetric], metric: Optional[CanonicalMetric]) -> None:
    if metric is not None:
        metrics.append(metric)


def _summarize_state_keys(state: Dict[str, Any]) -> List[str]:
    """Return a flat list of top-level section.key paths that have non-null values."""
    found: List[str] = []
    for section_key, section in state.items():
        if isinstance(section, dict):
            for k, v in section.items():
                if v is not None:
                    found.append(f"{section_key}.{k}")
        elif state[section_key] is not None:
            found.append(section_key)
    return found

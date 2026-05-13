"""Variance analysis engine — variance-analysis skill pattern.

Compares actual vs budget or prior-period values, classifies each line as
favorable/unfavorable/neutral, flags threshold breaches, and produces both a
typed VarianceReport and a WorkbookSheetSpec ready to embed in any workbook.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from src.workflows.workbook_models import WorkbookSheetSpec, WorkbookTable


Severity = Literal["critical", "warning", "ok"]
Direction = Literal["favorable", "unfavorable", "neutral"]

# Metrics where a negative variance is *favorable* (lower cost is good)
_COST_KEYWORDS = {"cogs", "opex", "expense", "cost", "spend", "burn", "overhead", "capex"}

DEFAULT_CRITICAL_PCT = 15.0
DEFAULT_WARNING_PCT = 5.0


@dataclass
class VarianceLine:
    metric: str
    actual: float
    reference: float
    reference_label: str
    variance_abs: float
    variance_pct: float
    direction: Direction
    severity: Severity
    flag: str


@dataclass
class VarianceReport:
    period: str
    reference_label: str
    lines: List[VarianceLine]
    critical_count: int
    warning_count: int
    narrative: str
    thresholds: Dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def _is_cost_metric(metric: str) -> bool:
    lower = metric.lower()
    return any(kw in lower for kw in _COST_KEYWORDS)


def _classify_direction(metric: str, variance_abs: float) -> Direction:
    if abs(variance_abs) < 0.01:
        return "neutral"
    if _is_cost_metric(metric):
        return "favorable" if variance_abs < 0 else "unfavorable"
    return "favorable" if variance_abs > 0 else "unfavorable"


def _classify_severity(
    variance_pct: float,
    direction: Direction,
    critical_pct: float,
    warning_pct: float,
) -> Severity:
    abs_pct = abs(variance_pct)
    if direction == "unfavorable":
        if abs_pct >= critical_pct:
            return "critical"
        if abs_pct >= warning_pct:
            return "warning"
    return "ok"


def _flag_text(direction: Direction, severity: Severity, variance_pct: float) -> str:
    abs_pct = abs(variance_pct)
    arrow = "▲" if direction == "favorable" else ("▼" if direction == "unfavorable" else "→")
    if severity == "critical":
        return f"{arrow} {abs_pct:.1f}% — CRITICAL"
    if severity == "warning":
        return f"{arrow} {abs_pct:.1f}% — Watch"
    if direction == "favorable":
        return f"{arrow} {abs_pct:.1f}% — On track"
    if direction == "unfavorable":
        return f"{arrow} {abs_pct:.1f}% — Minor miss"
    return f"→ {abs_pct:.1f}%"


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

def run_variance_analysis(
    *,
    period: str,
    metrics: List[Dict],
    thresholds: Optional[Dict] = None,
) -> VarianceReport:
    """
    Run variance analysis over a list of metric dicts.

    Each metric dict must contain:
        metric            str   — metric name (e.g. "Revenue", "Total OpEx")
        actual            float — actual value for the period
        reference         float — budget or prior-period value to compare against
        reference_label   str   — label for the reference column (e.g. "Budget", "Q1 2026")

    thresholds (optional):
        critical_pct  float — unfavorable variance % that triggers CRITICAL (default 15)
        warning_pct   float — unfavorable variance % that triggers Watch   (default 5)
    """
    t = thresholds or {}
    critical_pct = float(t.get("critical_pct", DEFAULT_CRITICAL_PCT))
    warning_pct = float(t.get("warning_pct", DEFAULT_WARNING_PCT))

    lines: List[VarianceLine] = []
    reference_label = "Reference"

    for m in metrics:
        metric = str(m.get("metric", "Unknown"))
        actual = float(m.get("actual", 0))
        reference = float(m.get("reference", 0))
        ref_label = str(m.get("reference_label", "Budget"))
        reference_label = ref_label

        variance_abs = actual - reference
        variance_pct = (variance_abs / abs(reference) * 100) if reference != 0 else 0.0
        direction = _classify_direction(metric, variance_abs)
        severity = _classify_severity(variance_pct, direction, critical_pct, warning_pct)
        flag = _flag_text(direction, severity, variance_pct)

        lines.append(
            VarianceLine(
                metric=metric,
                actual=actual,
                reference=reference,
                reference_label=ref_label,
                variance_abs=variance_abs,
                variance_pct=variance_pct,
                direction=direction,
                severity=severity,
                flag=flag,
            )
        )

    critical = [l for l in lines if l.severity == "critical"]
    warnings = [l for l in lines if l.severity == "warning"]
    on_track = [l for l in lines if l.severity == "ok" and l.direction == "favorable"]

    narrative_parts: List[str] = []
    if critical:
        names = ", ".join(l.metric for l in critical)
        narrative_parts.append(f"{len(critical)} critical variance(s) require attention: {names}.")
    if warnings:
        names = ", ".join(l.metric for l in warnings)
        narrative_parts.append(f"{len(warnings)} metric(s) warrant monitoring: {names}.")
    if not critical and not warnings:
        narrative_parts.append("All metrics within acceptable thresholds.")
    if on_track:
        names = ", ".join(l.metric for l in on_track[:3])
        narrative_parts.append(f"Outperforming reference on: {names}.")

    return VarianceReport(
        period=period,
        reference_label=reference_label,
        lines=lines,
        critical_count=len(critical),
        warning_count=len(warnings),
        narrative=" ".join(narrative_parts),
        thresholds={"critical_pct": critical_pct, "warning_pct": warning_pct},
    )


# ---------------------------------------------------------------------------
# WorkbookSheetSpec output
# ---------------------------------------------------------------------------

def variance_report_to_sheet(report: VarianceReport) -> WorkbookSheetSpec:
    """Convert a VarianceReport into a WorkbookSheetSpec with kind='variance'."""

    def _fmt(v: float) -> str:
        return f"${v:,.0f}"

    def _pct(v: float) -> str:
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.1f}%"

    ref_label = report.lines[0].reference_label if report.lines else report.reference_label

    columns = ["Metric", "Actual", ref_label, "Variance ($)", "Variance (%)", "Status"]
    rows = [
        [
            l.metric,
            _fmt(l.actual),
            _fmt(l.reference),
            _fmt(l.variance_abs),
            _pct(l.variance_pct),
            l.flag,
        ]
        for l in report.lines
    ]

    tables = [
        WorkbookTable(
            title=f"Variance Analysis — {report.period}",
            columns=columns,
            rows=rows,
        ),
        WorkbookTable(
            title="Executive Summary",
            columns=["Summary"],
            rows=[[report.narrative]],
        ),
    ]

    critical_rows = [
        [l.metric, l.flag, _pct(l.variance_pct), _fmt(l.variance_abs)]
        for l in report.lines
        if l.severity == "critical"
    ]
    if critical_rows:
        tables.append(
            WorkbookTable(
                title="Critical Flags",
                columns=["Metric", "Flag", "Variance %", "Variance ($)"],
                rows=critical_rows,
            )
        )

    warning_rows = [
        [l.metric, l.flag, _pct(l.variance_pct)]
        for l in report.lines
        if l.severity == "warning"
    ]
    if warning_rows:
        tables.append(
            WorkbookTable(
                title="Warnings",
                columns=["Metric", "Flag", "Variance %"],
                rows=warning_rows,
            )
        )

    return WorkbookSheetSpec(
        name="Variance",
        kind="variance",
        tables=tables,
        chart_specs=[],
        metadata={
            "period": report.period,
            "reference_label": report.reference_label,
            "critical_count": report.critical_count,
            "warning_count": report.warning_count,
            "narrative": report.narrative,
            "thresholds": report.thresholds,
        },
    )

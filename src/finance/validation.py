from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .metrics import find_metric_definition


class QACheckItem(BaseModel):
    name: str
    passed: bool
    severity: Literal["info", "warning", "critical"]
    detail: str
    flagged_items: List[str] = Field(default_factory=list)


class QAChecklistResult(BaseModel):
    passed: bool
    checks: List[QACheckItem]
    failed_count: int
    warning_count: int
    flagged_metrics: List[str]
    summary: str


# Maps company_state field paths to row metric name patterns used in WorkbookFinancialRow.
_STATE_TO_ROW_PATTERNS: list[tuple[str, str, str]] = [
    # (state_section, state_key, row_metric_pattern)
    ("capital_position", "cash_on_hand",       r"cash (at bank|on hand|balance)"),
    ("cost_structure",   "burn_rate_monthly",   r"burn rate"),
    ("capital_position", "runway_months",       r"cash runway"),
]


def run_finance_qa_checklist(
    *,
    rows: list,  # list[WorkbookFinancialRow]
    expected_metric_keys: list[str],
    company_state: Dict[str, Any],
    finance_template: Optional[str] = None,
) -> QAChecklistResult:
    checks: list[QACheckItem] = [
        _check_source_quality(rows),
        _check_missing_metrics(rows, expected_metric_keys),
        _check_magnitude_sanity(rows),
        _check_cross_reference(rows, company_state),
    ]

    failed = [c for c in checks if not c.passed]
    critical = [c for c in failed if c.severity == "critical"]
    warnings_list = [c for c in failed if c.severity == "warning"]

    all_flagged: list[str] = []
    for check in failed:
        all_flagged.extend(check.flagged_items)
    flagged_metrics = list(dict.fromkeys(all_flagged))

    passed = len(critical) == 0
    if failed:
        parts = []
        if critical:
            parts.append(f"{len(critical)} critical issue(s)")
        if warnings_list:
            parts.append(f"{len(warnings_list)} warning(s)")
        summary = f"QA checklist: {', '.join(parts)}. Flagged: {', '.join(flagged_metrics) or 'none'}."
    else:
        summary = f"QA checklist passed. {len(rows)} row(s) validated."

    return QAChecklistResult(
        passed=passed,
        checks=checks,
        failed_count=len(failed),
        warning_count=len(warnings_list),
        flagged_metrics=flagged_metrics,
        summary=summary,
    )


def _check_source_quality(rows: list) -> QACheckItem:
    """Check 1: every row must have a non-empty source_ref and source_type != 'fallback'."""
    flagged: list[str] = []
    for row in rows:
        if not getattr(row, "source_ref", ""):
            flagged.append(f"{row.metric} ({row.period}): missing source_ref")
        if getattr(row, "source_type", "") == "fallback":
            flagged.append(f"{row.metric} ({row.period}): source_type is fallback")

    if flagged:
        return QACheckItem(
            name="source_quality",
            passed=False,
            severity="warning",
            detail=f"{len(flagged)} row(s) have missing or fallback sources.",
            flagged_items=flagged,
        )
    return QACheckItem(
        name="source_quality",
        passed=True,
        severity="info",
        detail="All rows have sourced, non-fallback values.",
    )


def _check_missing_metrics(rows: list, expected_metric_keys: list[str]) -> QACheckItem:
    """Check 2: all expected metrics from the finance template should appear in rows."""
    if not expected_metric_keys:
        return QACheckItem(
            name="missing_metrics",
            passed=True,
            severity="info",
            detail="No expected metrics specified for this template.",
        )

    row_metric_names = {(getattr(row, "metric", "") or "").lower() for row in rows}
    flagged: list[str] = []

    for key in expected_metric_keys:
        definition = find_metric_definition(key)
        label = definition.metric_label.lower() if definition else key.replace("_", " ").lower()
        # Check if any row metric contains the label words (at least 2 tokens must match)
        label_tokens = set(label.split())
        matched = any(
            len(label_tokens & set(row_name.split())) >= min(2, len(label_tokens))
            for row_name in row_metric_names
        )
        if not matched:
            display = definition.metric_label if definition else key
            flagged.append(display)

    if flagged:
        return QACheckItem(
            name="missing_metrics",
            passed=False,
            severity="warning",
            detail=f"{len(flagged)} expected metric(s) are absent from the report rows.",
            flagged_items=flagged,
        )
    return QACheckItem(
        name="missing_metrics",
        passed=True,
        severity="info",
        detail="All expected metrics are present.",
    )


def _check_magnitude_sanity(rows: list) -> QACheckItem:
    """Check 3: flag extreme values — negative actuals, implausibly large numbers,
    or actuals >500% different from budget."""
    IMPLAUSIBLE_MAX = 1e10  # $10B
    BUDGET_RATIO_THRESHOLD = 5.0

    flagged: list[str] = []
    for row in rows:
        metric = getattr(row, "metric", "")
        period = getattr(row, "period", "")
        actual = getattr(row, "actual", 0.0)
        budget = getattr(row, "budget", 0.0)
        source_type = getattr(row, "source_type", "")

        # Negative actuals are suspicious for cost/capital/revenue metrics
        if actual < 0 and source_type != "derived_metric":
            flagged.append(f"{metric} ({period}): negative actual {actual:,.0f}")

        # Implausibly large values
        if abs(actual) > IMPLAUSIBLE_MAX:
            flagged.append(f"{metric} ({period}): actual {actual:,.0f} exceeds plausibility threshold")

        # Budget vs actual extreme divergence (only when both are non-zero)
        if budget != 0 and actual != 0:
            ratio = abs(actual - budget) / abs(budget)
            if ratio > BUDGET_RATIO_THRESHOLD:
                flagged.append(
                    f"{metric} ({period}): actual is {ratio:.0%} different from budget — possible scale error"
                )

    if flagged:
        severity: Literal["warning", "critical"] = (
            "critical" if any("exceeds plausibility" in f for f in flagged) else "warning"
        )
        return QACheckItem(
            name="magnitude_sanity",
            passed=False,
            severity=severity,
            detail=f"{len(flagged)} row(s) have suspicious values.",
            flagged_items=flagged,
        )
    return QACheckItem(
        name="magnitude_sanity",
        passed=True,
        severity="info",
        detail="All row values are within plausible ranges.",
    )


def _check_cross_reference(rows: list, company_state: Dict[str, Any]) -> QACheckItem:
    """Check 4: key row values should be consistent with company_state ground truth (within 50%)."""
    flagged: list[str] = []

    for state_section, state_key, row_pattern in _STATE_TO_ROW_PATTERNS:
        section = company_state.get(state_section)
        if not isinstance(section, dict):
            continue
        state_value = section.get(state_key)
        if state_value is None:
            continue
        try:
            state_float = float(state_value)
        except (TypeError, ValueError):
            continue
        if state_float == 0:
            continue

        # Find matching row
        pattern = re.compile(row_pattern, re.IGNORECASE)
        matching_rows = [row for row in rows if pattern.search(getattr(row, "metric", "") or "")]
        for row in matching_rows:
            actual = getattr(row, "actual", None)
            if actual is None:
                continue
            try:
                actual_float = float(actual)
            except (TypeError, ValueError):
                continue
            if actual_float == 0:
                continue
            divergence = abs(actual_float - state_float) / abs(state_float)
            if divergence > 0.50:
                flagged.append(
                    f"{row.metric}: row actual {actual_float:,.0f} differs from "
                    f"company_state {state_section}.{state_key} ({state_float:,.0f}) by {divergence:.0%}"
                )

    if flagged:
        return QACheckItem(
            name="cross_reference",
            passed=False,
            severity="warning",
            detail=f"{len(flagged)} row(s) diverge >50% from company_state ground truth.",
            flagged_items=flagged,
        )
    return QACheckItem(
        name="cross_reference",
        passed=True,
        severity="info",
        detail="Row values are consistent with company_state.",
    )

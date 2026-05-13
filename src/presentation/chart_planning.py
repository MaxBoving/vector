from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from src.presentation.presentation_contract import ChartIntentKind, ChartRequest


class QuantitativeEvidenceBundle(BaseModel):
    numeric_series: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    time_periods: list[str] = Field(default_factory=list)
    comparisons: list[dict[str, Any]] = Field(default_factory=list)
    available_fields: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class ChartPlan(BaseModel):
    requests: list[ChartRequest] = Field(default_factory=list)
    available_fields: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)


def build_chart_plan(
    *,
    numeric_series: Sequence[Mapping[str, Any]] | None = None,
    dimensions: Sequence[str] | None = None,
    time_periods: Sequence[str] | None = None,
    comparisons: Sequence[Mapping[str, Any]] | None = None,
    available_fields: Sequence[str] | None = None,
) -> ChartPlan:
    rows = [dict(row) for row in (numeric_series or []) if isinstance(row, Mapping)]
    comparison_rows = [dict(item) for item in (comparisons or []) if isinstance(item, Mapping)]
    dimensions_list = _normalize_strings(dimensions)
    periods = _normalize_strings(time_periods)
    explicit_fields = _normalize_strings(available_fields)
    inferred_fields = _infer_available_fields(rows)
    all_fields = _merge_preserving_order(explicit_fields, inferred_fields)

    numeric_fields = _numeric_fields(rows, all_fields)
    primary_field = _pick_field(numeric_fields, preferred=("actual", "value", "amount", "count", "total"))
    secondary_field = _pick_field(
        numeric_fields,
        preferred=("budget", "plan", "target", "forecast", "variance", "delta"),
        exclude=primary_field,
    )
    comparison_label = dimensions_list[0] if dimensions_list else _first_non_empty(rows, ("metric", "category", "dimension", "name"))
    primary_dimension = dimensions_list[0] if dimensions_list else _first_non_empty(rows, ("period", "time_period", "month", "quarter"))

    requests: list[ChartRequest] = []
    signals: list[str] = []

    if comparison_rows:
        requests.append(
            ChartRequest(
                kind=ChartIntentKind.COMPARISON,
                title=_chart_title(comparison_label, "Comparison"),
                purpose="Compare values across the provided dimension.",
                x_axis=_axis_label(comparison_label, "Category"),
                y_axis="Delta",
                group_by=comparison_label,
                series=_series_for_fields(all_fields, primary_field, secondary_field),
                required=False,
                rationale="Comparison rows are available, so a delta chart is useful.",
            )
        )
        signals.append("comparison_rows")
    elif primary_field and secondary_field:
        requests.append(
            ChartRequest(
                kind=ChartIntentKind.VARIANCE,
                title=_chart_title(comparison_label or primary_field, "Variance vs Plan"),
                purpose="Show where the primary measure diverges from the secondary measure.",
                x_axis=_axis_label(comparison_label, "Category"),
                y_axis=_axis_label(secondary_field, "Variance"),
                group_by=comparison_label,
                series=_series_for_fields(all_fields, primary_field, secondary_field),
                required=False,
                rationale="Multiple numeric series are available, so variance is decision-relevant.",
            )
        )
        signals.append("variance_pair")

    if len(periods) > 1 and primary_field:
        requests.append(
            ChartRequest(
                kind=ChartIntentKind.TREND,
                title=_chart_title(primary_field, "Trend"),
                purpose="Show how the main measure changes over time.",
                x_axis=_axis_label(primary_dimension, "Period"),
                y_axis=_axis_label(primary_field, "Value"),
                group_by=primary_dimension or "period",
                series=_series_for_fields(all_fields, primary_field, secondary_field),
                required=False,
                rationale="Multiple time periods are available, so a trend chart is useful.",
            )
        )
        signals.append("time_series")

    if "forecast" in all_fields and len(requests) < 3:
        requests.append(
            ChartRequest(
                kind=ChartIntentKind.FORECAST,
                title=_chart_title(primary_field or "forecast", "Forecast Trajectory"),
                purpose="Show expected direction of travel.",
                x_axis=_axis_label(primary_dimension, "Period"),
                y_axis="Forecast",
                group_by=primary_dimension or "period",
                series=_series_for_fields(all_fields, primary_field, secondary_field, "forecast"),
                required=False,
                rationale="Forecast data is available and should be visualized separately.",
            )
        )
        signals.append("forecast")

    if not requests and numeric_fields:
        requests.append(
            ChartRequest(
                kind=ChartIntentKind.MIX,
                title=_chart_title(comparison_label or primary_field or numeric_fields[0], "Mixed Measures"),
                purpose="Show the available quantitative measures together.",
                x_axis=_axis_label(comparison_label, "Category"),
                y_axis=_axis_label(primary_field or numeric_fields[0], "Value"),
                group_by=comparison_label,
                series=_series_for_fields(all_fields, primary_field, secondary_field),
                required=False,
                rationale="Quantifiable data is available even without a stronger chart cue.",
            )
        )
        signals.append("fallback_mix")

    return ChartPlan(
        requests=requests[:3],
        available_fields=all_fields,
        signals=signals,
    )


def _normalize_strings(values: Sequence[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values or []:
        cleaned = " ".join(str(value).split()).strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(cleaned)
    return normalized


def _infer_available_fields(rows: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for key, value in row.items():
            if key in seen:
                continue
            if _is_numeric(value):
                seen.append(key)
    priority = [
        "actual",
        "budget",
        "forecast",
        "variance",
        "value",
        "amount",
        "count",
        "total",
        "delta",
        "plan",
        "target",
    ]
    ordered: list[str] = []
    seen_lower = {field.lower(): field for field in seen}
    for field in priority:
        actual = seen_lower.get(field)
        if actual and actual not in ordered:
            ordered.append(actual)
    for field in seen:
        if field not in ordered:
            ordered.append(field)
    return ordered


def _numeric_fields(rows: list[dict[str, Any]], available_fields: list[str]) -> list[str]:
    if not available_fields:
        return _infer_available_fields(rows)
    numeric_fields: list[str] = []
    for field in available_fields:
        if any(_is_numeric(row.get(field)) for row in rows):
            numeric_fields.append(field)
    return numeric_fields


def _pick_field(fields: list[str], *, preferred: Sequence[str], exclude: str | None = None) -> str | None:
    lowered_exclude = exclude.lower() if exclude else None
    preferred_lower = [item.lower() for item in preferred]
    for target in preferred_lower:
        for field in fields:
            if field.lower() == target and field.lower() != lowered_exclude:
                return field
    for field in fields:
        if lowered_exclude and field.lower() == lowered_exclude:
            continue
        return field
    return None


def _series_for_fields(fields: list[str], *desired: str | None) -> list[str]:
    desired_lower = {field.lower() for field in desired if field}
    series = [field for field in fields if field.lower() in desired_lower]
    return series or list(fields[:3])


def _first_non_empty(rows: list[dict[str, Any]], keys: Sequence[str]) -> str | None:
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            text = " ".join(str(value).split()).strip()
            if text:
                return text
    return None


def _chart_title(seed: str | None, fallback: str) -> str:
    text = " ".join(str(seed or "").split()).strip()
    if not text:
        return fallback
    return text[:80]


def _axis_label(seed: str | None, fallback: str) -> str:
    text = " ".join(str(seed or "").split()).strip()
    return text[:40] if text else fallback


def _merge_preserving_order(*lists: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for values in lists:
        for value in values:
            lowered = value.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            merged.append(value)
    return merged


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

from __future__ import annotations

import re
from typing import Any

from src.presentation.presentation_contract import (
    ChartIntentKind,
    ChartRequest,
    ChartValidationResult,
    PresentationBlock,
    PresentationQualityResult,
    PresentationSpec,
)

_PLACEHOLDER_PATTERNS = (
    re.compile(r"\bconfirm the key assumption\b", re.IGNORECASE),
    re.compile(r"\btbd\b", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"\bto be determined\b", re.IGNORECASE),
)

_PROFILE_BULLET_CAPS: dict[str, int] = {
    "board_deck": 4,
    "memo": 6,
    "brief": 5,
    "financial_analysis": 5,
    "report": 6,
    "canvas": 4,
}


def normalize_presentation_spec(raw_spec: PresentationSpec | dict[str, Any]) -> PresentationSpec:
    spec = raw_spec if isinstance(raw_spec, PresentationSpec) else PresentationSpec(**raw_spec)
    repaired = spec.model_copy(deep=True)
    repaired.title = _normalize_whitespace(repaired.title)
    repaired.executive_summary = _normalize_whitespace(repaired.executive_summary)
    repaired.recommendation = _normalize_optional_text(repaired.recommendation)
    repaired.decision_required = _normalize_optional_text(repaired.decision_required)
    repaired.assumptions = _normalize_list(repaired.assumptions)
    repaired.sensitivities = _normalize_list(repaired.sensitivities)
    repaired.charts = _normalize_chart_requests(repaired.charts)

    normalized_blocks: list[PresentationBlock] = []
    bullet_cap = _PROFILE_BULLET_CAPS.get(repaired.artifact_kind, 5)
    for index, block in enumerate(repaired.blocks):
        title = _normalize_whitespace(block.title)
        summary = _normalize_optional_text(block.summary)
        bullets = _normalize_list(block.bullets)[:bullet_cap]
        if repaired.artifact_kind == "board_deck":
            title, summary, bullets = _repair_board_deck_block(title=title, summary=summary, bullets=bullets)
        if not title and not summary and not bullets:
            continue
        if repaired.artifact_kind == "board_deck":
            title = _compress_deck_title(title or summary or f"Slide {index + 1}")
        normalized_blocks.append(
            block.model_copy(
                update={
                    "title": title or f"Section {index + 1}",
                    "summary": summary,
                    "bullets": bullets,
                }
            )
        )
    repaired.blocks = normalized_blocks
    if repaired.artifact_kind == "board_deck":
        repaired.title = _compress_deck_title(repaired.title)
    return repaired


def validate_presentation_spec(raw_spec: PresentationSpec | dict[str, Any]) -> PresentationQualityResult:
    spec = normalize_presentation_spec(raw_spec)
    hard_failures: list[str] = []
    warnings: list[str] = []
    chart_validation: ChartValidationResult | None = None

    if not spec.title:
        hard_failures.append("missing_title")
    if not spec.executive_summary:
        hard_failures.append("missing_executive_summary")
    if spec.intent in {"decide", "approve"} and not spec.recommendation:
        hard_failures.append("missing_recommendation")
    if spec.audience in {"board", "ceo"} and spec.artifact_kind == "board_deck" and not spec.decision_required:
        warnings.append("missing_decision_required")
    if not spec.blocks:
        hard_failures.append("missing_content_blocks")

    for block in spec.blocks:
        joined = " ".join([block.title, block.summary or "", *block.bullets]).strip()
        if any(pattern.search(joined) for pattern in _PLACEHOLDER_PATTERNS):
            hard_failures.append(f"placeholder_content:{block.title}")
        if spec.artifact_kind == "board_deck" and len(block.bullets) > 4:
            warnings.append(f"dense_slide:{block.title}")
        if not block.summary and not block.bullets:
            warnings.append(f"thin_block:{block.title}")

    if spec.artifact_kind == "board_deck" and len(spec.title) > 120:
        warnings.append("headline_too_long")
    if spec.artifact_kind == "board_deck" and not any(block.kind in {"decision", "actions"} for block in spec.blocks):
        warnings.append("board_deck_missing_decision_slide")
    if spec.sensitivities and spec.audience in {"board", "ceo"}:
        warnings.append("contains_sensitivities")

    if spec.charts:
        available_series = _normalize_list(_metadata_chart_series(spec.metadata))
        chart_reasons: list[str] = []
        chart_supported = True
        if not available_series:
            chart_reasons.append("chart_series_not_provided")
            chart_supported = False
        for chart in spec.charts:
            if chart.required and not chart.series:
                hard_failures.append(f"missing_chart_series:{chart.title}")
                chart_supported = False
                continue
            if available_series and chart.series and not set(s.lower() for s in chart.series).intersection(
                s.lower() for s in available_series
            ):
                reason = f"chart_series_not_available:{chart.title}"
                if chart.required:
                    hard_failures.append(reason)
                    chart_supported = False
                else:
                    warnings.append(reason)
                chart_reasons.append(reason)
        chart_validation = ChartValidationResult(
            supported=chart_supported and not any(reason.startswith("missing_chart_series:") for reason in hard_failures),
            reasons=list(dict.fromkeys(chart_reasons)),
            available_series=available_series,
        )

    penalty = min(0.8, len(hard_failures) * 0.25 + len(warnings) * 0.05)
    return PresentationQualityResult(
        profile=spec.artifact_kind,
        presentation_ready=not hard_failures,
        score=max(0.0, 1.0 - penalty),
        hard_failures=hard_failures,
        warnings=list(dict.fromkeys(warnings)),
        repaired=spec != (raw_spec if isinstance(raw_spec, PresentationSpec) else PresentationSpec(**raw_spec)),
        chart_validation=chart_validation,
    )


def normalize_and_validate_presentation_spec(
    raw_spec: PresentationSpec | dict[str, Any],
) -> tuple[PresentationSpec, PresentationQualityResult]:
    spec = normalize_presentation_spec(raw_spec)
    quality = validate_presentation_spec(spec)
    return spec, quality


def _normalize_whitespace(text: str | None) -> str:
    return " ".join((text or "").split()).strip()


def _normalize_optional_text(text: str | None) -> str | None:
    cleaned = _normalize_whitespace(text)
    return cleaned or None


def _normalize_list(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values or []:
        cleaned = _normalize_whitespace(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
    return normalized


def _normalize_chart_requests(values: list[ChartRequest] | None) -> list[ChartRequest]:
    seen: set[tuple[Any, ...]] = set()
    normalized: list[ChartRequest] = []
    for value in values or []:
        chart = value if isinstance(value, ChartRequest) else ChartRequest(**value)
        title = _normalize_whitespace(chart.title)
        if not title or chart.kind == ChartIntentKind.NONE:
            continue
        purpose = _normalize_optional_text(chart.purpose)
        x_axis = _normalize_optional_text(chart.x_axis)
        y_axis = _normalize_optional_text(chart.y_axis)
        group_by = _normalize_optional_text(chart.group_by)
        series = _normalize_list(chart.series)
        normalized_chart = chart.model_copy(
            update={
                "title": title,
                "purpose": purpose,
                "x_axis": x_axis,
                "y_axis": y_axis,
                "group_by": group_by,
                "series": series,
                "rationale": _normalize_optional_text(chart.rationale),
            }
        )
        key = (
            normalized_chart.kind,
            normalized_chart.title.lower(),
            normalized_chart.x_axis.lower() if normalized_chart.x_axis else "",
            normalized_chart.y_axis.lower() if normalized_chart.y_axis else "",
            normalized_chart.group_by.lower() if normalized_chart.group_by else "",
            tuple(normalized_chart.series),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_chart)
    return normalized


def _metadata_chart_series(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get("available_chart_series") or metadata.get("chart_series") or []
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [raw]
    return []


def _compress_deck_title(text: str) -> str:
    cleaned = _normalize_whitespace(text)
    if not cleaned:
        return ""
    for separator in (" — ", ". ", "; "):
        if separator in cleaned:
            cleaned = cleaned.split(separator, 1)[0].strip()
            break
    return cleaned[:120].rstrip(" ,;:-")


def _repair_board_deck_block(*, title: str, summary: str | None, bullets: list[str]) -> tuple[str, str | None, list[str]]:
    repaired_title = title
    repaired_summary = summary
    repaired_bullets = bullets

    if repaired_title.lower() in {"title", "executive summary"} and repaired_bullets:
        repaired_bullets = repaired_bullets[:1]

    question_only = all(
        item.endswith("?") or item.lower().startswith(("confirm ", "decide ", "determine "))
        for item in repaired_bullets
    ) if repaired_bullets else False
    if question_only:
        repaired_title = "Decision Framing" if repaired_title.lower() in {"key questions", "questions"} else repaired_title
        if repaired_summary is None and repaired_bullets:
            repaired_summary = repaired_bullets[0]
        repaired_bullets = []

    return repaired_title, repaired_summary, repaired_bullets

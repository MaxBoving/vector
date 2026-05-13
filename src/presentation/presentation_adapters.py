from __future__ import annotations

from src.presentation.deck_spec import DeckSlideSpec, DeckSpec
from src.presentation.presentation_contract import PresentationSpec
from src.presentation.presentation_validator import normalize_presentation_spec
from src.presentation.report_spec import MemoSectionSpec, MemoSpec


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def presentation_spec_to_memo_spec(
    spec: PresentationSpec,
    *,
    template_id: str,
    theme_id: str,
    finance_template: str | None = None,
) -> MemoSpec:
    normalized = normalize_presentation_spec(spec)
    sections: list[MemoSectionSpec] = []
    for block in normalized.blocks:
        items = []
        if block.summary:
            items.append(block.summary)
        items.extend(block.bullets)
        items = _dedupe_preserve_order([str(item) for item in items if str(item).strip()])
        if items:
            sections.append(MemoSectionSpec(label=block.title, items=items[:6]))
    return MemoSpec(
        title=normalized.title,
        summary=normalized.executive_summary,
        section_order=[section.label for section in sections],
        sections=sections,
        assumptions=normalized.assumptions + normalized.sensitivities[:2],
        open_questions=[normalized.decision_required] if normalized.decision_required and normalized.intent in {"decide", "approve"} else [],
        metadata={
            "template_id": template_id,
            "theme_id": theme_id,
            "presentation_version": "presentation_spec_v1",
            "finance_template": finance_template,
            "artifact_kind": normalized.artifact_kind,
            "audience": normalized.audience,
            "charts": [chart.model_dump(mode="json") for chart in normalized.charts],
        },
    )


def presentation_spec_to_deck_spec(
    spec: PresentationSpec,
    *,
    template_id: str,
    theme_id: str,
    finance_template: str | None = None,
) -> DeckSpec:
    normalized = normalize_presentation_spec(spec)
    slides: list[DeckSlideSpec] = [DeckSlideSpec(title="Title", bullets=[normalized.executive_summary], kind="title")]
    if normalized.recommendation:
        slides.append(DeckSlideSpec(title="Recommendation", bullets=[normalized.recommendation], kind="decision"))
    for block in normalized.blocks:
        if block.title.lower() in {"title", "executive summary"}:
            continue
        bullets = list(block.bullets)
        if block.summary and block.summary not in bullets:
            bullets = [block.summary, *bullets]
        slides.append(
            DeckSlideSpec(
                title=block.title,
                bullets=bullets[:4],
                kind="decision" if block.kind in {"headline", "decision", "actions"} else "content",
            )
        )
    if normalized.decision_required and not any(slide.title.lower() == "decision required" for slide in slides):
        slides.append(DeckSlideSpec(title="Decision Required", bullets=[normalized.decision_required], kind="decision"))
    return DeckSpec(
        title=normalized.title,
        subtitle=normalized.executive_summary,
        slide_order=[slide.title for slide in slides],
        slides=slides,
        metadata={
            "template_id": template_id,
            "theme_id": theme_id,
            "presentation_version": "presentation_spec_v1",
            "finance_template": finance_template,
            "artifact_kind": normalized.artifact_kind,
            "audience": normalized.audience,
            "charts": [chart.model_dump(mode="json") for chart in normalized.charts],
        },
    )

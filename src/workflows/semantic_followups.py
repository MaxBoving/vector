from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, Field

from src.workflows.follow_up_planner import FollowUpCandidate, build_follow_up_candidate, select_follow_up_candidates


class SemanticContext(BaseModel):
    topic: str
    importance: float = 0.0
    date: str | None = None
    families: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    confidence_score: float = 0.5
    evidence_state: str | None = None
    missing_context: list[str] = Field(default_factory=list)
    needs_more_info: bool = False
    summary: str | None = None
    workflow_type: str | None = None
    response_type: str | None = None


_GENERIC_TITLE_TOKENS = {
    "brief",
    "report",
    "memo",
    "summary",
    "overview",
    "analysis",
    "update",
    "executive brief",
    "morning brief",
    "weekly recap",
    "schedule",
    "calendar",
}

_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:[T ][0-9:\-+.Z]+)?\b")


def build_semantic_context(
    *,
    title: str | None = None,
    summary: str | None = None,
    sections: Sequence[Mapping[str, Any]] | None = None,
    sources: Sequence[Mapping[str, Any]] | None = None,
    confidence_score: float = 0.5,
    evidence_state: str | None = None,
    missing_context: Iterable[str] = (),
    workflow_type: str | None = None,
    response_type: str | None = None,
    topic_hint: str | None = None,
    date_hint: str | None = None,
    importance_hint: float | None = None,
    families_hint: Iterable[str] = (),
    source_ids_hint: Iterable[str] = (),
) -> SemanticContext:
    section_list = [section for section in (sections or []) if isinstance(section, Mapping)]
    source_list = [source for source in (sources or []) if isinstance(source, Mapping)]
    topic = _compact_text(topic_hint or title or summary or "this item")
    date = date_hint or _extract_date_hint(section_list, source_list)
    families = _merge_unique(list(families_hint))
    if not families and workflow_type:
        families = [str(workflow_type).strip()]
    source_ids = _merge_unique(
        list(source_ids_hint)
        + [str(source.get("source_id") or "").strip() for source in source_list if str(source.get("source_id") or "").strip()]
    )
    resolved_missing_context = [str(item).strip() for item in missing_context if str(item).strip()]
    confidence_score = _clamp(float(confidence_score or 0.0), 0.0, 1.0)
    importance = _score_importance(
        confidence_score=confidence_score,
        evidence_state=evidence_state,
        families=families,
        sections=section_list,
        importance_hint=importance_hint,
    )
    needs_more_info = bool(resolved_missing_context) or confidence_score < 0.45 or evidence_state == "sparse"

    return SemanticContext(
        topic=topic,
        importance=importance,
        date=date,
        families=families,
        source_ids=source_ids,
        confidence_score=confidence_score,
        evidence_state=evidence_state,
        missing_context=resolved_missing_context,
        needs_more_info=needs_more_info,
        summary=summary.strip() if isinstance(summary, str) and summary.strip() else None,
        workflow_type=workflow_type,
        response_type=response_type,
    )


def build_semantic_follow_up_candidates(
    context: SemanticContext,
    *,
    limit: int = 3,
) -> list[FollowUpCandidate]:
    if not context.topic:
        return []

    family = context.families[0] if context.families else "semantic"
    candidates: list[FollowUpCandidate] = []

    candidates.append(
        build_follow_up_candidate(
            f"Review {context.topic}",
            family=f"semantic:{family}",
            deadline_at=context.date,
            priority=context.importance,
            topic_key=context.topic,
        )
    )

    if context.date:
        candidates.append(
            build_follow_up_candidate(
                f"Check the timing for {context.topic}",
                family=f"semantic:{family}",
                deadline_at=context.date,
                priority=max(context.importance - 5.0, 0.0),
                topic_key=context.topic,
            )
        )

    if context.needs_more_info:
        candidates.append(
            build_follow_up_candidate(
                f"Pull more detail on {context.topic}",
                family="semantic:clarification",
                deadline_at=context.date,
                priority=context.importance + 5.0,
                topic_key=context.topic,
            )
        )
    elif context.importance >= 70.0:
        candidates.append(
            build_follow_up_candidate(
                f"Decide the next step for {context.topic}",
                family=f"semantic:{family}",
                deadline_at=context.date,
                priority=max(context.importance - 2.0, 0.0),
                topic_key=context.topic,
            )
        )

    selected = select_follow_up_candidates(candidates, limit=limit)
    return selected


def build_semantic_question_options(context: SemanticContext) -> list[dict[str, Any]]:
    if not context.needs_more_info or not context.topic:
        return []

    date_suffix = f" before {context.date}" if context.date else ""
    question = f"Want me to pull more detail on {context.topic}{date_suffix}?"
    return [
        {
            "question": question,
            "offer_type": "clarification",
            "options": [
                {
                    "label": "Pull more detail",
                    "value": "retrieve_more_context",
                    "description": "Gather the missing context before deciding",
                    "apply_text": (
                        f"Pull more detail on {context.topic}{date_suffix}. "
                        f"Prioritize the most important evidence and keep the current summary in view."
                    ),
                },
                {
                    "label": "Use current summary",
                    "value": "use_current_summary",
                    "description": "Continue with the current level of detail",
                    "apply_text": (
                        f"Continue with the current summary for {context.topic}. "
                        "Do not expand the context further."
                    ),
                },
            ],
        }
    ]


def _score_importance(
    *,
    confidence_score: float,
    evidence_state: str | None,
    families: Sequence[str],
    sections: Sequence[Mapping[str, Any]],
    importance_hint: float | None,
) -> float:
    if importance_hint is not None:
        return _clamp(float(importance_hint), 0.0, 100.0)

    score = confidence_score * 100.0
    if any(family in {"finance", "board", "customer"} for family in families):
        score += 8.0
    if any(
        marker in " ".join(str(section.get("label") or "").lower() for section in sections)
        for marker in ("risk", "decision", "action", "deadline", "meeting", "priority")
    ):
        score += 10.0
    if evidence_state == "mixed":
        score -= 6.0
    elif evidence_state == "sparse":
        score -= 14.0
    return _clamp(score, 0.0, 100.0)


def _extract_date_hint(
    sections: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
) -> str | None:
    blobs: list[str] = []
    for section in sections:
        blobs.append(str(section.get("content") or ""))
        for item in section.get("items") or []:
            blobs.append(str(item))
    for source in sources:
        blobs.append(str(source.get("snippet") or ""))
        blobs.append(str(source.get("relevance_reason") or ""))

    for blob in blobs:
        match = _ISO_DATE_RE.search(blob)
        if not match:
            continue
        candidate = match.group(0).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    return None


def _compact_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def _merge_unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        normalized = _compact_text(value)
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        merged.append(normalized)
    return merged


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))

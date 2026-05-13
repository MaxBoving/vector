from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass(frozen=True)
class FollowUpCandidate:
    text: str
    family: str = "general"
    deadline_at: str | None = None
    priority: float = 0.0
    topic_key: str | None = None


def build_follow_up_candidate(
    text: str,
    *,
    family: str = "general",
    deadline_at: Any = None,
    priority: float = 0.0,
    topic_key: str | None = None,
) -> FollowUpCandidate:
    normalized_text = " ".join(str(text or "").split()).strip()
    normalized_family = " ".join(str(family or "general").split()).strip() or "general"
    normalized_topic = " ".join(str(topic_key or "").split()).strip() or None
    normalized_deadline = _normalize_deadline(deadline_at)
    return FollowUpCandidate(
        text=normalized_text,
        family=normalized_family,
        deadline_at=normalized_deadline,
        priority=float(priority or 0.0),
        topic_key=normalized_topic,
    )


def select_follow_up_candidates(
    candidates: Iterable[FollowUpCandidate | dict[str, Any]],
    *,
    limit: int = 3,
) -> list[FollowUpCandidate]:
    normalized = [
        candidate if isinstance(candidate, FollowUpCandidate) else _candidate_from_dict(candidate)
        for candidate in candidates
        if _candidate_text(candidate)
    ]
    if not normalized:
        return []

    ranked = sorted(
        normalized,
        key=lambda item: (
            _deadline_sort_key(item.deadline_at),
            -float(item.priority or 0.0),
            item.text.lower(),
        ),
    )

    selected: list[FollowUpCandidate] = []
    seen_families: set[str] = set()
    seen_topics: set[str] = set()

    def _accept(candidate: FollowUpCandidate) -> bool:
        family_key = candidate.family or "general"
        topic_key = candidate.topic_key or candidate.text
        if family_key in seen_families and topic_key in seen_topics:
            return False
        if family_key in seen_families:
            return False
        selected.append(candidate)
        seen_families.add(family_key)
        seen_topics.add(topic_key)
        return True

    for candidate in ranked:
        if len(selected) >= limit:
            break
        _accept(candidate)

    if len(selected) < limit:
        for candidate in ranked:
            if len(selected) >= limit:
                break
            topic_key = candidate.topic_key or candidate.text
            if topic_key in seen_topics and candidate.family in seen_families:
                continue
            selected.append(candidate)
            seen_topics.add(topic_key)
            seen_families.add(candidate.family or "general")

    return selected[:limit]


def _candidate_text(candidate: FollowUpCandidate | dict[str, Any]) -> str:
    if isinstance(candidate, FollowUpCandidate):
        return candidate.text
    return str(candidate.get("text") or candidate.get("label") or "").strip()


def _candidate_from_dict(candidate: dict[str, Any]) -> FollowUpCandidate:
    return build_follow_up_candidate(
        str(candidate.get("text") or candidate.get("label") or ""),
        family=str(candidate.get("family") or "general"),
        deadline_at=candidate.get("deadline_at"),
        priority=float(candidate.get("priority") or 0.0),
        topic_key=str(candidate.get("topic_key") or candidate.get("text") or candidate.get("label") or ""),
    )


def _normalize_deadline(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _deadline_sort_key(value: str | None) -> tuple[int, str]:
    if not value:
        return (1, "9999-12-31T23:59:59+00:00")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return (0, value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (0, parsed.astimezone(timezone.utc).isoformat())

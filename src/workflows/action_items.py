from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any

from src.workflows.planning_types import PlanningWindow, StructuredWatchActionItem


_TIME_PATTERN = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)
_MONTH_DAY_PATTERN = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})\b",
    re.IGNORECASE,
)
_ISO_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")

_MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_WEEKDAY_MAP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def normalize_structured_watch(
    structured_watch: dict[str, Any] | None,
    *,
    upcoming_events: list[dict[str, Any]] | None = None,
    reference_dt: datetime | None = None,
) -> dict[str, Any]:
    structured_watch = dict(structured_watch or {})
    now = (reference_dt or datetime.now().astimezone()).astimezone()
    events = list(upcoming_events or [])

    asks = [
        _normalize_action_item("ask", item, upcoming_events=events, reference_dt=now)
        for item in (structured_watch.get("asks", []) or [])
        if isinstance(item, dict) and item.get("ask")
    ]
    deadlines = [
        _normalize_action_item("deadline", item, upcoming_events=events, reference_dt=now)
        for item in (structured_watch.get("deadlines", []) or [])
        if isinstance(item, dict) and item.get("deadline")
    ]

    structured_watch["asks"] = asks
    structured_watch["deadlines"] = deadlines
    return structured_watch


def filter_structured_watch_for_window(
    structured_watch: dict[str, Any] | None,
    *,
    planning_window: PlanningWindow,
) -> dict[str, Any]:
    structured_watch = dict(structured_watch or {})
    filtered = dict(structured_watch)
    filtered["asks"] = _filter_action_items(structured_watch.get("asks", []) or [], planning_window=planning_window)
    filtered["deadlines"] = _filter_action_items(structured_watch.get("deadlines", []) or [], planning_window=planning_window)
    return filtered


def action_item_text(item: dict[str, Any], *, kind: str) -> str:
    key = "ask" if kind == "ask" else "deadline"
    return str(item.get(key) or item.get("text") or "").strip()


def unresolved_action_items(structured_watch: dict[str, Any] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("asks", "deadlines"):
        for item in (dict(structured_watch or {}).get(key, []) or []):
            if isinstance(item, dict) and item.get("inference_kind") == "unresolved":
                items.append(item)
    return items


def _filter_action_items(items: list[dict[str, Any]], *, planning_window: PlanningWindow) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        due_at = _parse_datetime(item.get("due_at"))
        due_date = _parse_date(item.get("due_date"))
        effective_date = due_at.date() if due_at else due_date
        if effective_date is None:
            continue
        if planning_window.start_date <= effective_date <= planning_window.end_date:
            filtered.append(item)
    return filtered


def _normalize_action_item(
    kind: str,
    item: dict[str, Any],
    *,
    upcoming_events: list[dict[str, Any]],
    reference_dt: datetime,
) -> dict[str, Any]:
    text = action_item_text(item, kind=kind)
    normalized = StructuredWatchActionItem(
        kind=kind, text=text, source_thread_id=str(item.get("thread_id") or "") or None, owner=item.get("owner")
    )
    resolved = _resolve_due_signal(text, upcoming_events=upcoming_events, reference_dt=reference_dt)
    payload = normalized.model_dump(mode="json")
    payload.update(item)
    payload["text"] = text
    payload["confidence"] = resolved["confidence"]
    payload["inference_kind"] = resolved["inference_kind"]
    payload["unresolved_reason"] = resolved.get("unresolved_reason")
    payload["time_window"] = resolved.get("time_window")
    payload["related_event_id"] = resolved.get("related_event_id")
    payload["related_event_title"] = resolved.get("related_event_title")
    payload["due_at"] = resolved.get("due_at")
    payload["due_date"] = resolved.get("due_date")
    payload["ask" if kind == "ask" else "deadline"] = text
    return payload


def _resolve_due_signal(
    text: str,
    *,
    upcoming_events: list[dict[str, Any]],
    reference_dt: datetime,
) -> dict[str, Any]:
    lowered = str(text or "").lower()

    if iso_match := _ISO_DATE_PATTERN.search(text):
        due_date = date.fromisoformat(iso_match.group(1))
        due_at = _combine_with_time(due_date, _extract_time(text), reference_dt=reference_dt)
        return _resolved(
            due_at=due_at,
            due_date=due_date.isoformat(),
            confidence=0.95 if due_at else 0.9,
            inference_kind="explicit",
            time_window=_time_window_label(text),
        )

    if relative := _resolve_relative_phrase(lowered, reference_dt=reference_dt):
        due_date = relative.date()
        return _resolved(
            due_at=relative.isoformat(),
            due_date=due_date.isoformat(),
            confidence=0.88,
            inference_kind="derived_relative_time",
            time_window=_time_window_label(text),
        )

    if event_based := _resolve_event_phrase(text, upcoming_events=upcoming_events, reference_dt=reference_dt):
        return event_based

    if month_day_match := _MONTH_DAY_PATTERN.search(text):
        month = _MONTH_MAP[month_day_match.group(1)[:3].lower()]
        day = int(month_day_match.group(2))
        year = reference_dt.date().year
        due_date = date(year, month, day)
        if due_date < reference_dt.date():
            due_date = date(year + 1, month, day)
        due_at = _combine_with_time(due_date, _extract_time(text), reference_dt=reference_dt)
        return _resolved(
            due_at=due_at,
            due_date=due_date.isoformat(),
            confidence=0.84 if due_at else 0.8,
            inference_kind="explicit",
            time_window=_time_window_label(text),
        )

    if slash_match := re.search(r"\b(\d{1,2})/(\d{1,2})\b", text):
        month, day = int(slash_match.group(1)), int(slash_match.group(2))
        due_date = date(reference_dt.date().year, month, day)
        if due_date < reference_dt.date():
            due_date = date(reference_dt.date().year + 1, month, day)
        return _resolved(
            due_date=due_date.isoformat(),
            due_at=_combine_with_time(due_date, _extract_time(text), reference_dt=reference_dt),
            confidence=0.8,
            inference_kind="explicit",
            time_window=_time_window_label(text),
        )

    return {
        "confidence": 0.3,
        "inference_kind": "unresolved",
        "unresolved_reason": "Could not resolve a concrete date from the item text.",
        "time_window": _time_window_label(text),
    }


def _resolve_relative_phrase(lowered: str, *, reference_dt: datetime) -> datetime | None:
    target_date: date | None = None
    today = reference_dt.date()
    if "today" in lowered:
        target_date = today
    elif "tomorrow" in lowered:
        target_date = today + timedelta(days=1)
    elif "this week" in lowered:
        target_date = today + timedelta(days=max(0, 4 - today.weekday()))
    elif "next week" in lowered:
        monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
        target_date = monday + timedelta(days=4)
    else:
        for name, weekday in _WEEKDAY_MAP.items():
            if name in lowered:
                days_ahead = (weekday - today.weekday()) % 7
                if "next" in lowered and days_ahead == 0:
                    days_ahead = 7
                target_date = today + timedelta(days=days_ahead)
                break
    if target_date is None:
        return None
    return datetime.combine(target_date, _extract_time(lowered) or time(17, 0), tzinfo=reference_dt.tzinfo)


def _resolve_event_phrase(
    text: str,
    *,
    upcoming_events: list[dict[str, Any]],
    reference_dt: datetime,
) -> dict[str, Any] | None:
    lowered = text.lower()
    when = _extract_time(lowered)
    if "before" not in lowered and "by" not in lowered:
        return None

    related_event = _match_event_from_text(lowered, upcoming_events)
    if related_event:
        event_start = _parse_datetime(related_event.get("starts_at"))
        if event_start:
            due_date = event_start.date()
            due_time = when or event_start.timetz().replace(tzinfo=None)
            due_at = datetime.combine(due_date, due_time, tzinfo=event_start.tzinfo or reference_dt.tzinfo)
            return _resolved(
                due_at=due_at.isoformat(),
                due_date=due_date.isoformat(),
                confidence=0.86 if when else 0.8,
                inference_kind="derived_from_event",
                time_window=_time_window_label(text),
                related_event_id=str(related_event.get("meeting_id") or "") or None,
                related_event_title=str(related_event.get("title") or "") or None,
            )

    if when:
        due_at = datetime.combine(reference_dt.date(), when, tzinfo=reference_dt.tzinfo)
        return _resolved(
            due_at=due_at.isoformat(),
            due_date=reference_dt.date().isoformat(),
            confidence=0.72,
            inference_kind="derived_relative_time",
            time_window=_time_window_label(text),
        )
    return None


def _match_event_from_text(text: str, upcoming_events: list[dict[str, Any]]) -> dict[str, Any] | None:
    meaningful_tokens = [token for token in re.findall(r"[a-z]+", text.lower()) if token not in {"before", "by", "the", "a", "an", "pm", "am", "today", "tomorrow"}]
    if not meaningful_tokens:
        return None
    best: dict[str, Any] | None = None
    best_score = 0
    for event in upcoming_events:
        title = str(event.get("title") or "").lower()
        if not title:
            continue
        score = sum(1 for token in meaningful_tokens if token in title)
        if score > best_score:
            best = event
            best_score = score
    return best if best_score >= 2 else None


def _extract_time(text: str) -> time | None:
    lowered = str(text or "").lower()
    if "noon" in lowered:
        return time(12, 0)
    if "eod" in lowered or "end of day" in lowered:
        return time(17, 0)
    match = _TIME_PATTERN.search(lowered)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3).lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return time(hour, minute)


def _combine_with_time(target_date: date, value: time | None, *, reference_dt: datetime) -> str | None:
    if value is None:
        return None
    return datetime.combine(target_date, value, tzinfo=reference_dt.tzinfo).isoformat()


def _time_window_label(text: str) -> str | None:
    lowered = str(text or "").lower()
    if "before" in lowered:
        return "before"
    if "by" in lowered:
        return "by"
    return None


def _resolved(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.astimezone()
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None

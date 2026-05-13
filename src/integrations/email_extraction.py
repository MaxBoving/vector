from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from src.workflows.action_items import normalize_structured_watch


ASK_PATTERNS = [
    re.compile(r"\b(?:please|can you|could you|need you to|action required|requesting that you)\b([^.!?\n]{0,180})", re.IGNORECASE),
]
DEADLINE_PATTERNS = [
    re.compile(r"\bby (monday|tuesday|wednesday|thursday|friday|eod|end of day|tomorrow|next week|this week|[A-Z][a-z]{2,8}\s+\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(deadline|due|before)\s*[:\-]?\s*([^.!?\n]{1,80})", re.IGNORECASE),
]
DOC_PATTERNS = [
    ("board memo", "board memo"),
    ("memo", "memo"),
    ("report", "report"),
    ("deck", "slide deck"),
    ("slides", "slide deck"),
    ("brief", "brief"),
    ("doc", "document"),
    ("update", "update"),
]


def cross_reference_threads_with_calendar(
    ranked_threads: Iterable[Dict[str, Any]],
    upcoming_events: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    events = list(upcoming_events)
    related_thread_ids: set[str] = set()
    for event in events:
        for thread in event.get("related_threads", []) or []:
            if not isinstance(thread, dict):
                continue
            thread_id = str(thread.get("thread_id") or thread.get("id") or "").strip()
            if thread_id:
                related_thread_ids.add(thread_id)

    enriched: List[Dict[str, Any]] = []
    for thread in ranked_threads:
        score_delta = 0
        reasons = list(thread.get("importance_reasons", []))
        matches: List[Dict[str, Any]] = []
        thread_id = str(thread.get("thread_id") or thread.get("id") or "").strip()
        if thread_id and thread_id in related_thread_ids:
            for event in events:
                for related_thread in event.get("related_threads", []) or []:
                    if not isinstance(related_thread, dict):
                        continue
                    related_thread_id = str(related_thread.get("thread_id") or related_thread.get("id") or "").strip()
                    if related_thread_id == thread_id:
                        matches.append({"meeting_id": event.get("meeting_id"), "title": event.get("title"), "match": "related_thread"})
                        score_delta += 12
                        break
        enriched_thread = dict(thread)
        if matches:
            reasons.append("Cross-references an upcoming calendar event.")
            enriched_thread["calendar_matches"] = matches
            enriched_thread["importance_score"] = int(enriched_thread.get("importance_score", 0)) + score_delta
            enriched_thread["importance_level"] = _importance_level_from_score(enriched_thread["importance_score"])
            enriched_thread["importance_reasons"] = _dedupe(reasons)[:5]
        enriched.append(enriched_thread)
    enriched.sort(key=lambda item: item.get("importance_score", 0), reverse=True)
    return enriched


def extract_structured_watch_items(
    ranked_threads: Iterable[Dict[str, Any]],
    upcoming_events: Iterable[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    asks: List[Dict[str, Any]] = []
    owners: List[Dict[str, Any]] = []
    deadlines: List[Dict[str, Any]] = []
    implied_meetings: List[Dict[str, Any]] = []
    implied_docs: List[Dict[str, Any]] = []

    events = list(upcoming_events)
    for thread in ranked_threads:
        if thread.get("suppressed") or thread.get("importance_level") == "low":
            continue
        subject = str(thread.get("subject") or "Inbox thread")
        sender = str(thread.get("latest_sender") or "Unknown sender")
        thread_id = str(thread.get("thread_id") or "")
        message_text = _thread_text(thread)

        for ask in _extract_asks(message_text):
            asks.append({"thread_id": thread_id, "subject": subject, "ask": ask, "owner": "CEO", "sender": sender})
            owners.append({"thread_id": thread_id, "subject": subject, "owner": "CEO", "reason": ask})

        for deadline in _extract_deadlines(message_text):
            deadlines.append({"thread_id": thread_id, "subject": subject, "deadline": deadline, "owner": "CEO"})

        for doc_name in _extract_docs(message_text):
            implied_docs.append({"thread_id": thread_id, "subject": subject, "document": doc_name, "owner": "CEO"})

        for match in thread.get("calendar_matches", []) or []:
            implied_meetings.append(
                {
                    "thread_id": thread_id,
                    "subject": subject,
                    "meeting": match.get("title"),
                    "meeting_id": match.get("meeting_id"),
                    "evidence": f"Cross-referenced by {match.get('match') or 'calendar reference'}.",
                }
            )

    return normalize_structured_watch(
        {
        "asks": _dedupe_dicts(asks, key="ask"),
        "owners": _dedupe_dicts(owners, key="reason"),
        "deadlines": _dedupe_dicts(deadlines, key="deadline"),
        "implied_meetings": _dedupe_dicts(implied_meetings, key="meeting"),
        "implied_docs": _dedupe_dicts(implied_docs, key="document"),
        },
        upcoming_events=events,
    )


def _thread_text(thread: Dict[str, Any]) -> str:
    return "\n".join(
        [
            str(thread.get("subject") or ""),
            str(thread.get("snippet") or ""),
            *[
                str(message.get("body_preview") or "")
                for message in thread.get("messages", [])
                if isinstance(message, dict)
            ],
        ]
    )


def _extract_asks(text: str) -> List[str]:
    results: List[str] = []
    for pattern in ASK_PATTERNS:
        for match in pattern.findall(text):
            value = _clean_fragment(match if isinstance(match, str) else " ".join(match))
            if value and len(value.split()) >= 3 and len(value) <= 120:
                results.append(value)
    return _dedupe(results)[:4]


def _extract_deadlines(text: str) -> List[str]:
    results: List[str] = []
    for pattern in DEADLINE_PATTERNS:
        for match in pattern.findall(text):
            if isinstance(match, tuple):
                value = next((part for part in reversed(match) if part), "")
            else:
                value = match
            cleaned = _clean_fragment(value)
            if cleaned and _looks_like_real_deadline(cleaned):
                results.append(cleaned)
    return _dedupe(results)[:4]


def _extract_docs(text: str) -> List[str]:
    lowered = text.lower()
    found = [label for marker, label in DOC_PATTERNS if marker in lowered]
    return _dedupe(found)[:4]


def _clean_fragment(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" :-.,;\n\t")


def _looks_like_real_deadline(value: str) -> bool:
    lowered = value.lower()
    if len(lowered) < 3 or len(lowered) > 40:
        return False
    if any(noise in lowered for noise in ("unsubscribe", "review the", "starting", "you get started", "options")):
        return False
    return any(
        marker in lowered
        for marker in ("today", "tomorrow", "next week", "this week", "monday", "tuesday", "wednesday", "thursday", "friday", "dec", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov")
    ) or bool(re.search(r"\b\d{1,2}/\d{1,2}\b", lowered))


def _dedupe(values: List[str]) -> List[str]:
    seen: set[str] = set()
    output: List[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        output.append(normalized)
    return output


def _dedupe_dicts(items: List[Dict[str, Any]], *, key: str) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    output: List[Dict[str, Any]] = []
    for item in items:
        value = str(item.get(key) or "").strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(item)
    return output[:6]


def _importance_level_from_score(score: int) -> str:
    if score >= 55:
        return "high"
    if score >= 28:
        return "medium"
    return "low"

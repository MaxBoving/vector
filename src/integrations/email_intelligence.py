from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List

_INTEL_MODEL = (
    os.getenv("EMAIL_INTEL_LLM_MODEL")
    or os.getenv("ANTHROPIC_SIMPLE_MODEL")
    or os.getenv("ANTHROPIC_MODEL")
    or "claude-3-haiku-20240307"
)
_LEVEL_BASE: dict[str, int] = {"high": 80, "medium": 45, "low": 10}


IMPORTANT_SENDER_HINTS = (
    "board",
    "investor",
    "legal",
    "finance",
    "cfo",
    "ceo",
    "founder",
    "partner",
)

LOW_TRUST_DOMAIN_MARKERS = (
    "sofi",
    "coinbase",
    "crypto",
    "joinrs",
    "student",
    "college",
    "bonus",
    "marketing",
    "mailer",
    "newsletter",
)

PROMOTIONAL_SUBJECT_MARKERS = (
    "unsubscribe",
    "view in browser",
    "bonus",
    "promo",
    "offer",
    "students",
    "crypto",
    "job alert",
    "intern",
    "hiring now",
    "you've got",
    "icymi",
)

IMPORTANT_KEYWORDS: dict[str, tuple[str, int, str]] = {
    "urgent": ("operations", 18, "Contains urgency language."),
    "asap": ("operations", 18, "Contains urgency language."),
    "deadline": ("operations", 16, "Mentions a deadline."),
    "approve": ("internal_exec", 18, "Requests approval."),
    "approval": ("internal_exec", 18, "Requests approval."),
    "board": ("board", 22, "Mentions the board."),
    "investor": ("investor", 22, "Mentions investors."),
    "customer": ("customer", 16, "Mentions a customer matter."),
    "contract": ("legal", 18, "Mentions a contract."),
    "legal": ("legal", 18, "Mentions a legal matter."),
    "renewal": ("customer", 14, "Mentions a renewal."),
    "budget": ("finance", 16, "Mentions budget impact."),
    "forecast": ("finance", 16, "Mentions a forecast."),
    "spend": ("finance", 14, "Mentions spend."),
    "invoice": ("finance", 14, "Mentions invoicing."),
    "meeting": ("internal_exec", 12, "Mentions a meeting."),
    "reschedule": ("internal_exec", 18, "Suggests a schedule change."),
    "next week": ("internal_exec", 12, "Mentions next week timing."),
    "deliverable": ("operations", 15, "Mentions a deliverable."),
    "follow up": ("operations", 12, "Requests follow-up."),
    "action required": ("operations", 20, "Indicates action required."),
}


def rank_email_threads(
    threads: Iterable[Dict[str, Any]],
    *,
    preferences: Dict[str, Any] | None = None,
    upcoming_events: Iterable[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    thread_list = list(threads)
    sender_counts = Counter(_normalize_sender_key(thread.get("latest_sender")) for thread in thread_list if _normalize_sender_key(thread.get("latest_sender")))
    domain_counts = Counter(_extract_email_domain(str(thread.get("latest_sender") or "")) for thread in thread_list if _extract_email_domain(str(thread.get("latest_sender") or "")))
    attendee_keys = {
        _normalize_sender_key(attendee)
        for event in (upcoming_events or [])
        for attendee in (event.get("attendees", []) or [])
        if _normalize_sender_key(attendee)
    }
    ranked: List[Dict[str, Any]] = []
    for thread in thread_list:
        ranked.append(
            score_email_thread(
                thread,
                preferences=preferences,
                sender_counts=sender_counts,
                domain_counts=domain_counts,
                attendee_keys=attendee_keys,
            )
        )
    ranked.sort(
        key=lambda item: (
            item.get("importance_score", 0),
            _coerce_datetime(item.get("latest_received_at")),
        ),
        reverse=True,
    )
    return ranked


def score_email_thread(
    thread: Dict[str, Any],
    *,
    preferences: Dict[str, Any] | None = None,
    sender_counts: Counter[str] | None = None,
    domain_counts: Counter[str] | None = None,
    attendee_keys: set[str] | None = None,
) -> Dict[str, Any]:
    subject = str(thread.get("subject") or "")
    participants = [str(value) for value in thread.get("participants", []) if value]
    latest_sender = str(thread.get("latest_sender") or "")
    sender_key = _normalize_sender_key(latest_sender)
    content = " ".join(
        str(message.get("body_preview") or "")
        for message in thread.get("messages", [])
        if isinstance(message, dict)
    )
    text = " ".join([subject, latest_sender, " ".join(participants), content]).lower()
    sender_domain = _extract_email_domain(latest_sender)

    score = 0
    reasons: List[str] = []
    category = "operations"
    suppressed = False
    preferences = preferences or {}
    priority_senders = set(preferences.get("priority_senders", []) or [])
    priority_domains = set(preferences.get("priority_domains", []) or [])
    ignored_senders = set(preferences.get("ignored_senders", []) or [])
    ignored_domains = set(preferences.get("ignored_domains", []) or [])

    if sender_key and sender_key in priority_senders:
        score += 42
        reasons.append("Sender is explicitly prioritized by the CEO.")

    if sender_domain and sender_domain in priority_domains:
        score += 28
        reasons.append("Sender domain is explicitly prioritized by the CEO.")

    if sender_key and sender_key in ignored_senders:
        score -= 100
        reasons.append("Sender is explicitly ignored by the CEO.")
        category = "ignored"
        suppressed = True

    if sender_domain and sender_domain in ignored_domains:
        score -= 100
        reasons.append("Sender domain is explicitly ignored by the CEO.")
        category = "ignored"
        suppressed = True

    if any(hint in latest_sender.lower() for hint in IMPORTANT_SENDER_HINTS):
        score += 18
        reasons.append("Sender appears to be a high-priority stakeholder.")

    participant_blob = " ".join(participants).lower()
    if any(hint in participant_blob for hint in IMPORTANT_SENDER_HINTS):
        score += 10
        reasons.append("Participants include likely executive stakeholders.")

    if _looks_promotional(subject, content, latest_sender, sender_domain):
        score -= 45
        reasons.append("Looks like promotional or bulk mail.")
        category = "promotional"
        suppressed = True

    if sender_domain and _is_low_trust_domain(sender_domain):
        score -= 22
        reasons.append("Sender domain looks low priority.")
        if category == "operations":
            category = "promotional"

    if sender_domain and _is_worklike_domain(sender_domain):
        score += 12
        reasons.append("Sender domain looks like a direct contact or work account.")

    if sender_key and sender_counts and sender_counts.get(sender_key, 0) >= 2:
        score += 10
        reasons.append("Sender appears repeatedly in the recent watch window.")

    if sender_domain and domain_counts and domain_counts.get(sender_domain, 0) >= 2:
        score += 6
        reasons.append("Sender domain appears repeatedly in the recent watch window.")

    if sender_key and attendee_keys and sender_key in attendee_keys:
        score += 12
        reasons.append("Sender is also on the calendar in the current watch window.")

    for keyword, (keyword_category, delta, reason) in IMPORTANT_KEYWORDS.items():
        if keyword in text:
            score += delta
            reasons.append(reason)
            if category == "operations":
                category = keyword_category

    message_count = int(thread.get("message_count") or len(thread.get("messages", [])) or 1)
    if message_count >= 3:
        score += min(message_count * 2, 10)
        reasons.append("Multiple recent messages indicate an active thread.")

    latest_received_at = _coerce_datetime(thread.get("latest_received_at"))
    if latest_received_at:
        age_hours = max((datetime.utcnow() - latest_received_at).total_seconds() / 3600, 0)
        if age_hours <= 12:
            score += 10
            reasons.append("Thread is recent.")
        elif age_hours <= 48:
            score += 5

    importance_level = "low"
    if score >= 55:
        importance_level = "high"
    elif score >= 28:
        importance_level = "medium"
    if suppressed and score < 40:
        importance_level = "low"

    ranked = dict(thread)
    ranked["importance_score"] = max(score, 0)
    ranked["importance_level"] = importance_level
    ranked["importance_reasons"] = _dedupe(reasons)[:4]
    ranked["category"] = category
    ranked["suppressed"] = suppressed
    ranked["sender_domain"] = sender_domain
    ranked["sender_key"] = sender_key
    return ranked


def select_primary_thread(ranked_threads: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    for thread in ranked_threads:
        if thread.get("importance_level") in {"high", "medium"} and not thread.get("suppressed"):
            return thread
    for thread in ranked_threads:
        if not thread.get("suppressed") and thread.get("category") != "promotional":
            return thread
    for thread in ranked_threads:
        if not thread.get("suppressed"):
            return thread
    return ranked_threads[0] if ranked_threads else None


async def async_rerank_threads(
    ranked_threads: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    LLM-enhanced re-ranking of already keyword-scored threads.

    Runs one batched Haiku call on non-suppressed threads for semantic scoring,
    then merges the LLM importance level with the deterministic bonuses already
    on each thread.  Falls back to returning the list unchanged on any failure.
    """
    eligible = [t for t in ranked_threads if not t.get("suppressed")]
    # Also include suppressed threads so the LLM can overturn false-positive spam classification
    suppressed = [t for t in ranked_threads if t.get("suppressed")]
    candidates = eligible + suppressed
    if not candidates:
        return ranked_threads

    llm_results = await _llm_score_threads(candidates)
    if not llm_results:
        return ranked_threads

    llm_by_id: dict[str, dict] = {}
    for r in llm_results:
        if isinstance(r, dict) and "thread_id" in r:
            llm_by_id[str(r["thread_id"])] = r

    merged: List[Dict[str, Any]] = []
    for thread in ranked_threads:
        tid = str(thread.get("thread_id", thread.get("id", "")))
        llm = llm_by_id.get(tid)
        if llm:
            thread = dict(thread)
            # LLM can unsuppress a thread the keyword heuristic wrongly flagged
            if thread.get("suppressed") and llm.get("spam_likely") is False:
                thread["suppressed"] = False
                thread["importance_reasons"] = (
                    ["Restored by semantic review — not promotional."]
                    + list(thread.get("importance_reasons") or [])[:2]
                )
            if not thread.get("suppressed"):
                llm_level = str(llm.get("importance_level", "")).lower()
                base = _LEVEL_BASE.get(llm_level)
                if base is not None:
                    # Keep structural bonuses (priority sender, recency, attendee match) capped
                    det_bonus = min(int(thread.get("importance_score", 0)), 30)
                    thread["importance_score"] = base + det_bonus
                    thread["importance_level"] = llm_level
                    if llm.get("category"):
                        thread["category"] = llm["category"]
                    if llm.get("action_required") is not None:
                        thread["action_required"] = bool(llm["action_required"])
                    reasons = list(thread.get("importance_reasons") or [])
                    if llm.get("reason"):
                        thread["importance_reasons"] = [llm["reason"]] + reasons[:2]
        merged.append(thread)

    merged.sort(
        key=lambda t: (
            t.get("importance_score", 0),
            _coerce_datetime(t.get("latest_received_at")) or datetime.min,
        ),
        reverse=True,
    )
    return merged


async def _llm_score_threads(threads: List[Dict[str, Any]]) -> List[dict] | None:
    """One batched Haiku call for semantic email importance scoring."""
    try:
        from src.core.llm import LLMClient  # local import to avoid circular deps

        llm = LLMClient(model=_INTEL_MODEL)
        if not llm.anthropic_async and not llm.openai_async:
            return None

        batch = [
            {
                "thread_id": str(t.get("thread_id", t.get("id", ""))),
                "subject": str(t.get("subject", ""))[:120],
                "sender": str(t.get("latest_sender", ""))[:80],
                "snippet": str(t.get("snippet") or t.get("latest_message_body", ""))[:200],
                "message_count": int(t.get("message_count") or 1),
            }
            for t in threads
        ]

        system = (
            "You are an executive inbox prioritizer for a CEO. "
            "Classify each email thread by importance based on business context and sender signals. "
            "Return ONLY a JSON array — no explanation, no wrapper keys. "
            'Each element: {"thread_id": str, "importance_level": "high"|"medium"|"low", '
            '"category": "board"|"investor"|"legal"|"finance"|"customer"|"internal_exec"|"operations"|"promotional", '
            '"action_required": bool, "spam_likely": bool, "reason": "one sentence"}. '
            "Set spam_likely=true only for marketing emails, newsletters, job alerts, or bulk mail — "
            "not for legitimate business emails that happen to contain promotional language."
        )
        prompt = f"Classify these {len(batch)} email threads:\n{json.dumps(batch, indent=2)}"

        raw = await llm.complete_async(prompt, system)

        match = re.search(r"\[[\s\S]*\]", raw)
        if match:
            return json.loads(match.group(0))

        print("[EmailIntelligence] LLM response contained no JSON array — falling back")
        return None
    except Exception as exc:
        print(f"[EmailIntelligence] LLM scoring failed: {exc}")
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        return parsed.replace(tzinfo=None)
    except Exception:
        return None


def _dedupe(values: List[str]) -> List[str]:
    seen: set[str] = set()
    deduped: List[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _extract_email_domain(value: str) -> str:
    match = re.search(r"([A-Z0-9._%+-]+)@([A-Z0-9.-]+\.[A-Z]{2,})", value, re.IGNORECASE)
    return match.group(2).lower() if match else ""


def _extract_email_address(value: str) -> str:
    match = re.search(r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", value, re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _normalize_sender_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _extract_email_address(text) or text


def _looks_promotional(subject: str, content: str, sender: str, sender_domain: str) -> bool:
    lowered_subject = subject.lower()
    lowered_content = content.lower()
    lowered_sender = sender.lower()
    if any(marker in lowered_subject for marker in PROMOTIONAL_SUBJECT_MARKERS):
        return True
    if "unsubscribe" in lowered_content or "manage preferences" in lowered_content:
        return True
    if "view in browser" in lowered_content or "limited time" in lowered_content:
        return True
    if sender_domain and _is_low_trust_domain(sender_domain):
        return True
    return any(marker in lowered_sender for marker in ("noreply", "no-reply", "mailer"))


def _is_low_trust_domain(domain: str) -> bool:
    return any(marker in domain for marker in LOW_TRUST_DOMAIN_MARKERS)


def _is_worklike_domain(domain: str) -> bool:
    return not any(
        consumer in domain
        for consumer in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com")
    )

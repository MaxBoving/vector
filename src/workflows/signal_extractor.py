"""Signal normalization layer — data-context-extractor pattern.

Converts raw connector payloads (email threads, calendar events, finance docs,
operating signals) into typed ExtractedSignal objects before they reach agents.
This makes signal handling deterministic and prevents agents from having to
guess what fields exist on arbitrary dicts.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

_SIGNAL_MODEL = (
    os.getenv("SIGNAL_LLM_MODEL")
    or os.getenv("ANTHROPIC_SIMPLE_MODEL")
    or os.getenv("ANTHROPIC_MODEL")
    or "claude-3-haiku-20240307"
)
_URGENCY_RANK = {"critical": 0, "high": 1, "normal": 2, "low": 3}
_VALID_URGENCY = frozenset({"critical", "high", "normal", "low"})


# ---------------------------------------------------------------------------
# Typed output model
# ---------------------------------------------------------------------------

class ExtractedSignal(BaseModel):
    """Normalized representation of any connector signal."""

    signal_id: str = ""
    source: str = "unknown"           # email | calendar | finance | internal | webhook
    signal_type: str = "general"      # thread | event | metric | alert | document | update
    urgency: str = "normal"           # critical | high | normal | low
    title: str = ""
    summary: str = ""
    entities: List[str] = Field(default_factory=list)   # people, orgs, products mentioned
    key_facts: List[str] = Field(default_factory=list)  # extracted atomic facts
    action_required: bool = False
    action_hint: str = ""
    domains: List[str] = Field(default_factory=list)    # finance | legal | ops | hr | product
    raw: Dict[str, Any] = Field(default_factory=dict)   # preserved original for debugging


# ---------------------------------------------------------------------------
# Urgency classification
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Provisional regex classifiers
# These run synchronously as a fast first pass. All results are marked
# provisional and should be overridden by async_enrich_signals / async_normalize_and_enrich
# when the async path is available.
# ---------------------------------------------------------------------------

_CRITICAL_PATTERNS = re.compile(
    r'\b(urgent|critical|asap|escalat|breach|overdue|missed|failed|lawsuit|litigation|sec|compliance|fraud|incident)\b',
    re.IGNORECASE,
)
_HIGH_PATTERNS = re.compile(
    r'\b(board|investor|legal|approve|sign.?off|deadline|contract|budget.?exceeded|over.?budget|runway|wire|payment)\b',
    re.IGNORECASE,
)
_LOW_PATTERNS = re.compile(
    r'\b(fyi|no.?action|newsletter|automated|subscription|unsubscribe|confirm.?receipt|read.?only)\b',
    re.IGNORECASE,
)

_DOMAIN_PATTERNS: List[tuple[str, re.Pattern]] = [
    ("finance", re.compile(r'\b(revenue|budget|burn|cash|runway|invoice|payment|p&l|ebitda|arr|mrr|opex|capex|cost)\b', re.IGNORECASE)),
    ("legal", re.compile(r'\b(contract|legal|counsel|litigation|compliance|gdpr|sec|nda|ip|intellectual property|agreement|clause)\b', re.IGNORECASE)),
    ("hr", re.compile(r'\b(hiring|headcount|offer|resignation|equity|compensation|performance|pip|onboard)\b', re.IGNORECASE)),
    ("product", re.compile(r'\b(launch|feature|roadmap|release|milestone|sprint|demo|beta|production|deploy)\b', re.IGNORECASE)),
    ("ops", re.compile(r'\b(incident|outage|latency|sla|vendor|infra|cloud|aws|gcp|azure|downtime|alert)\b', re.IGNORECASE)),
]


def _classify_urgency(text: str) -> str:
    """Provisional urgency — regex only. Override with async_enrich_signals."""
    if _CRITICAL_PATTERNS.search(text):
        return "critical"
    if _HIGH_PATTERNS.search(text):
        return "high"
    if _LOW_PATTERNS.search(text):
        return "low"
    return "normal"


def _classify_domains(text: str) -> List[str]:
    """Provisional domain list — regex only. Override with async_enrich_signals."""
    return [domain for domain, pattern in _DOMAIN_PATTERNS if pattern.search(text)]


def _extract_entities(text: str, known_people: List[str] | None = None) -> List[str]:
    """Extract capitalized proper noun candidates (very lightweight)."""
    candidates: List[str] = []
    for match in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text):
        name = match.group(1)
        if name not in candidates:
            candidates.append(name)
    if known_people:
        for person in known_people:
            if person in text and person not in candidates:
                candidates.append(person)
    return candidates[:8]


def _extract_key_facts(text: str, max_facts: int = 5) -> List[str]:
    """Extract sentences that contain numbers, monetary values, or modal verbs."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    facts: List[str] = []
    fact_patterns = re.compile(
        r'(\$[\d,.]+|\d+%|\d+\s*(month|week|day|year|hour)s?|must|need|require|approve|sign|urgent)',
        re.IGNORECASE,
    )
    for sentence in sentences:
        if fact_patterns.search(sentence) and len(sentence) > 20:
            facts.append(sentence.strip()[:200])
        if len(facts) >= max_facts:
            break
    return facts


# ---------------------------------------------------------------------------
# Per-source extractors
# ---------------------------------------------------------------------------

def extract_from_email_thread(thread: Dict[str, Any]) -> ExtractedSignal:
    """Normalize an email thread dict into an ExtractedSignal."""
    subject = str(thread.get("subject", ""))
    snippet = str(thread.get("snippet", "") or thread.get("latest_message_body", "") or "")
    sender = str(thread.get("latest_sender", "") or thread.get("sender", ""))
    importance = str(thread.get("importance_level", "normal")).lower()
    importance_reasons = thread.get("importance_reasons", []) or []

    full_text = f"{subject} {snippet} {' '.join(importance_reasons)}"
    urgency = _classify_urgency(full_text)
    if importance in {"high", "critical"}:
        urgency = max(urgency, importance, key=lambda x: {"critical": 3, "high": 2, "normal": 1, "low": 0}.get(x, 1))

    category = str(thread.get("category", ""))
    action_required = thread.get("action_required", False) or urgency in {"critical", "high"}
    action_hint = str(thread.get("suggested_action", "") or thread.get("next_step", ""))

    return ExtractedSignal(
        signal_id=str(thread.get("thread_id", thread.get("id", ""))),
        source="email",
        signal_type="thread",
        urgency=urgency,
        title=subject or f"Email from {sender}",
        summary=snippet[:300],
        entities=_extract_entities(full_text, [sender] if sender else None),
        key_facts=_extract_key_facts(full_text),
        action_required=action_required,
        action_hint=action_hint,
        domains=_classify_domains(full_text) or ([category] if category else []),
        raw=thread,
    )


def extract_from_calendar_event(event: Dict[str, Any]) -> ExtractedSignal:
    """Normalize a calendar event dict into an ExtractedSignal."""
    title = str(event.get("title", event.get("summary", "")))
    description = str(event.get("description", ""))
    attendees_raw = event.get("attendees", []) or []
    attendee_names = [
        str(a.get("displayName", a.get("email", a))) if isinstance(a, dict) else str(a)
        for a in attendees_raw[:8]
    ]
    starts_at = str(event.get("start", event.get("starts_at", "")))

    full_text = f"{title} {description}"
    urgency = _classify_urgency(full_text)

    # Board/investor/legal meetings are always high priority
    if re.search(r'\b(board|investor|legal|fundrais|due diligence|audit)\b', title, re.IGNORECASE):
        urgency = "high" if urgency == "normal" else urgency

    needs_prep = bool(re.search(r'\b(board|investor|demo|present|pitch|review|debrief)\b', title, re.IGNORECASE))

    return ExtractedSignal(
        signal_id=str(event.get("id", event.get("event_id", ""))),
        source="calendar",
        signal_type="event",
        urgency=urgency,
        title=title,
        summary=f"{starts_at}: {title} — {len(attendee_names)} attendees" if attendee_names else f"{starts_at}: {title}",
        entities=attendee_names,
        key_facts=[f"Starts: {starts_at}"] + ([f"Prep required: yes"] if needs_prep else []),
        action_required=needs_prep,
        action_hint="Prepare talking points and review relevant documents before this meeting." if needs_prep else "",
        domains=_classify_domains(full_text),
        raw=event,
    )


def extract_from_finance_doc(doc: Dict[str, Any]) -> ExtractedSignal:
    """Normalize a finance document signal dict into an ExtractedSignal."""
    title = str(doc.get("title", "Finance Document"))
    content = str(doc.get("content", doc.get("summary", "")))
    purpose = str(doc.get("purpose", "reference"))
    domains = doc.get("domains", ["finance"]) or ["finance"]

    full_text = f"{title} {content}"
    urgency = _classify_urgency(full_text)

    return ExtractedSignal(
        signal_id=str(doc.get("document_id", doc.get("id", ""))),
        source="finance",
        signal_type="document",
        urgency=urgency,
        title=title,
        summary=content[:300],
        entities=_extract_entities(full_text),
        key_facts=_extract_key_facts(full_text),
        action_required=urgency in {"critical", "high"},
        action_hint="Review this document for material financial implications." if urgency in {"critical", "high"} else "",
        domains=domains if isinstance(domains, list) else [str(domains)],
        raw=doc,
    )


def extract_from_operating_signal(signal: Dict[str, Any]) -> ExtractedSignal:
    """Normalize a generic operating signal (internal alert, watcher output, etc.)."""
    title = str(signal.get("title", signal.get("label", signal.get("name", "Signal"))))
    body = str(signal.get("body", signal.get("content", signal.get("description", ""))))
    level = str(signal.get("level", signal.get("severity", signal.get("importance", "normal")))).lower()

    urgency_map = {"critical": "critical", "high": "high", "warning": "high", "low": "low", "info": "low"}
    urgency = urgency_map.get(level, _classify_urgency(f"{title} {body}"))

    return ExtractedSignal(
        signal_id=str(signal.get("id", signal.get("signal_id", ""))),
        source="internal",
        signal_type="alert",
        urgency=urgency,
        title=title,
        summary=body[:300],
        entities=_extract_entities(f"{title} {body}"),
        key_facts=_extract_key_facts(f"{title} {body}"),
        action_required=urgency in {"critical", "high"},
        action_hint=str(signal.get("action", signal.get("recommended_action", ""))),
        domains=_classify_domains(f"{title} {body}"),
        raw=signal,
    )


# ---------------------------------------------------------------------------
# Unified normalizer
# ---------------------------------------------------------------------------

_SOURCE_DETECTORS: List[tuple[str, re.Pattern]] = [
    ("email", re.compile(r'thread_id|subject|sender|inbox|gmail', re.IGNORECASE)),
    ("calendar", re.compile(r'starts_at|attendees|calendar|event_id|gcal', re.IGNORECASE)),
    ("finance", re.compile(r'document_id|purpose|domains.*finance|financial', re.IGNORECASE)),
]


def _detect_source(raw: Dict[str, Any]) -> str:
    text = " ".join(str(v) for v in raw.keys()) + " " + " ".join(str(v) for v in list(raw.values())[:5])
    for source, pattern in _SOURCE_DETECTORS:
        if pattern.search(text):
            return source
    return "internal"


def normalize_signals(raw_signals: List[Dict[str, Any]]) -> List[ExtractedSignal]:
    """
    Route a mixed list of raw signal dicts through the appropriate extractor.
    Returns normalized ExtractedSignal objects sorted by urgency (critical first).
    """
    results: List[ExtractedSignal] = []

    for raw in raw_signals:
        if not isinstance(raw, dict):
            continue
        source = raw.get("_source_hint") or _detect_source(raw)
        if source == "email":
            results.append(extract_from_email_thread(raw))
        elif source == "calendar":
            results.append(extract_from_calendar_event(raw))
        elif source == "finance":
            results.append(extract_from_finance_doc(raw))
        else:
            results.append(extract_from_operating_signal(raw))

    results.sort(key=lambda s: _URGENCY_RANK.get(s.urgency, 2))
    return results


# ---------------------------------------------------------------------------
# LLM-enhanced enrichment
# ---------------------------------------------------------------------------


async def async_enrich_signals(signals: List[ExtractedSignal]) -> List[ExtractedSignal]:
    """
    LLM-enhanced signal enrichment.

    Makes one batched Haiku call to semantically improve urgency, domains,
    action_required, key_facts, and entities for all signals in the list.
    Falls back to returning signals unchanged on any failure.
    """
    if not signals:
        return signals

    llm_results = await _llm_enrich_signals(signals)
    if not llm_results:
        return signals

    llm_by_id: dict[str, dict] = {}
    for r in llm_results:
        if isinstance(r, dict) and "signal_id" in r:
            llm_by_id[str(r["signal_id"])] = r

    enriched: List[ExtractedSignal] = []
    for signal in signals:
        llm = llm_by_id.get(signal.signal_id)
        if llm:
            updates: Dict[str, Any] = {}
            if llm.get("urgency") in _VALID_URGENCY:
                updates["urgency"] = llm["urgency"]
            if isinstance(llm.get("domains"), list):
                updates["domains"] = [str(d) for d in llm["domains"][:6]]
            if llm.get("action_required") is not None:
                updates["action_required"] = bool(llm["action_required"])
            if llm.get("action_hint"):
                updates["action_hint"] = str(llm["action_hint"])[:300]
            if isinstance(llm.get("key_facts"), list):
                updates["key_facts"] = [str(f)[:200] for f in llm["key_facts"][:5]]
            if isinstance(llm.get("entities"), list):
                updates["entities"] = [str(e) for e in llm["entities"][:8]]
            if updates:
                signal = signal.model_copy(update=updates)
        enriched.append(signal)

    enriched.sort(key=lambda s: _URGENCY_RANK.get(s.urgency, 2))
    return enriched


async def _llm_enrich_signals(signals: List[ExtractedSignal]) -> List[dict] | None:
    """One batched Haiku call for semantic signal enrichment."""
    try:
        from src.core.llm import LLMClient  # local import to avoid circular deps

        llm = LLMClient(model=_SIGNAL_MODEL)
        if not llm.anthropic_async and not llm.openai_async:
            return None

        batch = [
            {
                "signal_id": s.signal_id,
                "source": s.source,
                "title": s.title[:120],
                "summary": s.summary[:200],
            }
            for s in signals
        ]

        system = (
            "You are a business context classifier for a CEO executive assistant. "
            "Given a batch of signals (emails, events, alerts), enrich each one semantically. "
            "Return ONLY a JSON array — no explanation. "
            'Each element: {"signal_id": str, "urgency": "critical"|"high"|"normal"|"low", '
            '"domains": ["finance"|"legal"|"hr"|"product"|"ops"|"board"|"customer"], '
            '"action_required": bool, "action_hint": "what the CEO should do or is being asked to do", '
            '"key_facts": ["up to 3 brief facts"], "entities": ["names or orgs mentioned"]}. '
            "Set urgency=critical only when the signal demands immediate CEO attention. "
            "Do not over-escalate — most signals are normal or high."
        )
        prompt = f"Enrich these {len(batch)} signals:\n{json.dumps(batch, indent=2)}"

        raw = await llm.complete_async(prompt, system)

        match = re.search(r"\[[\s\S]*\]", raw)
        if match:
            return json.loads(match.group(0))

        print("[SignalExtractor] LLM response contained no JSON array — falling back")
        return None
    except Exception as exc:
        print(f"[SignalExtractor] LLM enrichment failed: {exc}")
        return None


async def async_normalize_and_enrich(
    raw_signals: List[Dict[str, Any]],
) -> List[ExtractedSignal]:
    """Canonical entry point: normalize raw signals then semantically enrich them.

    Prefer this over calling normalize_signals + async_enrich_signals separately.
    Callers that use the sync-only normalize_signals path will get provisional
    regex-based urgency/domains that may be inaccurate.
    """
    signals = normalize_signals(raw_signals)
    return await async_enrich_signals(signals)

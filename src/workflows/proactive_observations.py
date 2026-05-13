from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ObservationType(str, Enum):
    RECURRING_UNRESOLVED = "recurring_unresolved"
    APPROACHING_DEADLINE = "approaching_deadline"
    STALE_OPEN_ITEM = "stale_open_item"
    PATTERN_SHIFT = "pattern_shift"
    CROSS_DOMAIN_LINK = "cross_domain_link"
    ENTITY_RESURFACING = "entity_resurfacing"


class ProactiveObservation(BaseModel):
    observation_type: ObservationType
    headline: str
    detail: Optional[str] = None
    suggested_action: Optional[str] = None
    confidence: float = 0.7
    entities: List[str] = Field(default_factory=list)
    source: str = "proactive_engine"
    suppressed: bool = False

    def to_prompt_line(self) -> str:
        line = f"[{self.observation_type.value}] {self.headline}"
        if self.detail:
            line += f" — {self.detail}"
        if self.suggested_action:
            line += f" → Suggested: {self.suggested_action}"
        return line


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def detect_recurring_unresolved(
    situational_profile: Dict[str, Any],
    task_input: str,
) -> List[ProactiveObservation]:
    """Surface topics the CEO keeps returning to without resolution."""
    observations = []
    recurring = situational_profile.get("recurring_topics") or []
    for topic in recurring:
        if not isinstance(topic, dict) or topic.get("resolved"):
            continue
        count = topic.get("mention_count", 0)
        topic_name = topic.get("topic", "")
        if count >= 3 and topic_name.lower() not in task_input.lower():
            observations.append(ProactiveObservation(
                observation_type=ObservationType.RECURRING_UNRESOLVED,
                headline=f"'{topic_name}' has come up {count} times without a resolution logged",
                suggested_action=f"Want me to draft a resolution plan for {topic_name}?",
                confidence=min(0.6 + (count * 0.05), 0.95),
                entities=[topic_name],
                source="recurring_unresolved_detector",
            ))
    return observations[:2]


def detect_approaching_deadlines(
    live_context: Dict[str, Any],
    signals: List[Dict[str, Any]],
) -> List[ProactiveObservation]:
    """Surface commitments or high-urgency signals within 72 hours."""
    observations = []

    commitments = live_context.get("open_commitments") or []
    urgency_markers = ["by thursday", "by friday", "by eod", "by end of day", "today", "tomorrow"]
    for commitment in commitments:
        if not isinstance(commitment, str):
            continue
        if any(marker in commitment.lower() for marker in urgency_markers):
            observations.append(ProactiveObservation(
                observation_type=ObservationType.APPROACHING_DEADLINE,
                headline=f"Open commitment approaching: {commitment[:120]}",
                confidence=0.75,
                source="deadline_detector",
            ))

    for signal in signals[:5]:
        if not isinstance(signal, dict):
            continue
        urgency = signal.get("urgency", "")
        subject = signal.get("subject") or signal.get("title") or ""
        if urgency in ("high", "critical") and subject:
            observations.append(ProactiveObservation(
                observation_type=ObservationType.APPROACHING_DEADLINE,
                headline=f"High-urgency signal unaddressed: {subject[:120]}",
                confidence=0.80,
                source="deadline_detector",
            ))

    return observations[:2]


def detect_stale_open_items(
    live_context: Dict[str, Any],
    situational_profile: Dict[str, Any],
) -> List[ProactiveObservation]:
    """Surface decisions or threads that haven't moved in > 5 days."""
    observations = []
    now = datetime.now()

    open_decisions = live_context.get("open_decisions") or []
    if len(open_decisions) > 3:
        observations.append(ProactiveObservation(
            observation_type=ObservationType.STALE_OPEN_ITEM,
            headline=f"{len(open_decisions)} open decisions in this conversation — none logged as resolved",
            detail="; ".join(str(d) for d in open_decisions[:2]),
            confidence=0.65,
            source="stale_item_detector",
        ))

    open_threads = situational_profile.get("open_threads") or []
    for thread in open_threads[:3]:
        if not isinstance(thread, dict):
            continue
        first_raised = thread.get("first_raised", "")
        try:
            first_dt = datetime.fromisoformat(first_raised)
            age_days = (now - first_dt).days
            if age_days >= 5:
                observations.append(ProactiveObservation(
                    observation_type=ObservationType.STALE_OPEN_ITEM,
                    headline=f"'{thread.get('topic')}' has been open for {age_days} days without resolution",
                    confidence=0.70,
                    entities=[str(thread.get("topic", ""))],
                    source="stale_item_detector",
                ))
        except (ValueError, TypeError):
            continue

    return observations[:2]


def detect_cross_domain_links(
    situational_profile: Dict[str, Any],
    task_input: str,
) -> List[ProactiveObservation]:
    """Notice when two topics the CEO is tracking are actually connected."""
    observations = []
    recurring = situational_profile.get("recurring_topics") or []
    active_topics = " ".join(
        t["topic"] for t in recurring
        if isinstance(t, dict) and not t.get("resolved")
    ).lower()

    _LINKED_PAIRS = [
        (
            {"aws", "cloud", "infrastructure"},
            {"runway", "burn", "budget"},
            "AWS cost overruns directly affect runway — these two topics are connected",
        ),
        (
            {"hiring", "headcount", "talent"},
            {"runway", "budget", "burn"},
            "Hiring pace affects burn rate — your hiring and finance topics are linked",
        ),
        (
            {"board", "investor"},
            {"variance", "forecast", "finance close"},
            "The board will ask about the variance — your finance close work feeds directly into board prep",
        ),
        (
            {"apex", "deal", "pipeline"},
            {"revenue", "forecast", "q2"},
            "Pipeline deals affect Q2 revenue forecast — Apex and your revenue topics are connected",
        ),
    ]

    task_lower = task_input.lower()
    combined = active_topics + " " + task_lower
    for topic_set_a, topic_set_b, link_message in _LINKED_PAIRS:
        a_present = any(t in combined for t in topic_set_a)
        b_present = any(t in combined for t in topic_set_b)
        if a_present and b_present:
            observations.append(ProactiveObservation(
                observation_type=ObservationType.CROSS_DOMAIN_LINK,
                headline=link_message,
                confidence=0.72,
                source="cross_domain_detector",
            ))

    return observations[:1]


def detect_entity_resurfacing(
    task_input: str,
    entity_context: List[Dict[str, Any]],
) -> List[ProactiveObservation]:
    """Notice when an entity last seen 7+ days ago appears in context."""
    observations = []
    if not entity_context:
        return []

    now = datetime.now()
    for item in entity_context[:5]:
        if not isinstance(item, dict):
            continue
        entity = item.get("entity", "")
        ts = item.get("timestamp", "")
        source_type = item.get("source_type", "")
        if not entity or entity.lower() in task_input.lower():
            continue
        try:
            last_dt = datetime.fromisoformat(ts)
            days_since = (now - last_dt).days
            if days_since >= 7 and source_type in ("memory", "thread_entry"):
                observations.append(ProactiveObservation(
                    observation_type=ObservationType.ENTITY_RESURFACING,
                    headline=f"'{entity}' last came up {days_since} days ago — still relevant?",
                    detail=str(item.get("snippet", ""))[:150],
                    confidence=0.60,
                    entities=[entity],
                    source="entity_resurfacing_detector",
                ))
        except (ValueError, TypeError):
            continue

    return observations[:1]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def run_proactive_scan(
    *,
    task_input: str,
    situational_profile: Dict[str, Any],
    live_context: Dict[str, Any],
    signals: List[Dict[str, Any]],
    entity_context: List[Dict[str, Any]],
    max_observations: int = 3,
) -> List[ProactiveObservation]:
    """
    Run all detectors and return the top N observations ranked by confidence.

    Fails silently — never blocks a workflow on observation errors.
    """
    try:
        all_observations: List[ProactiveObservation] = []
        all_observations.extend(detect_recurring_unresolved(situational_profile, task_input))
        all_observations.extend(detect_approaching_deadlines(live_context, signals))
        all_observations.extend(detect_stale_open_items(live_context, situational_profile))
        all_observations.extend(detect_cross_domain_links(situational_profile, task_input))
        all_observations.extend(detect_entity_resurfacing(task_input, entity_context))

        # Deduplicate by headline prefix
        seen: set = set()
        deduped = []
        for obs in all_observations:
            prefix = obs.headline[:40].lower()
            if prefix not in seen:
                deduped.append(obs)
                seen.add(prefix)

        ranked = sorted(deduped, key=lambda o: o.confidence, reverse=True)
        return ranked[:max_observations]
    except Exception:
        return []


def observations_to_prompt_block(
    observations: List[ProactiveObservation],
    min_confidence: float = 0.65,
) -> str:
    """Render observations as a prompt block for agents."""
    visible = [o for o in observations if o.confidence >= min_confidence]
    if not visible:
        return ""
    lines = [
        "=== PROACTIVE OBSERVATIONS (surface one if it's relevant — don't force it) ===",
        "These are things noticed in context that the CEO didn't explicitly ask about.",
        "Surface at most ONE if it genuinely fits the current response.",
        "",
    ]
    for obs in visible:
        lines.append(f"• {obs.to_prompt_line()}")
    lines.append(
        "\nInstruction: If you surface an observation, do it at the END of your response "
        "in a brief '--- Also noticed ---' section. Do not interrupt the main response."
    )
    return "\n".join(lines) + "\n\n"

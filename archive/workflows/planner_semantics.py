from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PlannerSemanticSignals:
    planning: bool
    email: bool
    calendar: bool
    documents: bool
    watch: bool
    finance_analysis: bool
    strategic_analysis: bool
    action_plan: bool
    escalation: bool
    recommendation: bool

    def as_metadata(self) -> dict[str, bool]:
        return {
            "planning": self.planning,
            "email": self.email,
            "calendar": self.calendar,
            "documents": self.documents,
            "watch": self.watch,
            "finance_analysis": self.finance_analysis,
            "strategic_analysis": self.strategic_analysis,
            "action_plan": self.action_plan,
            "escalation": self.escalation,
            "recommendation": self.recommendation,
        }


def infer_planner_semantics(
    *,
    text: str,
    has_time_scope: bool,
    inbox_keywords: tuple[str, ...],
    calendar_keywords: tuple[str, ...],
    document_keywords: tuple[str, ...],
    watch_keywords: tuple[str, ...],
    planning_keywords: tuple[str, ...],
    scheduling_intent_keywords: tuple[str, ...],
    semantic_email_cues: tuple[str, ...],
    semantic_calendar_cues: tuple[str, ...],
    semantic_document_cues: tuple[str, ...],
    semantic_planning_cues: tuple[str, ...],
    semantic_watch_cues: tuple[str, ...],
    finance_analysis_keywords: tuple[str, ...],
    strategic_analysis_keywords: tuple[str, ...],
    action_plan_keywords: tuple[str, ...],
    escalation_keywords: tuple[str, ...],
    recommendation_keywords: tuple[str, ...] = (),
) -> PlannerSemanticSignals:
    token_count = len(re.findall(r"[a-z0-9']+", text))

    planning_score = _cue_score(text, semantic_planning_cues)
    email_score = _cue_score(text, semantic_email_cues)
    calendar_score = _cue_score(text, semantic_calendar_cues)
    document_score = _cue_score(text, semantic_document_cues)
    watch_score = _cue_score(text, semantic_watch_cues)

    planning = (
        _contains_any(text, planning_keywords)
        or _contains_any(text, scheduling_intent_keywords)
        or planning_score >= 1
        or (has_time_scope and any(cue in text for cue in ("free time", "what do i need to do", "what should i work on")))
    )
    email = (_contains_any(text, inbox_keywords) or email_score >= 1) and (planning or has_time_scope or token_count <= 20)
    calendar = (_contains_any(text, calendar_keywords) or calendar_score >= 1) and (planning or has_time_scope)
    documents = _contains_any(text, document_keywords) or (document_score >= 1 and not planning)
    watch = _contains_any(text, watch_keywords) or watch_score >= 1 or (not planning and has_time_scope and email_score >= 1 and calendar_score >= 1)

    return PlannerSemanticSignals(
        planning=planning,
        email=email,
        calendar=calendar,
        documents=documents,
        watch=watch,
        finance_analysis=_contains_any(text, finance_analysis_keywords),
        strategic_analysis=_contains_any(text, strategic_analysis_keywords),
        action_plan=_contains_any(text, action_plan_keywords),
        escalation=_contains_any(text, escalation_keywords),
        recommendation=_contains_any(text, recommendation_keywords) if recommendation_keywords else False,
    )


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _cue_score(text: str, cues: tuple[str, ...]) -> int:
    return sum(1 for cue in cues if cue in text)

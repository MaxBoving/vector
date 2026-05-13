from __future__ import annotations

from typing import Any


def rank_question_options(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for entry in entries:
        question = str(entry.get("question") or "").strip()
        offer_type = str(entry.get("offer_type") or "").strip() or None
        score = _score_question(question=question, offer_type=offer_type, options=entry.get("options") or [])
        ranked.append({**entry, "priority_score": score})
    return sorted(ranked, key=lambda item: float(item.get("priority_score") or 0), reverse=True)


def _score_question(*, question: str, offer_type: str | None, options: list[dict[str, Any]]) -> float:
    ql = question.lower()
    score = 0.0
    if offer_type == "clarification" and len(options) == 2:
        score += 5.0
    if any(marker in ql for marker in ("which option", "which approach", "do you want", "prefer", "should i use")):
        score += 4.0
    if any(marker in ql for marker in ("which period", "anchor to", "quarter", "ytd", "year-to-date", "current month", "rolling", "trailing")):
        score += 3.0
    if any(marker in ql for marker in ("source of truth", "numbers", "baseline")):
        score += 2.5
    if any(marker in ql for marker in ("board packet", "investor", "operating decision")):
        score += 2.0
    if any(marker in ql for marker in ("what decision", "what outcome", "key decision", "key outcome")):
        score += 2.0
    if any(marker in ql for marker in ("assumption", "confirm", "is this correct")):
        score += 1.0
    if offer_type == "action_offer":
        score -= 2.0
    return score

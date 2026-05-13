from __future__ import annotations

from src.workflows.follow_up_planner import build_follow_up_candidate, select_follow_up_candidates
from src.workflows.semantic_followups import (
    build_semantic_context,
    build_semantic_follow_up_candidates,
    build_semantic_question_options,
)


def test_follow_up_planner_prefers_earliest_deadline_then_diversity() -> None:
    candidates = [
        build_follow_up_candidate(
            "Customer follow-up",
            family="customer",
            deadline_at="2026-05-11T09:00:00-07:00",
            priority=80.0,
            topic_key="customer-1",
        ),
        build_follow_up_candidate(
            "Finance follow-up",
            family="finance",
            deadline_at="2026-05-10T09:00:00-07:00",
            priority=70.0,
            topic_key="finance-1",
        ),
        build_follow_up_candidate(
            "Second customer follow-up",
            family="customer",
            deadline_at="2026-05-12T09:00:00-07:00",
            priority=95.0,
            topic_key="customer-2",
        ),
        build_follow_up_candidate(
            "Calendar follow-up",
            family="calendar",
            deadline_at="2026-05-13T09:00:00-07:00",
            priority=60.0,
            topic_key="calendar-1",
        ),
    ]

    selected = select_follow_up_candidates(candidates, limit=3)

    assert [item.family for item in selected] == ["finance", "customer", "calendar"]
    assert [item.text for item in selected] == [
        "Finance follow-up",
        "Customer follow-up",
        "Calendar follow-up",
    ]


def test_semantic_follow_ups_are_topic_driven() -> None:
    context = build_semantic_context(
        title="Morning Brief",
        summary="4 important threads need attention.",
        sections=[
            {"label": "Important Threads", "items": ["P&L 2025-10"]},
            {"label": "Upcoming Meetings", "items": ["Finance review — Farrukh • 2026-05-12T09:00:00-07:00"]},
        ],
        sources=[{"source_id": "thread-1", "title": "P&L 2025-10", "type": "state"}],
        confidence_score=0.32,
        evidence_state="sparse",
        missing_context=["Calendar context is thin."],
        topic_hint="P&L 2025-10",
        date_hint="2026-05-12T09:00:00-07:00",
        importance_hint=86.0,
    )

    follow_ups = build_semantic_follow_up_candidates(context, limit=3)
    question_options = build_semantic_question_options(context)

    assert context.topic == "P&L 2025-10"
    assert context.needs_more_info is True
    assert any("P&L 2025-10" in item.text for item in follow_ups)
    assert all("morning brief" not in item.text.lower() for item in follow_ups)
    assert question_options and question_options[0]["offer_type"] == "clarification"
    assert "P&L 2025-10" in question_options[0]["question"]

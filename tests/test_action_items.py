from __future__ import annotations

from datetime import datetime

from src.agents.briefing_agent import BriefingAgent
from src.workflows.action_items import normalize_structured_watch


def test_normalize_structured_watch_resolves_before_3pm_board_review() -> None:
    reference_dt = datetime.fromisoformat("2026-03-29T09:00:00-07:00")
    structured_watch = {
        "asks": [{"ask": "Decide on cloud spend containment option before 3 PM board review."}],
        "deadlines": [],
        "implied_docs": [],
    }
    upcoming_events = [
        {"meeting_id": "evt_board", "title": "Board review", "starts_at": "2026-03-29T17:00:00-07:00"},
    ]

    normalized = normalize_structured_watch(
        structured_watch,
        upcoming_events=upcoming_events,
        reference_dt=reference_dt,
    )
    ask = normalized["asks"][0]

    assert ask["inference_kind"] == "derived_from_event"
    assert ask["due_at"] == "2026-03-29T15:00:00-07:00"
    assert ask["related_event_id"] == "evt_board"
    assert ask["related_event_title"] == "Board review"


def test_normalize_structured_watch_resolves_by_noon_today() -> None:
    reference_dt = datetime.fromisoformat("2026-03-29T09:00:00-07:00")
    structured_watch = {
        "asks": [{"ask": "Markup board packet slides 4, 9, and 12 by noon today."}],
        "deadlines": [],
        "implied_docs": [],
    }

    normalized = normalize_structured_watch(
        structured_watch,
        upcoming_events=[],
        reference_dt=reference_dt,
    )
    ask = normalized["asks"][0]

    assert ask["inference_kind"] == "derived_relative_time"
    assert ask["due_at"] == "2026-03-29T12:00:00-07:00"
    assert ask["due_date"] == "2026-03-29"


def test_schedule_rendering_uses_normalized_timing_for_next_week() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {"subject": "Board packet", "importance_level": "high", "importance_score": 88, "suppressed": False}
        ],
        "structured_watch": {
            "asks": [
                {"ask": "Markup board packet slides 4, 9, and 12 by noon today."},
                {"ask": "Prepare board prep kick-off agenda by April 10."},
                {"ask": "Decide on cloud spend containment option before 3 PM board review."},
            ],
            "deadlines": [
                {"deadline": "Board packet CEO markup by 2026-03-29T12:00:00-07:00."},
                {"deadline": "Board prep readout by 2026-04-10T17:00:00-07:00."},
            ],
            "implied_docs": [{"document": "Board packet CEO narrative"}],
        },
        "upcoming_events": [
            {"meeting_id": "evt_today", "title": "Board review", "starts_at": "2026-03-29T17:00:00-07:00"},
            {"meeting_id": "evt_next", "title": "Board prep kick-off", "starts_at": "2026-04-10T11:00:00-07:00"},
        ],
        "planning_context": {"time_horizon": "next_week", "mode": "compound_plan"},
    }

    payload = agent._generate_payload(  # type: ignore[attr-defined]
        workflow_type="schedule_planning",
        event_payload=event_payload,
        prepared_context={},
        completion=None,
        task_input="Generate me a schedule for next week",
    )
    payload = agent._apply_presentation_metadata(  # type: ignore[attr-defined]
        payload,
        event_payload=event_payload,
        workflow_type="schedule_planning",
    )

    deadlines = next(section for section in payload.answer.sections if section.label == "Deadlines")
    follow_ups = next(section for section in payload.answer.sections if section.label == "Suggested Follow-Ups")

    assert all("noon today" not in item.lower() for item in follow_ups.items)
    assert all("3 pm board review" not in item.lower() for item in follow_ups.items)
    assert deadlines.items == ["Board prep readout by 2026-04-10T17:00:00-07:00."]

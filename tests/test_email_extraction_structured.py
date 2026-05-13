from __future__ import annotations

from src.integrations.email_extraction import cross_reference_threads_with_calendar, extract_structured_watch_items


def test_cross_reference_threads_uses_structured_related_threads_only() -> None:
    ranked_threads = [
        {
            "thread_id": "thread_1",
            "subject": "Board packet",
            "participants": ["farrukh@vela.com"],
            "snippet": "Please review before the meeting.",
            "importance_reasons": [],
            "importance_score": 10,
            "importance_level": "medium",
        }
    ]
    upcoming_events = [
        {
            "meeting_id": "meeting_1",
            "title": "Board packet review",
            "related_threads": [{"thread_id": "thread_1"}],
        }
    ]

    enriched = cross_reference_threads_with_calendar(ranked_threads, upcoming_events)

    assert enriched[0]["calendar_matches"] == [
        {"meeting_id": "meeting_1", "title": "Board packet review", "match": "related_thread"}
    ]
    assert enriched[0]["importance_score"] > 10


def test_cross_reference_threads_ignores_title_similarity_without_structure() -> None:
    ranked_threads = [
        {
            "thread_id": "thread_2",
            "subject": "Board packet",
            "participants": ["farrukh@vela.com"],
            "snippet": "Please review before the meeting.",
            "importance_reasons": [],
            "importance_score": 10,
            "importance_level": "medium",
        }
    ]
    upcoming_events = [
        {
            "meeting_id": "meeting_2",
            "title": "Board packet review",
            "related_threads": [],
        }
    ]

    enriched = cross_reference_threads_with_calendar(ranked_threads, upcoming_events)

    assert "calendar_matches" not in enriched[0]
    assert enriched[0]["importance_score"] == 10


def test_extract_structured_watch_items_does_not_infer_meetings_from_thread_text() -> None:
    watch = extract_structured_watch_items(
        [
            {
                "thread_id": "thread_3",
                "subject": "Need to discuss the board meeting",
                "latest_sender": "CEO",
                "importance_level": "high",
                "importance_score": 40,
                "suppressed": False,
                "snippet": "Let's sync tomorrow about the meeting.",
            }
        ],
        upcoming_events=[],
    )

    assert watch["implied_meetings"] == []

"""
Artifact rendering tests.

Validates that each workflow type produces a BriefPayload / AssistantMessageResponse
that satisfies the frontend rendering contract:

  1. presentation.mode is correct (drives renderer selection in ModeRenderer.tsx)
  2. presentation.variant is correct (drives label in getExecutiveResponseLabel)
  3. presentation.summary is non-empty (used by getExecutiveSummary)
  4. presentation.priorities / recommended_actions / risks are populated
     (so BriefRenderer 2-column layout renders without falling through to section regex)
  5. answer.sections have the right labels and non-empty items
     (fallback path: section regex grouping in groupExecutiveSections)
  6. Section labels map to the correct frontend grouping bucket
  7. schedule mode: weekly_plan structure is returned for schedule workflows
  8. Trust shape is complete (confidence, evidence_state, safe_to_act)
  9. AssistantMessageResponse presentation field is set correctly end-to-end

Frontend regex patterns (from messagePresentation.ts):
  PRIORITY_LABELS = /(priority|key finding|headline|important|focus|top|watch|threads|inputs|proposal)/i
  ACTION_LABELS   = /(action|recommend|follow[- ]?up|next step|decision|plan|send|approve)/i
  RISK_LABELS     = /(risk|deadline|constraint|tradeoff|missing|concern|blocker|question|meeting)/i
"""

import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agents.briefing_agent import BriefingAgent, BriefPayload
from src.api.schemas import AssistantMessageResponse, TrustMetadata
from src.core.models import SessionInteraction, User
from src.workflows.event_payloads import build_watch_event_payload
from src.workflows.read_model import build_assistant_message_response

# ---------------------------------------------------------------------------
# Frontend regex mirrors (from messagePresentation.ts)
# ---------------------------------------------------------------------------

PRIORITY_LABELS = re.compile(r"(priority|key finding|headline|important|focus|top|watch|threads|inputs|proposal)", re.IGNORECASE)
ACTION_LABELS = re.compile(r"(action|recommend|follow[- ]?up|next step|decision|plan|send|approve)", re.IGNORECASE)
RISK_LABELS = re.compile(r"(risk|deadline|constraint|tradeoff|missing|concern|blocker|question|meeting)", re.IGNORECASE)


def _label_bucket(label: str) -> str:
    if PRIORITY_LABELS.search(label):
        return "priority"
    if ACTION_LABELS.search(label):
        return "action"
    if RISK_LABELS.search(label):
        return "risk"
    return "detail"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _agent() -> BriefingAgent:
    return BriefingAgent(tools=None)  # type: ignore[arg-type]


def _trust_dump() -> dict:
    return TrustMetadata().model_dump()


def _user() -> User:
    return User(id=1, username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")


def _interaction(id: int, query: str) -> SessionInteraction:
    return SessionInteraction(id=id, ceo_id="ceo_test", query=query, status="COMPLETED")


def _stub_planner(monkeypatch, workflow_type: str) -> None:
    monkeypatch.setattr(
        "src.workflows.read_model.plan_request",
        lambda *args, **kwargs: SimpleNamespace(direct_workflow=workflow_type),
    )


def _base_threads() -> list[dict]:
    return [
        {
            "subject": "Q1 Budget Review",
            "latest_sender": "cfo@company.com",
            "importance_level": "high",
            "importance_score": 88,
            "importance_reasons": ["Board-level visibility"],
            "suppressed": False,
            "category": "finance",
        },
        {
            "subject": "Investor Update",
            "latest_sender": "investor@vc.com",
            "importance_level": "medium",
            "importance_score": 70,
            "importance_reasons": ["Awaiting reply"],
            "suppressed": False,
            "category": "investor",
        },
    ]


def _base_events() -> list[dict]:
    return [
        {
            "title": "Q1 Board Meeting",
            "starts_at": "2026-03-20T10:00:00",
            "ends_at": "2026-03-20T11:00:00",
            "attendees": ["board@company.com", "cfo@company.com"],
        }
    ]


def _base_structured_watch() -> dict:
    return {
        "asks": [{"ask": "Review budget proposal by Friday"}],
        "deadlines": [{"deadline": "Friday EOD — Q1 board deck"}],
        "implied_docs": [{"document": "Q1 board deck"}],
    }


def _build_payload(
    workflow_type: str,
    event_payload: dict[str, Any],
    prepared_context: dict | None = None,
) -> BriefPayload:
    agent = _agent()
    payload = agent._generate_payload(  # type: ignore[attr-defined]
        workflow_type=workflow_type,
        event_payload=event_payload,
        prepared_context=prepared_context or {},
        completion=None,
    )
    return agent._apply_presentation_metadata(payload, event_payload=event_payload, workflow_type=workflow_type)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Per-mode event payload factories
# ---------------------------------------------------------------------------

def _email_watcher_payload() -> dict:
    return {
        "ranked_threads": _base_threads(),
        "structured_watch": _base_structured_watch(),
        "upcoming_events": [],
    }


def _calendar_briefing_payload() -> dict:
    return {
        "title": "Q1 Board Meeting",
        "starts_at": "2026-03-20T10:00:00",
        "attendees": ["board@company.com", "cfo@company.com"],
        "related_threads": [_base_threads()[0]],
        "ranked_threads": [],
        "upcoming_events": _base_events(),
    }


def _morning_brief_payload() -> dict:
    return {
        "ranked_threads": _base_threads(),
        "structured_watch": _base_structured_watch(),
        "upcoming_events": _base_events(),
    }


def _weekly_recap_payload() -> dict:
    return {
        "ranked_threads": _base_threads(),
        "structured_watch": _base_structured_watch(),
        "upcoming_events": _base_events(),
    }


def _meeting_prep_payload() -> dict:
    return {
        "upcoming_events": [
            {
                "title": "Q1 Board Meeting",
                "starts_at": "2026-03-20T10:00:00",
                "attendees": ["board@company.com", "cfo@company.com"],
            }
        ],
        "attendee_threads": [
            {
                "subject": "Board deck pre-read",
                "latest_sender": "cfo@company.com",
                "importance_level": "high",
                "importance_reasons": ["Required reading before the board meeting"],
            }
        ],
        "attendee_emails": ["board@company.com", "cfo@company.com"],
        "ranked_threads": _base_threads(),
        "structured_watch": _base_structured_watch(),
    }


def _day_schedule_payload() -> dict:
    return {
        "ranked_threads": _base_threads(),
        "structured_watch": _base_structured_watch(),
        "upcoming_events": _base_events(),
        "planning_context": {
            "mode": "compound_plan",
            "time_horizon": "tomorrow",
            "subtasks": [{"description": "Scan inbox for actionable threads"}],
            "evidence_summary": {"context_source_count": 3, "deadline_count": 1, "meeting_count": 1},
            "execution_steps": [
                {"key": "gather_email", "status": "completed", "details": {}},
                {"key": "gather_calendar", "status": "completed", "details": {}},
                {"key": "synthesize_response", "status": "completed", "details": {}},
            ],
        },
        "plan_execution": {
            "planning_window": {"horizon": "tomorrow"},
            "schedule_blocks": [
                "9:00 AM - 9:30 AM: Review Q1 board deck pre-read materials.",
                "10:00 AM - 11:00 AM: Q1 Board Meeting.",
                "11:30 AM - 12:00 PM: Draft investor update reply.",
            ],
            "evidence_summary": {"placed_candidate_count": 3, "candidate_count": 3},
            "sparse_guidance": False,
        },
    }


def _week_schedule_payload() -> dict:
    p = _day_schedule_payload()
    p["planning_context"]["time_horizon"] = "this_week"  # type: ignore[index]
    return p


# ---------------------------------------------------------------------------
# 1. presentation.mode and presentation.variant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_type,expected_mode,expected_variant,event_payload_override", [
    ("email_watcher", "brief", "inbox_watch", None),
    ("email_ingestion", "brief", "inbox_watch", None),
    ("calendar_briefing", "calendar", "day_grid", None),
    ("morning_brief", "brief", "weekly_watch", None),
    ("weekly_recap", "brief", "weekly_recap", None),
    ("meeting_prep", "brief", "meeting_prep", None),
    ("schedule_planning", "schedule", "timeline", "_day_schedule_payload"),
    ("schedule_planning", "schedule", "week_timeline", "_week_schedule_payload"),
])
def test_presentation_mode_and_variant(workflow_type: str, expected_mode: str, expected_variant: str, event_payload_override) -> None:
    event_payloads = {
        "email_watcher": _email_watcher_payload(),
        "email_ingestion": _email_watcher_payload(),
        "calendar_briefing": _calendar_briefing_payload(),
        "morning_brief": _morning_brief_payload(),
        "weekly_recap": _weekly_recap_payload(),
        "meeting_prep": _meeting_prep_payload(),
        "schedule_planning": _day_schedule_payload(),
    }
    if event_payload_override == "_week_schedule_payload":
        ep = _week_schedule_payload()
    else:
        ep = event_payloads[workflow_type]
    payload = _build_payload(workflow_type, ep)

    assert payload.presentation is not None, f"{workflow_type}/{expected_variant}: presentation is None"
    assert payload.presentation.mode == expected_mode, (
        f"{workflow_type}/{expected_variant}: expected mode {expected_mode!r}, got {payload.presentation.mode!r}"
    )
    assert payload.presentation.variant == expected_variant, (
        f"{workflow_type}/{expected_variant}: expected variant {expected_variant!r}, got {payload.presentation.variant!r}"
    )


# ---------------------------------------------------------------------------
# 2. presentation.summary is non-empty
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_type,event_payload_fn", [
    ("email_watcher", _email_watcher_payload),
    ("calendar_briefing", _calendar_briefing_payload),
    ("morning_brief", _morning_brief_payload),
    ("weekly_recap", _weekly_recap_payload),
    ("meeting_prep", _meeting_prep_payload),
    ("schedule_planning", _day_schedule_payload),
    ("schedule_planning", _week_schedule_payload),
])
def test_presentation_summary_is_populated(workflow_type: str, event_payload_fn) -> None:
    payload = _build_payload(workflow_type, event_payload_fn())
    assert payload.presentation is not None
    assert payload.presentation.summary, f"{workflow_type}: presentation.summary is empty"
    assert len(payload.presentation.summary) > 10, f"{workflow_type}: summary too short"


# ---------------------------------------------------------------------------
# 3. BriefRenderer native fields — priorities / recommended_actions populated
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_type,event_payload_fn", [
    ("email_watcher", _email_watcher_payload),
    ("calendar_briefing", _calendar_briefing_payload),
    ("morning_brief", _morning_brief_payload),
    ("weekly_recap", _weekly_recap_payload),
    ("meeting_prep", _meeting_prep_payload),
])
def test_brief_mode_has_native_presentation_sections(workflow_type: str, event_payload_fn) -> None:
    """BriefRenderer gets priorities and recommended_actions, avoids regex fallback."""
    payload = _build_payload(workflow_type, event_payload_fn())
    assert payload.presentation is not None
    assert payload.presentation.priorities, f"{workflow_type}: priorities is empty"
    assert payload.presentation.recommended_actions, f"{workflow_type}: recommended_actions is empty"
    # At least one section has items
    priority_items = [item for s in payload.presentation.priorities for item in s.items]
    assert priority_items, f"{workflow_type}: no items in any priority section"


# ---------------------------------------------------------------------------
# 4. answer.sections — labels, content, and fallback bucket classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_type,event_payload_fn,expected_labels", [
    ("email_watcher", _email_watcher_payload,
     ["Top Threads", "Action Required", "Waiting On Reply", "FYI Only", "Suggested Actions"]),
    ("calendar_briefing", _calendar_briefing_payload,
     ["Upcoming Meetings", "Related Threads", "Suggested Follow-Ups"]),
    ("morning_brief", _morning_brief_payload,
     ["Important Threads", "Deadlines", "Upcoming Meetings", "Suggested Follow-Ups"]),
    ("weekly_recap", _weekly_recap_payload,
     ["This Week's Threads", "Meetings Held", "Deadlines & Commitments", "Still Open"]),
    ("meeting_prep", _meeting_prep_payload,
     ["Meeting Overview", "Meeting Objectives", "Open Items", "Suggested Talking Points", "Desired Outcomes"]),
    ("schedule_planning", _day_schedule_payload,
     ["Planning Inputs", "Schedule Proposal", "Deadlines", "Upcoming Meetings", "Suggested Follow-Ups"]),
])
def test_answer_section_labels(workflow_type: str, event_payload_fn, expected_labels: list[str]) -> None:
    payload = _build_payload(workflow_type, event_payload_fn())
    actual_labels = [s.label for s in payload.answer.sections]
    assert actual_labels == expected_labels, (
        f"{workflow_type}: expected {expected_labels}, got {actual_labels}"
    )


@pytest.mark.parametrize("workflow_type,event_payload_fn", [
    ("email_watcher", _email_watcher_payload),
    ("morning_brief", _morning_brief_payload),
    ("weekly_recap", _weekly_recap_payload),
    ("meeting_prep", _meeting_prep_payload),
    ("calendar_briefing", _calendar_briefing_payload),
])
def test_answer_sections_have_non_empty_items(workflow_type: str, event_payload_fn) -> None:
    payload = _build_payload(workflow_type, event_payload_fn())
    empty_sections = [s.label for s in payload.answer.sections if not s.items and not s.content]
    assert not empty_sections, f"{workflow_type}: empty sections: {empty_sections}"


# ---------------------------------------------------------------------------
# 5. Section label → correct frontend fallback bucket
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,expected_bucket", [
    # email_watcher / morning_brief
    ("Important Threads", "priority"),
    ("Deadlines", "risk"),
    ("Upcoming Meetings", "risk"),
    ("Suggested Follow-Ups", "action"),
    # weekly_recap
    ("This Week's Threads", "priority"),
    ("Meetings Held", "risk"),
    ("Deadlines & Commitments", "risk"),
    # meeting_prep
    ("Attendee Threads", "priority"),
    ("Meeting Overview", "risk"),
    # schedule
    ("Planning Inputs", "priority"),
    ("Schedule Proposal", "priority"),
])
def test_section_label_frontend_bucket(label: str, expected_bucket: str) -> None:
    assert _label_bucket(label) == expected_bucket, (
        f"Label {label!r}: expected bucket {expected_bucket!r}, got {_label_bucket(label)!r}"
    )


# ---------------------------------------------------------------------------
# 6. Schedule mode weekly_plan structure
# ---------------------------------------------------------------------------

def test_day_schedule_weekly_plan_is_populated() -> None:
    payload = _build_payload("schedule_planning", _day_schedule_payload())
    assert payload.presentation is not None
    assert payload.presentation.weekly_plan is not None, "weekly_plan is None for schedule_planning (day)"
    wp = payload.presentation.weekly_plan
    assert wp.blocks or wp.meetings or wp.deadlines, "weekly_plan has no blocks, meetings, or deadlines"


def test_week_schedule_weekly_plan_is_populated() -> None:
    payload = _build_payload("schedule_planning", _week_schedule_payload())
    assert payload.presentation is not None
    assert payload.presentation.weekly_plan is not None, "weekly_plan is None for schedule_planning (week)"


def test_brief_mode_weekly_plan_is_none() -> None:
    """Brief-mode workflows must not emit weekly_plan — ScheduleRenderer would misfire."""
    for wf, ep_fn in [
        ("email_watcher", _email_watcher_payload),
        ("morning_brief", _morning_brief_payload),
        ("weekly_recap", _weekly_recap_payload),
        ("meeting_prep", _meeting_prep_payload),
    ]:
        payload = _build_payload(wf, ep_fn())
        assert payload.presentation is not None
        assert payload.presentation.weekly_plan is None, f"{wf}: weekly_plan should be None for brief mode"


# ---------------------------------------------------------------------------
# 7. Trust shape completeness
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_type,event_payload_fn", [
    ("email_watcher", _email_watcher_payload),
    ("morning_brief", _morning_brief_payload),
    ("weekly_recap", _weekly_recap_payload),
    ("meeting_prep", _meeting_prep_payload),
    ("schedule_planning", _day_schedule_payload),
    ("schedule_planning", _week_schedule_payload),
])
def test_trust_shape_is_complete(workflow_type: str, event_payload_fn) -> None:
    payload = _build_payload(workflow_type, event_payload_fn())
    trust = payload.trust
    assert trust.confidence in {"low", "medium", "high"}
    assert 0.0 <= trust.confidence_score <= 1.0
    assert trust.evidence_state in {"strong", "mixed", "sparse", None}
    assert isinstance(trust.assumptions, list)
    assert isinstance(trust.missing_context, list)


def test_strong_evidence_yields_high_or_medium_confidence() -> None:
    payload = _build_payload(
        "morning_brief",
        {
            "ranked_threads": _base_threads() * 2,
            "structured_watch": {
                "asks": [{"ask": "reply to board"}],
                "deadlines": [{"deadline": "Friday"}, {"deadline": "Monday"}],
                "implied_docs": [],
            },
            "upcoming_events": _base_events() * 2,
            "document_context": {"attachments": [{"document_id": "doc1"}]},
            "planning_context": {
                "mode": "compound_plan",
                "execution_steps": [
                    {"key": "gather_email", "status": "completed"},
                    {"key": "gather_calendar", "status": "completed"},
                    {"key": "synthesize_response", "status": "completed"},
                ],
                "evidence_summary": {"context_source_count": 3},
            },
        },
        {"signals": [{"subject": "Weekly signal"}]},
    )
    assert payload.trust.confidence in {"medium", "high"}


def test_no_evidence_yields_low_confidence() -> None:
    payload = _build_payload(
        "email_watcher",
        {
            "ranked_threads": [],
            "structured_watch": {"asks": [], "deadlines": [], "implied_docs": []},
            "upcoming_events": [],
        },
    )
    assert payload.trust.confidence == "low"


# ---------------------------------------------------------------------------
# 8. Read model end-to-end — AssistantMessageResponse presentation contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_type,query,expected_mode,expected_title", [
    ("email_watcher", "scan my inbox", "brief", "Inbox Brief"),
    ("calendar_briefing", "what meetings do I have", "calendar", "Calendar Brief"),
    ("morning_brief", "give me my morning brief", "brief", "Morning Brief"),
    ("weekly_recap", "recap my week", "brief", "Week in Review"),
    ("meeting_prep", "prep for my meeting with the board", "brief", "Meeting Brief"),
    ("schedule_planning", "plan my day for today", "schedule", "Executive Schedule"),
    ("schedule_planning", "plan my week schedule", "schedule", "Executive Schedule"),
    ("report_generation", "give me a company health overview", "report", "Executive Report"),
    ("document_explanation", "explain this contract", "report", "Business Implication Brief"),
])
def test_read_model_response_presentation_contract(
    workflow_type: str, query: str, expected_mode: str, expected_title: str, monkeypatch
) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts",
                        lambda _id, _ceo: {"synthesizer": f"{workflow_type} synthesizer output"})
    _stub_planner(monkeypatch, workflow_type)

    interaction = _interaction(1, query)
    response = build_assistant_message_response(interaction, _user())

    assert response.workflow_type == workflow_type, f"query={query!r}: expected {workflow_type}, got {response.workflow_type}"
    assert response.answer.title == expected_title, f"{workflow_type}: expected title {expected_title!r}, got {response.answer.title!r}"
    assert response.presentation is not None, f"{workflow_type}: presentation is None"
    assert response.presentation.mode == expected_mode, f"{workflow_type}: expected mode {expected_mode!r}, got {response.presentation.mode!r}"
    assert response.presentation.summary, f"{workflow_type}: presentation.summary is empty"
    assert response.answer.summary, f"{workflow_type}: answer.summary is empty"


def test_read_model_response_has_required_fields(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts",
                        lambda _id, _ceo: {"synthesizer": "Email brief content here"})
    _stub_planner(monkeypatch, "email_watcher")

    interaction = _interaction(5, "scan my inbox")
    response = build_assistant_message_response(interaction, _user())

    assert isinstance(response, AssistantMessageResponse)
    assert response.conversation_id
    assert response.message_id
    assert response.status in {"pending", "completed", "failed"}
    assert response.trust is not None
    assert response.trust.confidence in {"low", "medium", "high"}
    assert isinstance(response.sources, list)
    assert isinstance(response.artifacts, list)
    assert response.metadata.get("interaction_id") is not None


def test_read_model_prefers_persisted_response_data_for_new_records(monkeypatch) -> None:
    persisted = AssistantMessageResponse(
        conversation_id="conv:ceo_test:primary",
        message_id="msg_77",
        workflow_type="calendar_briefing",
        response_type="brief",
        status="completed",
        answer={"title": "Calendar Brief", "summary": "Persisted summary", "sections": []},
        trust=TrustMetadata(),
        sources=[],
        artifacts=[],
        presentation={"mode": "calendar", "variant": "day_grid", "summary": "Persisted summary"},
        metadata={"persisted": True},
    )
    workflow_run = SimpleNamespace(response_data=persisted.model_dump(), state_data={}, workflow_type="calendar_briefing")

    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: workflow_run)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts", lambda _id, _ceo: {"synthesizer": "# artifact"})
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    interaction = _interaction(77, "what meetings do I have today?")
    response = build_assistant_message_response(interaction, _user())

    assert response.workflow_type == "calendar_briefing"
    assert response.response_type == "brief"
    assert response.presentation is not None
    assert response.presentation.mode == "calendar"
    assert response.metadata["persisted"] is True


def test_legacy_read_model_uses_workflow_metadata_for_response_truth(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts", lambda _id, _ceo: {"synthesizer": "Inbox brief content here"})
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})
    _stub_planner(monkeypatch, "email_watcher")

    interaction = _interaction(78, "scan my inbox")
    response = build_assistant_message_response(interaction, _user())

    assert response.response_type == "brief"
    assert response.presentation is not None
    assert response.presentation.mode == "brief"


# ---------------------------------------------------------------------------
# 9. Mode-specific rendering safety — no cross-mode bleed
# ---------------------------------------------------------------------------

def test_schedule_mode_does_not_bleed_into_brief_mode(monkeypatch) -> None:
    """week_schedule_planning must emit mode=schedule, not brief."""
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts",
                        lambda _id, _ceo: {"synthesizer": "Week plan output"})
    _stub_planner(monkeypatch, "schedule_planning")

    interaction = _interaction(10, "plan my week schedule")
    response = build_assistant_message_response(interaction, _user())

    assert response.presentation is not None
    assert response.presentation.mode == "schedule"


def test_weekly_recap_does_not_bleed_into_morning_brief_mode(monkeypatch) -> None:
    """weekly_recap must emit mode=brief with variant=weekly_recap, not morning_brief."""
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts",
                        lambda _id, _ceo: {"synthesizer": "Week recap output"})
    _stub_planner(monkeypatch, "weekly_recap")

    interaction = _interaction(11, "recap my week")
    response = build_assistant_message_response(interaction, _user())

    assert response.workflow_type == "weekly_recap"
    assert response.presentation is not None
    assert response.presentation.mode == "brief"


def test_meeting_prep_is_brief_not_report(monkeypatch) -> None:
    """meeting_prep must emit mode=brief, not report (old default)."""
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts",
                        lambda _id, _ceo: {"synthesizer": "Meeting prep content"})
    _stub_planner(monkeypatch, "meeting_prep")

    interaction = _interaction(12, "prep for my meeting with the board")
    response = build_assistant_message_response(interaction, _user())

    assert response.presentation is not None
    assert response.presentation.mode == "brief", (
        f"meeting_prep must render as 'brief', got {response.presentation.mode!r} — "
        "frontend would use ReportRenderer instead of BriefRenderer"
    )


# ---------------------------------------------------------------------------
# 10. _presentation_mode/_presentation_variant delegate to WORKFLOW_PROFILES
# ---------------------------------------------------------------------------

def test_presentation_mode_uses_workflow_profiles_not_hardcoded_list() -> None:
    """Regression: _presentation_mode must not be a hardcoded list that misses new types."""
    agent = _agent()
    expected = {
        "email_watcher": "brief",
        "email_ingestion": "brief",
        "calendar_briefing": "calendar",
        "morning_brief": "brief",
        "weekly_recap": "brief",
        "meeting_prep": "brief",
        "schedule_planning": "schedule",
    }
    for wf, mode in expected.items():
        result = agent._presentation_mode(wf)  # type: ignore[attr-defined]
        assert result == mode, f"{wf}: _presentation_mode returned {result!r}, expected {mode!r}"


def test_presentation_variant_uses_workflow_profiles_not_hardcoded_list() -> None:
    agent = _agent()
    expected = {
        "email_watcher": "inbox_watch",
        "email_ingestion": "inbox_watch",
        "calendar_briefing": "day_grid",
        "morning_brief": "weekly_watch",
        "weekly_recap": "weekly_recap",
        "meeting_prep": "meeting_prep",
        "schedule_planning": "timeline",
    }
    for wf, variant in expected.items():
        result = agent._presentation_variant(wf, ceo_id="ceo_test")  # type: ignore[attr-defined]
        assert result == variant, f"{wf}: _presentation_variant returned {result!r}, expected {variant!r}"


# ---------------------------------------------------------------------------
# _apply_presentation_metadata — section count edge cases
# ---------------------------------------------------------------------------

class TestApplyPresentationMetadataSectionEdgeCases:
    """
    _apply_presentation_metadata must not duplicate or skip sections based on
    the number of sections the agent produced. These tests lock in the mapping:

        n=1  → priorities only (no recommended_actions, risks, details)
        n=2  → priorities + recommended_actions (s[0] and s[-1] are distinct)
        n=3  → priorities, risks, recommended_actions (s[0], s[1], s[2])
        n=4+ → all four buckets populated, s[2:-1] lands in details
    """

    def _make_payload(self, sections: list[dict]) -> BriefPayload:
        from src.agents.briefing_agent import BriefAnswer, BriefSection, BriefTrust
        return BriefPayload(
            answer=BriefAnswer(
                title="Test",
                summary="summary",
                sections=[BriefSection(**s) for s in sections],
            ),
            trust=BriefTrust(
                confidence="medium",
                confidence_score=0.6,
                assumptions=[],
                open_questions=[],
                data_quality="medium",
                calculation_used=False,
                missing_context=[],
            ),
        )

    def _apply(self, payload: BriefPayload) -> BriefPayload:
        agent = _agent()
        return agent._apply_presentation_metadata(  # type: ignore[attr-defined]
            payload,
            event_payload={},
            workflow_type="email_watcher",
        )

    def test_zero_sections_produces_empty_buckets(self) -> None:
        payload = self._apply(self._make_payload([]))
        assert payload.presentation.priorities == []
        assert payload.presentation.recommended_actions == []
        assert payload.presentation.risks == []
        assert payload.presentation.details == []

    def test_one_section_goes_to_priorities_only(self) -> None:
        payload = self._apply(self._make_payload([{"label": "Key Threads", "items": ["a"]}]))
        assert len(payload.presentation.priorities) == 1
        assert payload.presentation.recommended_actions == []
        assert payload.presentation.risks == []
        assert payload.presentation.details == []

    def test_two_sections_no_duplication(self) -> None:
        payload = self._apply(self._make_payload([
            {"label": "Priorities", "items": ["p"]},
            {"label": "Actions", "items": ["a"]},
        ]))
        assert len(payload.presentation.priorities) == 1
        assert len(payload.presentation.recommended_actions) == 1
        assert payload.presentation.priorities[0].title != payload.presentation.recommended_actions[0].title
        assert payload.presentation.risks == []
        assert payload.presentation.details == []

    def test_three_sections_all_distinct(self) -> None:
        payload = self._apply(self._make_payload([
            {"label": "Priorities", "items": ["p"]},
            {"label": "Risks", "items": ["r"]},
            {"label": "Actions", "items": ["a"]},
        ]))
        assert len(payload.presentation.priorities) == 1
        assert len(payload.presentation.risks) == 1
        assert len(payload.presentation.recommended_actions) == 1
        assert payload.presentation.details == []
        titles = {
            payload.presentation.priorities[0].title,
            payload.presentation.risks[0].title,
            payload.presentation.recommended_actions[0].title,
        }
        assert len(titles) == 3, "all three buckets must point to different sections"

    def test_four_sections_details_populated(self) -> None:
        payload = self._apply(self._make_payload([
            {"label": "Priorities", "items": ["p"]},
            {"label": "Risks", "items": ["r"]},
            {"label": "Context", "items": ["c"]},
            {"label": "Actions", "items": ["a"]},
        ]))
        assert len(payload.presentation.priorities) == 1
        assert len(payload.presentation.risks) == 1
        assert len(payload.presentation.details) == 1
        assert len(payload.presentation.recommended_actions) == 1
        assert payload.presentation.details[0].title == "Context"
        assert payload.presentation.recommended_actions[0].title == "Actions"


# ---------------------------------------------------------------------------
# _generate_payload — malformed completion fallback
# ---------------------------------------------------------------------------

class TestGeneratePayloadCompletionFallback:
    """
    When the LLM returns a structurally invalid completion, _generate_payload
    must fall back to manual construction rather than raising, and the fallback
    must produce a valid BriefPayload (not None, not empty title).
    """

    def test_invalid_completion_falls_back_without_raising(self) -> None:
        agent = _agent()
        payload = agent._generate_payload(  # type: ignore[attr-defined]
            workflow_type="email_watcher",
            event_payload={
                "ranked_threads": [{"subject": "Budget", "importance_level": "high", "suppressed": False}],
                "structured_watch": {"asks": [], "deadlines": [], "implied_docs": []},
                "upcoming_events": [],
            },
            prepared_context={},
            completion={"bad_field": "this will fail BriefPayload(**completion)"},
        )
        assert isinstance(payload, BriefPayload)
        assert payload.answer.title
        assert payload.answer.summary

    def test_none_completion_produces_valid_payload(self) -> None:
        agent = _agent()
        payload = agent._generate_payload(  # type: ignore[attr-defined]
            workflow_type="email_watcher",
            event_payload={
                "ranked_threads": [{"subject": "Board update", "importance_level": "high", "suppressed": False}],
                "structured_watch": {"asks": [], "deadlines": [], "implied_docs": []},
                "upcoming_events": [],
            },
            prepared_context={},
            completion=None,
        )
        assert isinstance(payload, BriefPayload)
        assert payload.answer.title
        assert payload.trust is not None


# ---------------------------------------------------------------------------
# Null propagation from empty event payloads
# ---------------------------------------------------------------------------

class TestNullPropagationFromEmptyEventPayloads:
    """
    When WatchContextAssembler._safe_email/calendar() catches a provider error
    it returns {}. build_watch_event_payload must not store explicit None values
    for optional fields, because explicit None bypasses .get(key, default) — the
    caller gets None instead of the fallback string, producing "key • None" output.
    """

    def test_build_watch_event_payload_empty_email_omits_optional_none_fields(self) -> None:
        payload = build_watch_event_payload(email_event={}, calendar_event={})
        # These fields are optional — absent from the payload is correct.
        # Explicit None would poison downstream .get(key, default) calls.
        assert "sender" not in payload or payload["sender"] is not None
        assert "importance" not in payload or payload["importance"] is not None
        assert "importance_score" not in payload or payload["importance_score"] is not None
        assert "starts_at" not in payload or payload["starts_at"] is not None

    def test_build_watch_event_payload_empty_email_has_string_fallbacks(self) -> None:
        payload = build_watch_event_payload(email_event={}, calendar_event={})
        # Fields with defaults must never be None.
        assert isinstance(payload["title"], str) and payload["title"]
        assert isinstance(payload["subject"], str) and payload["subject"]
        assert isinstance(payload["importance_reasons"], list)
        assert isinstance(payload["related_threads"], list)
        assert isinstance(payload["attendees"], list)

    def test_build_watch_event_payload_present_fields_are_preserved(self) -> None:
        email_event = {
            "sender": "cfo@company.com",
            "importance": "high",
            "importance_score": 0.9,
            "subject": "Q1 review",
        }
        calendar_event = {
            "title": "Board Meeting",
            "starts_at": "2026-03-21T10:00:00",
            "upcoming_events": [],
        }
        payload = build_watch_event_payload(email_event=email_event, calendar_event=calendar_event)
        assert payload["sender"] == "cfo@company.com"
        assert payload["importance"] == "high"
        assert payload["importance_score"] == 0.9
        assert payload["starts_at"] == "2026-03-21T10:00:00"
        assert payload["subject"] == "Q1 review"
        assert payload["title"] == "Board Meeting"

    def test_calendar_briefing_section_no_none_string_on_empty_calendar(self) -> None:
        """Regression: 'Q1 Board Meeting • None' must never appear in section items."""
        payload = _build_payload(
            workflow_type="calendar_briefing",
            event_payload={
                "title": "Q1 Board Meeting",
                # starts_at intentionally absent (simulates empty calendar event)
                "related_threads": [],
                "ranked_threads": [],
                "upcoming_events": [],
                "attendees": [],
            },
        )
        all_items = [
            item
            for section in payload.answer.sections
            for item in (section.items or [])
        ]
        none_items = [item for item in all_items if "None" in str(item)]
        assert not none_items, f"Literal 'None' found in section items: {none_items}"

    def test_compound_workflow_with_empty_both_providers_produces_valid_payload(self) -> None:
        """Full path: both providers fail → both return {} → compound payload must be coherent."""
        event_payload = build_watch_event_payload(email_event={}, calendar_event={})
        payload = _build_payload(
            workflow_type="morning_brief",
            event_payload=event_payload,
        )
        assert isinstance(payload, BriefPayload)
        assert payload.answer.title
        assert payload.answer.summary
        assert payload.presentation is not None
        # Verify no "None" string leaked anywhere in text output
        import json
        serialized = json.dumps(payload.model_dump())
        assert '"None"' not in serialized, "Literal string 'None' found in serialized payload"

    def test_morning_brief_does_not_surface_out_of_window_meetings(self) -> None:
        payload = _build_payload(
            workflow_type="morning_brief",
            event_payload={
                "ranked_threads": _base_threads(),
                "structured_watch": {
                    "asks": [{"ask": "Review budget proposal by Friday"}],
                    "deadlines": [{"deadline": "Friday EOD — Q1 board deck"}],
                    "implied_meetings": [
                        {"meeting": "Q1 Board Meeting • 2026-04-05T10:00:00-07:00"},
                    ],
                    "implied_docs": [{"document": "Q1 board deck"}],
                },
                "upcoming_events": [
                    {
                        "title": "Q1 Board Meeting",
                        "starts_at": "2026-04-05T10:00:00-07:00",
                        "ends_at": "2026-04-05T11:00:00-07:00",
                        "attendees": ["board@company.com", "cfo@company.com"],
                    }
                ],
                "planning_context": {
                    "mode": "direct_workflow",
                    "time_horizon": "tomorrow",
                    "target_date": "2026-03-29",
                    "target_label": "Tomorrow",
                },
            },
        )

        meeting_items = []
        for section in payload.answer.sections:
            if section.label == "Upcoming Meetings":
                meeting_items = section.items
                break

        assert meeting_items[0] == "No meetings in today's window. Next up outside that window:"
        assert meeting_items[1].startswith("Next meeting: Q1 Board Meeting •")
        assert all("Q1 Board Meeting" not in item for item in meeting_items[:1])

    def test_schedule_planning_with_empty_providers_produces_valid_payload(self) -> None:
        event_payload = build_watch_event_payload(
            email_event={},
            calendar_event={},
            message="plan my day",
        )
        payload = _build_payload(
            workflow_type="schedule_planning",
            event_payload=event_payload,
        )
        assert isinstance(payload, BriefPayload)
        assert payload.answer.title
        all_items = [
            item
            for section in payload.answer.sections
            for item in (section.items or [])
        ]
        none_items = [item for item in all_items if "• None" in str(item) or item == "None"]
        assert not none_items, f"Literal 'None' found in schedule items: {none_items}"

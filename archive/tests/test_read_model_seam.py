"""
Read model seam tests.

Covers build_assistant_message_response() with all external I/O (DB, artifact
file system) monkeypatched. Each test crosses exactly one boundary: the read
model that transforms a persisted WorkflowRun + staged artifacts into an
AssistantMessageResponse for the frontend.

Seam: src.workflows.read_model.build_assistant_message_response

Test structure:
  1. Workflow-type inference from query text (one test per workflow)
  2. Parametrized inference across all workflow types
  3. Planner execution metadata surfacing
  4. Artifact metadata / binary-artifact ref handling
  5. Schedule alias normalization (legacy → canonical)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.api.schemas import TrustMetadata
from src.core.models import SessionInteraction, User
from src.workflows.interaction_persistence import serialize_interaction_response
from src.workflows.read_model import build_assistant_message_response


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _user() -> User:
    return User(id=1, username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")


def _interaction(id: int, query: str, status: str = "COMPLETED") -> SessionInteraction:
    return SessionInteraction(id=id, ceo_id="ceo_test", query=query, status=status)


# ---------------------------------------------------------------------------
# 1. Per-workflow read model mapping
# ---------------------------------------------------------------------------


def test_email_watcher_read_model_mapping(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda _id, _ceo: {"synthesizer": "Inbox brief text"},
    )
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(10, "scan my inbox"), _user())

    assert response.workflow_type == "email_watcher"
    assert response.answer.title == "Inbox Brief"


def test_calendar_briefing_read_model_mapping(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda _id, _ceo: {"synthesizer": "Calendar brief text"},
    )
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(20, "what meetings do I have today"), _user())

    assert response.workflow_type == "calendar_briefing"
    assert response.answer.title == "Calendar Brief"
    assert response.presentation.mode == "calendar"


def test_morning_brief_read_model_mapping(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda _id, _ceo: {"synthesizer": "Morning brief text"},
    )
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(30, "give me my morning brief"), _user())

    assert response.workflow_type == "morning_brief"
    assert response.answer.title == "Morning Brief"
    assert response.presentation.mode == "brief"


def test_weekly_recap_read_model_mapping(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda _id, _ceo: {"synthesizer": "Week recap text"},
    )
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(40, "recap my week"), _user())

    assert response.workflow_type == "weekly_recap"
    assert response.answer.title == "Week in Review"
    assert response.presentation.mode == "brief"


def test_meeting_prep_read_model_mapping(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda _id, _ceo: {"synthesizer": "Meeting prep text"},
    )
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(50, "prep for my meeting with the board"), _user())

    assert response.workflow_type == "meeting_prep"
    assert response.answer.title == "Meeting Brief"
    assert response.presentation.mode == "brief"


def test_schedule_planning_day_read_model_mapping(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda _id, _ceo: {"synthesizer": "Schedule text"},
    )
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(60, "plan my day for today"), _user())

    assert response.workflow_type == "schedule_planning"
    assert response.answer.title == "Executive Schedule"
    assert response.presentation.mode == "schedule"


def test_schedule_planning_week_read_model_mapping(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda _id, _ceo: {"synthesizer": "Week plan text"},
    )
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(70, "plan my week schedule"), _user())

    assert response.workflow_type == "schedule_planning"
    assert response.answer.title == "Executive Schedule"
    assert response.presentation.mode == "schedule"


def test_report_generation_read_model_mapping(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda _id, _ceo: {"synthesizer": "Report text"},
    )
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(80, "give me an AWS cost review"), _user())

    assert response.workflow_type == "report_generation"
    assert response.answer.title == "Executive Report"
    assert response.presentation.mode == "report"


def test_document_explanation_read_model_mapping(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda _id, _ceo: {"synthesizer": "Explanation text"},
    )
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(90, "explain the implications of this contract"), _user())

    assert response.workflow_type == "document_explanation"
    assert response.answer.title == "Business Implication Brief"
    assert response.presentation.mode == "report"


# ---------------------------------------------------------------------------
# 2. Parametrized: read model infers workflow type from query text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected_workflow",
    [
        ("recap my week", "weekly_recap"),
        ("week in review", "weekly_recap"),
        ("give me my morning brief", "morning_brief"),
        ("daily digest", "morning_brief"),
        ("scan my inbox", "email_watcher"),
        ("prep for my meeting with the board", "meeting_prep"),
        ("plan my week schedule", "schedule_planning"),
        ("plan my day for today", "schedule_planning"),
        ("explain this contract", "document_explanation"),
        ("give me a company health overview", "report_generation"),
    ],
)
def test_read_model_infers_workflow_type_from_query(query: str, expected_workflow: str, monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(100, query), _user())

    assert response.workflow_type == expected_workflow, (
        f"Query {query!r}: expected {expected_workflow!r}, got {response.workflow_type!r}"
    )


# ---------------------------------------------------------------------------
# 3. Schedule alias normalization: legacy → canonical
# ---------------------------------------------------------------------------


def test_read_model_normalizes_day_schedule_planning_alias(monkeypatch) -> None:
    """day_schedule_planning persisted in old records must surface as schedule_planning."""
    workflow_run = type(
        "WorkflowRunStub",
        (),
        {
            "response_data": {
                "conversation_id": "conv_test",
                "message_id": "msg_1",
                "workflow_type": "day_schedule_planning",
                "response_type": "report",
                "status": "completed",
                "answer": {"title": "Plan", "summary": "Ready", "sections": []},
                "trust": TrustMetadata().model_dump(),
                "sources": [],
                "artifacts": [],
                "metadata": {},
            },
            "state_data": {"metadata": {}},
        },
    )()

    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda interaction_id: workflow_run)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts", lambda _id, _ceo: {})
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})

    response = build_assistant_message_response(_interaction(200, "plan my day"), _user())

    assert response.workflow_type == "schedule_planning"


def test_read_model_semantic_fallback_maps_generic_schedule_request(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts", lambda _id, _ceo: {})
    monkeypatch.setattr("src.workflows.read_model.workflow_response_type", lambda workflow_type: "schedule")
    monkeypatch.setattr("src.workflows.read_model.workflow_title", lambda workflow_type: "Executive Schedule")
    monkeypatch.setattr(
        "src.workflows.read_model.default_presentation_payload",
        lambda workflow_type, summary=None: {"mode": "schedule", "summary": summary},
    )

    interaction = _interaction(171, "Make me a schedule")
    interaction.response = "Need horizon."
    response = build_assistant_message_response(interaction, _user())

    assert response.workflow_type == "schedule_planning"
    assert response.answer.title == "Executive Schedule"
    assert response.presentation.mode == "schedule"


def test_canonical_run_without_response_data_uses_persisted_workflow_truth(monkeypatch) -> None:
    workflow_run = type(
        "WorkflowRunStub",
        (),
        {
            "response_data": None,
            "workflow_type": "report_generation",
            "state_data": {
                "workflow_type": "report_generation",
                "metadata": {
                    "envelope_version": 2,
                    "semantic_source": "assistant_service",
                },
            },
        },
    )()

    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: workflow_run)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts", lambda _id, _ceo: {})
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifact_refs", lambda _id, _ceo: {})
    monkeypatch.setattr("src.workflows.read_model.workflow_response_type", lambda workflow_type: "report")
    monkeypatch.setattr("src.workflows.read_model.workflow_title", lambda workflow_type: "Executive Report")
    monkeypatch.setattr(
        "src.workflows.read_model.default_presentation_payload",
        lambda workflow_type, summary=None: {"mode": "report", "summary": summary},
    )

    interaction = _interaction(172, "plan my day for today")
    interaction.response = json.dumps(
        {
            "conversation_id": "conv_test",
            "message_id": "msg_172",
            "workflow_type": "schedule_planning",
            "response_type": "schedule",
            "status": "completed",
            "answer": {"title": "Wrong envelope", "summary": "Wrong", "sections": []},
            "trust": TrustMetadata().model_dump(),
            "sources": [],
            "artifacts": [],
            "metadata": {},
        }
    )

    response = build_assistant_message_response(interaction, _user())

    assert response.workflow_type == "report_generation"
    assert response.answer.title == "Executive Report"
    assert response.metadata["read_model_status"] == "canonical_envelope_missing"
    assert response.trust.missing_context == ["canonical_envelope_missing"]


def test_serialize_interaction_response_keeps_only_compatibility_summary() -> None:
    from src.api.schemas import AssistantMessageResponse

    response = AssistantMessageResponse(
        conversation_id="conv_test",
        message_id="msg_42",
        workflow_type="report_generation",
        response_type="clarification",
        status="completed",
        answer={"title": "Need one detail", "summary": "Choose the horizon.", "sections": []},
        trust={
            "question_options": [
                {
                    "question": "Which horizon should I use?",
                    "offer_type": "clarification",
                    "options": [
                        {"label": "Today", "value": "today", "apply_text": "Use today."},
                        {"label": "Next week", "value": "next_week", "apply_text": "Use next week."},
                    ],
                }
            ]
        },
        sources=[],
        artifacts=[{"artifact_type": "report_docx", "artifact_id": "interaction:42:report_docx", "label": "Memo"}],
        metadata={"original_query": "Make me a schedule", "envelope_version": 2, "semantic_source": "assistant_service", "ignored": "x"},
    )

    serialized = json.loads(serialize_interaction_response(response))

    assert serialized["conversation_id"] == "conv_test"
    assert serialized["workflow_type"] == "report_generation"
    assert serialized["response_type"] == "clarification"
    assert serialized["answer"] == {"title": "Need one detail", "summary": "Choose the horizon."}
    assert serialized["artifacts"] == [{"artifact_type": "report_docx", "label": "Memo"}]
    assert serialized["trust"]["question_options"][0]["question"] == "Which horizon should I use?"
    assert serialized["metadata"] == {
        "original_query": "Make me a schedule",
        "envelope_version": 2,
        "semantic_source": "assistant_service",
    }


# ---------------------------------------------------------------------------
# 4. Planner execution metadata surfacing
# ---------------------------------------------------------------------------


def test_read_model_preserves_planner_metadata_contract_safely(monkeypatch) -> None:
    workflow_run = type(
        "WorkflowRunStub",
        (),
        {
            "response_data": {
                "conversation_id": "conv_test",
                "message_id": "msg_1",
                "workflow_type": "day_schedule_planning",
                "response_type": "report",
                "status": "completed",
                "answer": {"title": "Plan", "summary": "Ready", "sections": []},
                "trust": TrustMetadata().model_dump(),
                "sources": [],
                "artifacts": [],
                "metadata": {},
            },
            "state_data": {
                "metadata": {
                    "planner_execution": {
                        "execution_mode": "carrier_workflow_with_planner_execution",
                        "planning_horizon": "next_week",
                        "executed_plan_steps": [{"key": "gather_email", "status": "completed", "details": {}}],
                        "evidence_summary": {"deadline_count": 1},
                        "sparse_guidance": False,
                        "planning_window": {"horizon": "next_week"},
                    }
                }
            },
        },
    )()

    import src.workflows.read_model as read_model_module

    original_loader = read_model_module._load_workflow_run
    read_model_module._load_workflow_run = lambda interaction_id: workflow_run  # type: ignore[assignment]
    try:
        from src.core.models import SessionInteraction
        interaction = SessionInteraction(id=1, ceo_id="ceo_test", query="plan next week", response="{}", status="COMPLETED")
        response = build_assistant_message_response(interaction, _user())
    finally:
        read_model_module._load_workflow_run = original_loader  # type: ignore[assignment]

    assert "planner_execution" in response.metadata
    assert response.metadata["planner_execution"]["planning_horizon"] == "next_week"


def test_read_model_exposes_planner_execution_metadata_without_schema_change(monkeypatch) -> None:
    interaction = _interaction(999, "Plan my day for tomorrow.", status="COMPLETED")
    interaction.current_stage = "complete"

    class FakeRun:
        response_data = None
        state_data = {
            "metadata": {
                "planner_execution": {
                    "execution_mode": "carrier_workflow_with_planner_execution",
                    "planning_horizon": "tomorrow",
                    "executed_plan_steps": [{"key": "gather_calendar", "status": "completed"}],
                    "evidence_summary": {"sparse_guidance": True},
                    "sparse_guidance": True,
                    "planning_window": {"horizon": "tomorrow"},
                }
            }
        }

    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda interaction_id: FakeRun())
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts", lambda interaction_id, ceo_id: {})

    response = build_assistant_message_response(interaction, current_user=_user())

    assert response.workflow_type == "schedule_planning"
    assert "planner_execution" in response.metadata
    assert response.metadata["planner_execution"]["planning_horizon"] == "tomorrow"


# ---------------------------------------------------------------------------
# 5. Artifact metadata handling
# ---------------------------------------------------------------------------


def test_read_model_artifacts_include_presentation_metadata(monkeypatch) -> None:
    interaction = _interaction(1001, "Prepare a board deck.", status="COMPLETED")
    interaction.current_stage = "complete"

    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda interaction_id: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda interaction_id, ceo_id: {"report_pptx_preview": "Board deck preview"},
    )
    monkeypatch.setattr(
        "src.workflows.read_model.read_stage_artifact_metadata",
        lambda interaction_id, ceo_id, stage: {
            "theme_id": "board_formal",
            "template_id": "board_deck_v1",
            "presentation_version": "deck_spec_v1",
            "ignored": "value",
        },
    )

    response = build_assistant_message_response(interaction, current_user=_user())

    artifact = response.artifacts[0]
    assert artifact.artifact_type == "report_pptx_preview"
    assert artifact.metadata["theme_id"] == "board_formal"
    assert artifact.metadata["template_id"] == "board_deck_v1"
    assert artifact.metadata["presentation_version"] == "deck_spec_v1"
    assert "ignored" not in artifact.metadata


def test_read_model_skips_binary_artifact_content_but_keeps_artifact_ref(monkeypatch, tmp_path) -> None:
    interaction = _interaction(1002, "Prepare the board memo.", status="COMPLETED")
    interaction.current_stage = "complete"

    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda interaction_id: None)
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifacts",
        lambda interaction_id, ceo_id: {"report_docx_preview": "# Board memo preview"},
    )
    monkeypatch.setattr(
        "src.workflows.read_model.hydrate_stage_artifact_refs",
        lambda interaction_id, ceo_id: {
            "report_docx": str(tmp_path / "board_memo.docx"),
            "report_docx_preview": str(tmp_path / "board_memo_preview.md"),
        },
    )
    monkeypatch.setattr("src.workflows.read_model.read_stage_artifact_metadata", lambda interaction_id, ceo_id, stage: {})

    response = build_assistant_message_response(interaction, current_user=_user())

    assert any(artifact.artifact_type == "report_docx" for artifact in response.artifacts)

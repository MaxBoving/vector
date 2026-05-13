from __future__ import annotations

from types import SimpleNamespace

from src.core.models import SessionInteraction, WorkflowRun
from src.workflows.context_loading import build_document_explanation_context_actions
from src.workflows.read_model import _infer_workflow_type


def test_infer_workflow_type_uses_persisted_attachments():
    interaction = SessionInteraction(ceo_id="ceo_test", query="What should I know from this?")
    workflow_run = WorkflowRun(
        workflow_id="wf_1",
        interaction_id=1,
        ceo_id="ceo_test",
        workflow_type="conversational",
        state_data={
            "metadata": {
                "attachments": [
                    {"document_id": "doc_1", "filename": "Contract.pdf"},
                ]
            }
        },
    )

    assert _infer_workflow_type(interaction, {}, workflow_run) == "document_explanation"


def test_infer_workflow_type_uses_response_presentation_metadata():
    interaction = SessionInteraction(ceo_id="ceo_test", query="Give me a recap of the week.")
    workflow_run = WorkflowRun(
        workflow_id="wf_2",
        interaction_id=2,
        ceo_id="ceo_test",
        workflow_type="",
        response_data={
            "workflow_type": "",
            "presentation": {
                "mode": "brief",
                "variant": "weekly_recap",
            },
            "answer": {
                "title": "Week in Review",
                "summary": "Summary",
                "sections": [],
            },
            "trust": {
                "confidence": "medium",
                "confidence_score": 0.5,
                "assumptions": [],
                "open_questions": [],
                "data_quality": "medium",
                "calculation_used": False,
                "missing_context": [],
            },
        },
    )

    assert _infer_workflow_type(interaction, {}, workflow_run) == "weekly_recap"


def test_infer_workflow_type_ignores_artifact_names_without_structured_metadata():
    interaction = SessionInteraction(ceo_id="ceo_test", query="hello")
    workflow_run = WorkflowRun(
        workflow_id="wf_3",
        interaction_id=3,
        ceo_id="ceo_test",
        workflow_type="",
        state_data={},
        response_data={},
    )

    assert _infer_workflow_type(interaction, {"morning_brief": "Morning Brief"}, workflow_run) == "conversational"


def test_document_explanation_retrieval_biases_attachment_ids():
    actions = build_document_explanation_context_actions(
        "Explain the attached contract before I sign it.",
        {
            "project_context": {"document_ids": ["doc_project_1"]},
            "attachments": [
                {"document_id": "doc_attachment_1", "filename": "Contract.pdf"},
                {"file_id": "doc_attachment_2", "filename": "Addendum.pdf"},
            ],
        },
    )

    semantic_action = next(action for action in actions if action.target == "semantic_search")
    assert semantic_action.args["preferred_document_ids"] == [
        "doc_project_1",
        "doc_attachment_1",
        "doc_attachment_2",
    ]

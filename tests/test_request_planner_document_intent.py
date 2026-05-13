from __future__ import annotations

from datetime import datetime

from src.workflows.request_planner import plan_request
from src.workflows.request_planner import IntentClassification
from src.workflows.types import WorkflowType


def test_document_only_requests_use_claude_classification(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.workflows.request_planner._classify_intent_semantic",
        lambda *args, **kwargs: IntentClassification(
            workflow=WorkflowType.DOCUMENT_EXPLANATION,
            mode="direct_workflow",
            needs_email=False,
            needs_calendar=False,
            needs_documents=True,
            time_horizon="unspecified",
            target_label=None,
            rationale="mock document classification",
        ),
    )

    cases = [
        "explain this contract",
        "what does this policy mean",
        "summarize the attached deck",
    ]

    for message in cases:
        plan = plan_request(message, has_attachments="attached" in message)
        assert plan.direct_workflow == WorkflowType.DOCUMENT_EXPLANATION, message
        assert plan.target_workflow == WorkflowType.DOCUMENT_EXPLANATION, message
        assert "documents" in plan.needed_context_sources, message


def test_llm_unavailable_defaults_to_conversational(monkeypatch) -> None:
    monkeypatch.setattr("src.workflows.request_planner._classify_intent_semantic", lambda *args, **kwargs: None)

    plan = plan_request("what does this mean?")

    assert plan.target_workflow == WorkflowType.CONVERSATIONAL
    assert plan.direct_workflow == WorkflowType.CONVERSATIONAL


def test_report_generation_classification_is_preserved(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.workflows.request_planner._classify_intent_semantic",
        lambda *args, **kwargs: IntentClassification(
            workflow=WorkflowType.REPORT_GENERATION,
            mode="direct_workflow",
            needs_email=False,
            needs_calendar=False,
            needs_documents=False,
            time_horizon="unspecified",
            target_label=None,
            rationale="mock report classification",
        ),
    )

    plan = plan_request("what do you know about our company right now?")

    assert plan.target_workflow == WorkflowType.REPORT_GENERATION
    assert plan.direct_workflow == WorkflowType.REPORT_GENERATION
    assert "email" in plan.needed_context_sources
    assert "calendar" in plan.needed_context_sources
    assert "documents" in plan.needed_context_sources
    assert plan.retrieval_plan.source_names == ["email", "calendar", "documents", "session_history", "signals"]


def test_morning_brief_tomorrow_uses_reference_date(monkeypatch) -> None:
    reference_dt = datetime.fromisoformat("2026-03-28T09:45:00-07:00")
    monkeypatch.setattr(
        "src.workflows.request_planner._classify_intent_semantic",
        lambda *args, **kwargs: IntentClassification(
            workflow=WorkflowType.MORNING_BRIEF,
            mode="direct_workflow",
            needs_email=True,
            needs_calendar=True,
            needs_documents=False,
            time_horizon="tomorrow",
            target_label="Tomorrow",
            rationale="mock morning brief classification",
        ),
    )

    plan = plan_request("Prepare a morning brief for tommorow.", reference_dt=reference_dt)

    assert plan.target_workflow == WorkflowType.MORNING_BRIEF
    assert plan.target_date is not None
    assert plan.target_date.isoformat() == "2026-03-29"
    assert plan.target_label == "Tomorrow"
    assert plan.requested_context_sources == ["email", "calendar", "signals"]

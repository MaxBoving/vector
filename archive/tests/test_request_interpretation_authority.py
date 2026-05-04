from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.schemas import AssistantQueryOptions, AssistantQueryRequest
from src.assistant.classification import classify_request_intent_async
from src.assistant.request_interpretation import (
    CandidateWorkflow,
    InterpretationStep,
    RequestInterpretation,
    build_request_interpretation,
)
from src.workflows.types import WorkflowType


def _payload(message: str) -> AssistantQueryRequest:
    return AssistantQueryRequest(
        message=message,
        conversation_id="conv_test",
        project_id=None,
        workflow_hint=None,
        attachments=[],
        options=AssistantQueryOptions(),
    )


def test_attachment_present_does_not_force_document_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"Board-ready burn analysis with attached backup",'
            '"mode":"single",'
            '"steps":[{"intent":"report_generation","kind":"analysis","requires":["company_state_read"],"approval_required":false}],'
            '"candidate_workflows":[{"name":"report_generation","confidence":0.92}],'
            '"needs_clarification":false,'
            '"risk_flags":[],'
            '"explanation":"Primary intent is analysis, attachments are supporting context."}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.REPORT_GENERATION,
            response_format="report",
            data_needed=["company_state"],
            action_requested=False,
            confidence=0.9,
            reasoning="report",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Use attached docs and build board-ready burn analysis",
            history=[],
            request_plan=None,
            has_attachments=True,
        )
    )
    assert interpretation.candidate_workflows[0].name == WorkflowType.REPORT_GENERATION
    assert interpretation.steps[0].intent == WorkflowType.REPORT_GENERATION


def test_classification_uses_interpretation_as_single_authority() -> None:
    interpretation = RequestInterpretation(
        request_id="r2",
        user_goal="Prep and draft follow-up",
        mode="compound",
        steps=[
            InterpretationStep(
                step_id="s1",
                intent=WorkflowType.MEETING_PREP,
                kind="plan",
                requires=["calendar_read", "brief_generation"],
                approval_required=False,
            ),
            InterpretationStep(
                step_id="s2",
                intent=WorkflowType.EMAIL_ACTION,
                kind="write_proposal",
                requires=["email_read", "email_draft"],
                approval_required=True,
            ),
        ],
        candidate_workflows=[
            CandidateWorkflow(name=WorkflowType.MEETING_PREP, confidence=0.9),
            CandidateWorkflow(name=WorkflowType.EMAIL_ACTION, confidence=0.74),
        ],
        needs_clarification=False,
        risk_flags=["contains_write_action"],
        explanation="compound",
    )

    payload = _payload("Prepare me and draft a follow-up")
    payload, intent = asyncio.run(
        classify_request_intent_async(
            payload,
            llm_router=SimpleNamespace(),
            interpretation=interpretation,
            history=[],
        )
    )
    assert intent.workflow_type == WorkflowType.MEETING_PREP
    assert intent.is_compound is True
    assert payload.workflow_hint == WorkflowType.MEETING_PREP


def test_service_source_removes_semantic_mutation_shortcuts() -> None:
    service_source = Path("src/assistant/service.py").read_text(encoding="utf-8")

    # artifact/report mode must not force workflow_hint-based semantic rewrite.
    assert "pin_to_report and not payload.workflow_hint" not in service_source

    # correction context should not silently hard-route ACT.
    assert "path\": \"correction_direct_action_precedence\"" not in service_source

    # post-classification semantic override function removed from main flow.
    assert "_apply_intent_to_request_intent(" not in service_source


def test_service_source_uses_typed_authority_and_not_raw_message_router() -> None:
    service_source = Path("src/assistant/service.py").read_text(encoding="utf-8")
    classification_source = Path("src/assistant/classification.py").read_text(encoding="utf-8")

    # downstream route decision should not re-read raw message via router.classify_and_route.
    assert "router.classify_and_route(payload.message" not in service_source

    # classification boundary should not call keyword route/planner fallbacks.
    assert "classify_route(" not in classification_source
    assert "keyword fallback" in classification_source


def test_post_classification_semantic_override_fails_loudly() -> None:
    service_source = Path("src/assistant/service.py").read_text(encoding="utf-8")

    assert "Semantic override blocked:" in service_source
    assert "request_intent.workflow_type not in allowed" in service_source
    assert "except RuntimeError:" in service_source
    assert "raise" in service_source


def test_direct_action_path_uses_typed_requirements_for_capability_selection() -> None:
    service_source = Path("src/assistant/service.py").read_text(encoding="utf-8")
    direct_actions_source = Path("src/workflows/direct_actions.py").read_text(encoding="utf-8")

    assert "typed_requirements=self._typed_capability_requirements(request_interpretation)" in service_source
    assert "_typed_requirements_allow_channel(typed_requirements, channel=\"calendar\")" in direct_actions_source
    assert "_typed_requirements_allow_channel(typed_requirements, channel=\"email\")" in direct_actions_source
    assert "if not bool(typed_requirements.get(\"contains_write_step\")):" in direct_actions_source


def test_turn_observability_fields_present() -> None:
    service_source = Path("src/assistant/service.py").read_text(encoding="utf-8")

    assert "\"request_id\"" in service_source
    assert "\"interpreted_primary_workflow\"" in service_source
    assert "\"selected_workflow_implementation\"" in service_source
    assert "\"replan_happened\"" in service_source
    assert "\"provenance_reason\"" in service_source
    assert "\"enforcement_mismatch\"" in service_source

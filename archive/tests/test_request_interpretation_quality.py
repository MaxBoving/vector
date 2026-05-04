from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.api.schemas import AssistantQueryOptions, AssistantQueryRequest
from src.assistant.classification import classify_request_intent_async
from src.assistant.request_interpretation import RequestInterpretation, build_request_interpretation
from src.workflows.planning_types import RequestPlan
from src.workflows.routing import RouteFamily
from src.workflows.types import WorkflowType


def _payload(message: str) -> AssistantQueryRequest:
    return AssistantQueryRequest(
        message=message,
        conversation_id="conv_quality",
        project_id=None,
        workflow_hint=None,
        attachments=[],
        options=AssistantQueryOptions(),
    )


def test_non_blocking_ambiguity_does_not_force_clarification(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"Need a summary","mode":"single",'
            '"steps":[{"intent":"report_generation","kind":"analysis","requires":["company_state_read"],"approval_required":false}],'
            '"candidate_workflows":[{"name":"report_generation","confidence":0.88}],'
            '"needs_clarification":true,"risk_flags":[],"explanation":"Can proceed with best effort."}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.REPORT_GENERATION,
            response_format="report",
            data_needed=["company_state"],
            action_requested=False,
            confidence=0.92,
            reasoning="analysis",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Can you give me a quick view of our cash posture?",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.needs_clarification is False


def test_analysis_requests_choose_best_fit_workflow_when_llm_unavailable(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        raise RuntimeError("llm unavailable")

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.0,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    plan = RequestPlan(
        mode="direct_workflow",
        target_workflow=WorkflowType.REPORT_GENERATION,
        direct_workflow=WorkflowType.REPORT_GENERATION,
        needed_context_sources=["company_state"],
        rationale="financial analysis",
    )
    interpretation = asyncio.run(
        build_request_interpretation(
            message="Analyze burn, runway, and margin variance for board prep.",
            history=[],
            request_plan=plan,
            has_attachments=False,
        )
    )
    assert interpretation.candidate_workflows[0].name == WorkflowType.REPORT_GENERATION
    assert interpretation.steps[0].intent == WorkflowType.REPORT_GENERATION


def test_workflow_normalization_preserves_semantic_intent(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"draft and send","mode":"single",'
            '"steps":[{"intent":"email_ingestion","kind":"unknown_kind","requires":["email_read"],"approval_required":true}],'
            '"candidate_workflows":[{"name":"email_ingestion","confidence":0.8}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"email action"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.EMAIL_ACTION,
            response_format="draft",
            data_needed=["email"],
            action_requested=True,
            confidence=0.8,
            reasoning="email",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Draft an email to the customer.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.EMAIL_ACTION
    assert interpretation.candidate_workflows[0].name == WorkflowType.EMAIL_ACTION
    assert interpretation.steps[0].kind == "write_proposal"


def test_primary_workflow_follows_step_intent_when_candidates_disagree(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"job_to_be_done":"prepare me for a meeting","user_goal":"meeting prep",'
            '"mode":"single",'
            '"steps":[{"intent":"meeting_prep","kind":"analysis","requires":["calendar_read"],"approval_required":false}],'
            '"candidate_workflows":[{"name":"report_generation","confidence":0.95},{"name":"meeting_prep","confidence":0.4}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"primary objective is prep"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.MEETING_PREP,
            response_format="report",
            data_needed=["calendar"],
            action_requested=False,
            confidence=0.7,
            reasoning="prep",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Get me ready for tomorrow's partner meeting.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.MEETING_PREP
    assert interpretation.steps[0].kind == "plan"
    assert interpretation.candidate_workflows[0].name == WorkflowType.MEETING_PREP


def test_route_family_mapping_consistent_with_interpreted_steps() -> None:
    interpretation = RequestInterpretation.model_validate(
        {
            "request_id": "q4",
            "user_goal": "Inbox scan",
            "mode": "single",
            "steps": [
                {
                    "step_id": "s1",
                    "intent": WorkflowType.EMAIL_WATCHER,
                    "kind": "watch",
                    "requires": ["email_read"],
                    "approval_required": False,
                }
            ],
            "candidate_workflows": [{"name": WorkflowType.EMAIL_WATCHER, "confidence": 0.9}],
            "needs_clarification": False,
            "risk_flags": [],
            "explanation": "watch",
        }
    )
    payload = _payload("What did I miss in inbox?")
    _, intent = asyncio.run(
        classify_request_intent_async(
            payload,
            llm_router=SimpleNamespace(),
            interpretation=interpretation,
            history=[],
        )
    )
    assert intent.workflow_type == WorkflowType.EMAIL_WATCHER
    assert intent.route_family == RouteFamily.WATCH


def test_advice_about_communication_does_not_trigger_act(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.3,
            reasoning="advice request",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Help me think through how to respond to legal about this issue.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].kind != "write_proposal"
    assert interpretation.candidate_workflows[0].name != WorkflowType.EMAIL_ACTION


def test_generic_write_like_phrasing_without_grounded_target_does_not_trigger_act(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="generic write-like phrasing",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Draft a board narrative for Q2 with recommendations.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].kind != "write_proposal"
    assert interpretation.candidate_workflows[0].name == WorkflowType.REPORT_GENERATION


def test_explicit_draft_proposal_request_enters_act_with_grounding(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Draft an email to legal about the renewal terms and cc finance.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].kind == "write_proposal"
    assert interpretation.candidate_workflows[0].name == WorkflowType.EMAIL_ACTION


def test_explicit_execution_request_still_enters_act_safely(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Schedule a follow-up call on Friday at 2pm with the investor team.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].kind == "write_proposal"
    assert interpretation.candidate_workflows[0].name == WorkflowType.CALENDAR_ACTION


def test_attachment_plus_board_summary_prefers_report_generation_over_document_explanation(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"board summary","mode":"single",'
            '"steps":[{"intent":"document_explanation","kind":"analysis","requires":["documents_read"],"approval_required":false}],'
            '"candidate_workflows":[{"name":"document_explanation","confidence":0.9}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"attachment"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.DOCUMENT_EXPLANATION,
            response_format="report",
            data_needed=["documents"],
            action_requested=False,
            confidence=0.9,
            reasoning="attachment",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    plan = RequestPlan(
        mode="direct_workflow",
        target_workflow=WorkflowType.REPORT_GENERATION,
        direct_workflow=WorkflowType.REPORT_GENERATION,
        needed_context_sources=["documents", "company_state"],
        rationale="board-ready synthesis",
    )
    interpretation = asyncio.run(
        build_request_interpretation(
            message="Use the attached workbook and make a board-ready summary.",
            history=[],
            request_plan=plan,
            has_attachments=True,
        )
    )
    assert interpretation.candidate_workflows[0].name == WorkflowType.REPORT_GENERATION
    assert interpretation.steps[0].intent == WorkflowType.REPORT_GENERATION


def test_plan_workflow_overrides_conversational_when_structure_is_non_conversational(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"meeting prep","mode":"single",'
            '"steps":[{"intent":"conversational","kind":"analysis","requires":[],"approval_required":false}],'
            '"candidate_workflows":[{"name":"conversational","confidence":0.91}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"generic"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.3,
            reasoning="generic",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)
    plan = RequestPlan(
        mode="direct_workflow",
        target_workflow=WorkflowType.MEETING_PREP,
        direct_workflow=WorkflowType.MEETING_PREP,
        needed_context_sources=["calendar", "documents"],
        rationale="prep",
    )
    interpretation = asyncio.run(
        build_request_interpretation(
            message="Prepare me for tomorrow's sales call.",
            history=[],
            request_plan=plan,
            has_attachments=False,
        )
    )
    assert interpretation.candidate_workflows[0].name == WorkflowType.MEETING_PREP
    assert interpretation.steps[0].intent == WorkflowType.MEETING_PREP


def test_calendar_planning_prompt_does_not_escalate_to_calendar_action(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="planning ask",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="I need help with my calendar next week.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.candidate_workflows[0].name != WorkflowType.CALENDAR_ACTION
    assert interpretation.steps[0].kind != "write_proposal"


def test_parsed_path_recalls_document_explanation_from_typed_plan_context(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"review attachment","mode":"single",'
            '"steps":[{"intent":"conversational","kind":"analysis","requires":[],"approval_required":false}],'
            '"candidate_workflows":[{"name":"conversational","confidence":0.92}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"generic"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.4,
            reasoning="generic",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)
    plan = RequestPlan(
        mode="direct_workflow",
        target_workflow=WorkflowType.DOCUMENT_EXPLANATION,
        direct_workflow=WorkflowType.DOCUMENT_EXPLANATION,
        needed_context_sources=["documents"],
        rationale="explain attached contract",
    )
    interpretation = asyncio.run(
        build_request_interpretation(
            message="Please explain this attached contract in plain English.",
            history=[],
            request_plan=plan,
            has_attachments=True,
        )
    )
    assert interpretation.candidate_workflows[0].name == WorkflowType.DOCUMENT_EXPLANATION
    assert interpretation.steps[0].intent == WorkflowType.DOCUMENT_EXPLANATION


def test_parsed_path_uses_proposal_oriented_calendar_act_selection(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"set up follow-up","mode":"single",'
            '"steps":[{"intent":"conversational","kind":"analysis","requires":[],"approval_required":false}],'
            '"candidate_workflows":[{"name":"conversational","confidence":0.89}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"generic"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="generic",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Draft a meeting invite proposal for Tuesday at 2pm with legal and finance.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.CALENDAR_ACTION
    assert interpretation.steps[0].kind == "write_proposal"
    assert interpretation.candidate_workflows[0].name == WorkflowType.CALENDAR_ACTION


def test_attachment_plus_action_set_up_interviews_routes_to_calendar_action(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.25,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="I attached the candidate packet. Set up interviews with the three finalists next week.",
            history=[],
            request_plan=None,
            has_attachments=True,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.CALENDAR_ACTION
    assert interpretation.steps[0].kind == "write_proposal"
    assert interpretation.candidate_workflows[0].name == WorkflowType.CALENDAR_ACTION


def test_attachment_plus_action_draft_update_send_to_investors_routes_to_email_action(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.25,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Use the attached strategy memo and draft a concise update I can send to investors.",
            history=[],
            request_plan=None,
            has_attachments=True,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.EMAIL_ACTION
    assert interpretation.steps[0].kind == "write_proposal"
    assert interpretation.candidate_workflows[0].name == WorkflowType.EMAIL_ACTION


def test_ambiguous_send_that_update_now_routes_to_email_action(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Can you send that update now?",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.EMAIL_ACTION
    assert interpretation.steps[0].kind == "write_proposal"
    assert interpretation.candidate_workflows[0].name == WorkflowType.EMAIL_ACTION


def test_ambiguous_no_email_book_meeting_prefers_calendar_action(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="No email. Book the meeting instead.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.CALENDAR_ACTION
    assert interpretation.steps[0].kind == "write_proposal"
    assert interpretation.candidate_workflows[0].name == WorkflowType.CALENDAR_ACTION


def test_attachment_plus_action_draft_email_not_forced_to_document_explanation(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Attached is the customer escalation. Draft a response email to the account owner and cc legal.",
            history=[],
            request_plan=None,
            has_attachments=True,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.EMAIL_ACTION
    assert interpretation.steps[0].kind == "write_proposal"


def test_explicit_policy_document_explain_without_attachment_routes_document_explanation(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Please explain this policy document and what actions it implies.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.DOCUMENT_EXPLANATION
    assert interpretation.steps[0].kind == "analysis"


def test_future_calendar_help_routes_schedule_planning_not_calendar_briefing(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CALENDAR_BRIEFING,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.6,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="I need help with my calendar next week.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.SCHEDULE_PLANNING
    assert interpretation.steps[0].kind == "plan"


def test_morning_focus_prompt_routes_morning_brief(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.SCHEDULE_PLANNING,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.4,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="What should I focus on first this morning?",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.MORNING_BRIEF
    assert interpretation.steps[0].kind == "watch"


def test_report_with_talking_points_stays_report_not_meeting_prep(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.REPORT_GENERATION,
            response_format="report",
            data_needed=[],
            action_requested=False,
            confidence=0.8,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Build a board-ready burn summary and include talking points for my Monday staff meeting.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.REPORT_GENERATION
    assert interpretation.steps[0].kind == "analysis"


def test_attachment_report_plus_schedule_keeps_report_primary(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.SCHEDULE_PLANNING,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.5,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)
    interpretation = asyncio.run(
        build_request_interpretation(
            message="I uploaded the deck. Summarize the key risks and schedule a review meeting for next Tuesday at 2pm.",
            history=[],
            request_plan=None,
            has_attachments=True,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.REPORT_GENERATION
    assert interpretation.steps[0].kind == "analysis"


def test_correction_analysis_only_routes_to_report_generation(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.1,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)
    interpretation = asyncio.run(
        build_request_interpretation(
            message="Correction: keep this as analysis only, do not send or schedule anything.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.REPORT_GENERATION
    assert interpretation.steps[0].kind == "analysis"


def test_correction_document_explanation_request_routes_document_explanation(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.1,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)
    interpretation = asyncio.run(
        build_request_interpretation(
            message="I changed my mind: this should be a document explanation of the attached memo.",
            history=[],
            request_plan=None,
            has_attachments=True,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.DOCUMENT_EXPLANATION
    assert interpretation.steps[0].kind == "analysis"


def test_draft_for_legal_routes_email_action_not_report(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.1,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)
    interpretation = asyncio.run(
        build_request_interpretation(
            message="Can you draft something for legal about this issue?",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.EMAIL_ACTION
    assert interpretation.steps[0].kind == "write_proposal"


def test_attachment_briefing_plus_calendar_followup_prefers_meeting_prep_primary(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"brief and follow-up","mode":"compound",'
            '"steps":[{"intent":"report_generation","kind":"analysis","requires":["documents_read"],"approval_required":false},'
            '{"intent":"calendar_action","kind":"plan","requires":["calendar_write"],"approval_required":true}],'
            '"candidate_workflows":[{"name":"report_generation","confidence":0.9},{"name":"calendar_action","confidence":0.85}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"compound"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.DOCUMENT_EXPLANATION,
            response_format="report",
            data_needed=["documents"],
            action_requested=False,
            confidence=0.9,
            reasoning="attachment",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    plan = RequestPlan(
        mode="compound_plan",
        target_workflow=WorkflowType.SCHEDULE_PLANNING,
        direct_workflow=None,
        needed_context_sources=["calendar", "documents"],
        rationale="briefing then follow-up meeting",
    )
    interpretation = asyncio.run(
        build_request_interpretation(
            message="With these attachments, build a briefing and then put a 30-minute follow-up on my calendar.",
            history=[],
            request_plan=plan,
            has_attachments=True,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.MEETING_PREP


def test_missed_updates_prompt_prefers_weekly_recap_over_morning_brief(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Give me the important updates I might have missed.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.WEEKLY_RECAP


def test_report_or_quick_answer_prompt_prefers_conversational(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.REPORT_GENERATION,
            response_format="report",
            data_needed=[],
            action_requested=False,
            confidence=0.4,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Should this be a report or just a quick answer?",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.CONVERSATIONAL


def test_followup_call_with_talking_points_prefers_meeting_prep_over_schedule_planning(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.SCHEDULE_PLANNING,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.6,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Can you coordinate a follow-up call and prepare talking points?",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.MEETING_PREP


def test_decision_support_spend_prompt_prefers_report_generation(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.2,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="I need help deciding if we should cut spend this quarter.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.REPORT_GENERATION


def test_correction_force_report_mode_prefers_conversational(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.REPORT_GENERATION,
            response_format="report",
            data_needed=[],
            action_requested=False,
            confidence=0.4,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Don't force report mode, just answer conversationally.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.CONVERSATIONAL


def test_day_level_prepare_prompt_prefers_schedule_planning_over_meeting_prep(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.MEETING_PREP,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.8,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Help me prepare for my day; I have multiple calls and open tasks.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.SCHEDULE_PLANNING


def test_follow_up_call_with_talking_points_stays_meeting_prep_on_parsed_path(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"coordinate and prep","mode":"single",'
            '"steps":[{"intent":"schedule_planning","kind":"plan","requires":["calendar_read"],"approval_required":false}],'
            '"candidate_workflows":[{"name":"schedule_planning","confidence":0.9},{"name":"meeting_prep","confidence":0.8}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"compound planning"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.SCHEDULE_PLANNING,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.7,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Can you coordinate a follow-up call and prepare talking points?",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.MEETING_PREP


def test_weekly_recap_then_plan_priorities_prefers_schedule_planning(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.WEEKLY_RECAP,
            response_format="report",
            data_needed=[],
            action_requested=False,
            confidence=0.9,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Give me a weekly recap and then plan my top priorities for next week.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.SCHEDULE_PLANNING


def test_runway_analysis_plus_board_prep_keeps_report_primary(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.REPORT_GENERATION,
            response_format="report",
            data_needed=[],
            action_requested=False,
            confidence=0.9,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Analyze runway scenarios and then give me talking points for my Monday board prep call.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.REPORT_GENERATION


def test_attachment_interview_invites_prefers_calendar_action(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.SCHEDULE_PLANNING,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.7,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="I uploaded candidate scorecards. Coordinate interviews and send invites for next week.",
            history=[],
            request_plan=None,
            has_attachments=True,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.CALENDAR_ACTION


def test_plain_language_explanation_of_attached_policy_routes_document_explanation(monkeypatch) -> None:
    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.CONVERSATIONAL,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.3,
            reasoning="fallback",
        )

    monkeypatch.setattr("src.assistant.request_interpretation._llm_available", lambda: False)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Write a plain-language explanation of the attached policy addendum.",
            history=[],
            request_plan=None,
            has_attachments=True,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.DOCUMENT_EXPLANATION


def test_close_score_cross_family_arbitration_uses_typed_intent_precedence(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"decision memo plus prep brief","mode":"compound",'
            '"steps":[{"intent":"meeting_prep","kind":"plan","requires":["calendar_read"],"approval_required":false}],'
            '"candidate_workflows":[{"name":"meeting_prep","confidence":0.79},{"name":"report_generation","confidence":0.76}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"both prep and analysis requested"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.MEETING_PREP,
            response_format="report",
            data_needed=["calendar"],
            action_requested=False,
            confidence=0.8,
            reasoning="prep",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="I need a decision memo on margin compression and a prep brief for Friday's partner meeting.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.REPORT_GENERATION
    arbitration = (interpretation.provenance or {}).get("arbitration") or {}
    assert "applied" in arbitration


def test_compound_plan_then_draft_email_keeps_plan_primary(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"weekly priorities and delegation","mode":"compound",'
            '"steps":[{"intent":"schedule_planning","kind":"plan","requires":["calendar_read"],"approval_required":false},'
            '{"intent":"email_action","kind":"write_proposal","requires":["email_draft"],"approval_required":true}],'
            '"candidate_workflows":[{"name":"schedule_planning","confidence":0.9},{"name":"email_action","confidence":0.9}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"compound plan plus draft"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.SCHEDULE_PLANNING,
            response_format="conversational",
            data_needed=[],
            action_requested=False,
            confidence=0.9,
            reasoning="planning",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Give me a weekly priorities plan, then draft a delegation email for ops.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.SCHEDULE_PLANNING


def test_report_style_weekly_summary_with_priorities_prefers_weekly_recap(monkeypatch) -> None:
    async def _mock_complete_async(self, prompt: str, system: str) -> str:  # noqa: ARG001
        return (
            '{"user_goal":"weekly summary with priorities","mode":"single",'
            '"steps":[{"intent":"weekly_recap","kind":"watch","requires":["email_read"],"approval_required":false}],'
            '"candidate_workflows":[{"name":"weekly_recap","confidence":0.95},{"name":"report_generation","confidence":0.95}],'
            '"needs_clarification":false,"risk_flags":[],"explanation":"weekly summary with report style"}'
        )

    async def _mock_intent_classify(self, message: str, history=None, has_attachments: bool = False):  # noqa: ARG001
        from src.assistant.intent_classifier import ClassifiedIntent

        return ClassifiedIntent(
            workflow=WorkflowType.WEEKLY_RECAP,
            response_format="report",
            data_needed=[],
            action_requested=False,
            confidence=0.95,
            reasoning="weekly recap",
        )

    monkeypatch.setattr("src.core.llm.LLMClient.complete_async", _mock_complete_async)
    monkeypatch.setattr("src.assistant.intent_classifier.IntentClassifier.classify", _mock_intent_classify)

    interpretation = asyncio.run(
        build_request_interpretation(
            message="Give me a report-style weekly summary with priorities.",
            history=[],
            request_plan=None,
            has_attachments=False,
        )
    )
    assert interpretation.steps[0].intent == WorkflowType.WEEKLY_RECAP

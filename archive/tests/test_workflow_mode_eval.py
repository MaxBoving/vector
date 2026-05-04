"""
Workflow mode eval suite.

Covers BriefingAgent domain behavior and runner e2e for every active workflow type:
  email_watcher, calendar_briefing, morning_brief, weekly_recap,
  meeting_prep, schedule_planning, report_generation, document_explanation.

Test structure per mode:
  1. BriefingAgent unit    — sections, title, summary, trust from inline event payloads
  2. Runner e2e            — mock context stages + agent, assert response shape

Classification, read model, and contract parity tests live in dedicated seam files:
  tests/test_classifier_seam.py, tests/test_read_model_seam.py,
  tests/test_message_contract_parity.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agents.briefing_agent import BriefingAgent
from src.agents.schemas import AgentOutput
from src.api.schemas import AnswerPayload, AnswerSection, AssistantMessageResponse, AssistantQueryRequest, TrustMetadata
from src.core.models import SessionInteraction, User
from src.workflows.request_planner import IntentClassification
from src.workflows.routing import RouteFamily
from src.workflows.runner import AssistantWorkflowRunner, detect_artifact_type_from_request
from src.workflows.runner_semantics import build_turn_semantic_bundle
from src.workflows.types import WorkflowType
from src.assistant.service import AssistantService
from src.assistant.types import RequestIntent
from src.workflows.intent_state import IntentState


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _user() -> User:
    return User(id=1, username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")


def _interaction(interaction_id: int, query: str, status: str = "COMPLETED") -> SessionInteraction:
    return SessionInteraction(id=interaction_id, ceo_id="ceo_test", query=query, status=status)


def _trust_dump() -> dict:
    return TrustMetadata().model_dump()


def _fake_briefing_output(stage: str, title: str = "Brief", mode: str = "brief") -> AgentOutput:
    return AgentOutput(
        agent_name="briefing_agent",
        stage=stage,
        success=True,
        summary=title,
        structured_output={
            "answer": {"title": title, "summary": f"{title} ready.", "sections": []},
            "trust": _trust_dump(),
            "sources": [],
        },
        metadata={},
    )


def _make_runner(email_event: dict | None = None, calendar_event: dict | None = None) -> AssistantWorkflowRunner:
    """Build a runner with context stages no-op'd and briefing agent faked."""
    runner = AssistantWorkflowRunner()
    runner.runtime._execute_context_stage = lambda **kwargs: None  # type: ignore[method-assign]

    async def _fake_briefing(agent_input, **kwargs):
        return _fake_briefing_output(agent_input.stage)

    runner.runtime.agents["briefing_agent"].run = _fake_briefing  # type: ignore[method-assign]
    runner._safe_fetch_email_event = lambda u: email_event or {  # type: ignore[method-assign]
        "ranked_threads": [
            {
                "subject": "Q1 budget review",
                "latest_sender": "cfo@company.com",
                "importance_level": "high",
                "importance_score": 88,
                "suppressed": False,
                "category": "finance",
                "importance_reasons": ["Pending board approval"],
            }
        ],
        "structured_watch": {
            "asks": [{"ask": "Review budget proposal"}],
            "deadlines": [{"deadline": "Friday EOD"}],
            "implied_docs": [],
        },
    }
    runner._safe_fetch_calendar_event = lambda u: calendar_event or {  # type: ignore[method-assign]
        "upcoming_events": [
            {
                "title": "Q1 Board Meeting",
                "starts_at": "2026-03-20T10:00:00",
                "attendees": ["board@company.com", "cfo@company.com"],
            }
        ]
    }
    return runner


# ---------------------------------------------------------------------------
# 1. EMAIL WATCHER
# ---------------------------------------------------------------------------


def test_email_watcher_briefing_sections_use_inbox_threads() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {
                "subject": "Investor update",
                "importance_level": "high",
                "latest_sender": "investor@fund.com",
                "importance_reasons": ["Awaiting reply from CEO"],
                "category": "investor",
            },
            {
                "subject": "Contract renewal",
                "importance_level": "medium",
                "latest_sender": "counsel@vendor.com",
                "importance_reasons": ["Review needed before Friday deadline"],
                "category": "legal",
            },
        ],
        "structured_watch": {
            "asks": [{"ask": "Reply to investor"}],
            "deadlines": [{"deadline": "Monday"}],
            "implied_docs": [],
        },
        "upcoming_events": [],
    }

    sections = agent._build_sections(  # type: ignore[attr-defined]
        workflow_type="email_watcher",
        items=["Investor update (High importance)", "Contract renewal (Medium importance)"],
        ranked_threads=event_payload["ranked_threads"],
        structured_watch=event_payload["structured_watch"],
        event_payload=event_payload,
    )
    labels = [s.label for s in sections]

    assert "Top Threads" in labels
    assert "Action Required" in labels
    assert "Waiting On Reply" in labels
    assert "Suggested Actions" in labels
    top = next(s for s in sections if s.label == "Top Threads")
    action_required = next(s for s in sections if s.label == "Action Required")
    suggested = next(s for s in sections if s.label == "Suggested Actions")
    assert any("Investor" in item for item in top.items)
    assert any("CEO" in item or "delegate" in item.lower() for item in action_required.items)
    assert any("today" in item.lower() or "end of day" in item.lower() for item in suggested.items)


def test_schedule_followup_sections_switch_to_do_delegate_defer_shape() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {
                "subject": "Northstar renewal at risk",
                "importance_level": "high",
                "importance_score": 92,
                "latest_sender": "maya@northstarhealth.com",
                "importance_reasons": ["Revenue risk tied to this week"],
                "category": "customer",
            }
        ],
        "structured_watch": {
            "asks": [{"ask": "Finalize investor runway narrative"}],
            "deadlines": [{"deadline": "Board deck final review by 3 PM"}],
            "implied_docs": [{"document": "Board deck final review memo"}],
        },
        "upcoming_events": [
            {"title": "Investor check-in", "starts_at": "2026-03-25T16:00:00-07:00"},
        ],
        "planning_context": {"mode": "compound_plan", "time_horizon": "today"},
    }

    sections = agent._build_sections(  # type: ignore[attr-defined]
        workflow_type="schedule_planning",
        items=[],
        ranked_threads=event_payload["ranked_threads"],
        structured_watch=event_payload["structured_watch"],
        event_payload=event_payload,
        task_input="What can I safely defer or delegate without creating downstream risk?",
        history=[{"query": "What decisions need to be made before the investor check-in?"}],
    )

    schedule = next(s for s in sections if s.label == "Schedule Proposal")
    assert any(item.startswith("DO NOW | CEO") for item in schedule.items)
    assert any(item.startswith("DELEGATE |") for item in schedule.items)
    assert any(item.startswith("DEFER |") or item.startswith("DO NOT DEFER |") for item in schedule.items)


def test_schedule_followup_summary_switches_to_operating_sequence_language() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    summary = agent._schedule_summary_for_followup(  # type: ignore[attr-defined]
        {
            "upcoming_events": [{"title": "Investor check-in"}],
            "structured_watch": {"deadlines": [{"deadline": "Board deck final review by 3 PM"}]},
        },
        [{"subject": "Northstar renewal at risk", "importance_level": "high", "category": "customer"}],
        {"deadlines": [{"deadline": "Board deck final review by 3 PM"}]},
        "How should I sequence the investor call prep given everything else on my plate?",
    )

    assert summary.startswith("Sequence the day around")


def test_email_watcher_runner_e2e() -> None:
    runner = _make_runner()
    payload = AssistantQueryRequest(message="Scan my inbox.", conversation_id="conv_test")
    interaction = _interaction(10, payload.message)

    response = asyncio.run(runner.run(payload, interaction, _user()))

    assert response.workflow_type == WorkflowType.EMAIL_WATCHER
    assert response.status == "completed"
    assert response.answer is not None


def test_report_runner_sanitizes_fake_execution_claims_end_to_end() -> None:
    runner = _make_runner()

    async def _fake_runtime_run(*, definition, payload, interaction, current_user, routing_decision, extra_metadata):
        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=definition.workflow_type,
            response_type="report",
            status="completed",
            answer=AnswerPayload(
                title="Redwood Recovery Status",
                summary="The email will be executed within 15 minutes and the tasks are queued for immediate execution.",
                sections=[
                    AnswerSection(
                        label="CEO Must Own Today",
                        items=[
                            "James Okafor is automatically CC'd on the note.",
                            "The follow-up tasks are queued for immediate execution.",
                        ],
                    )
                ],
            ),
            trust=TrustMetadata(),
            sources=[],
            artifacts=[],
            metadata={},
        )

    runner.runtime.run = _fake_runtime_run  # type: ignore[method-assign]

    payload = AssistantQueryRequest(
        message="Build the Redwood recovery plan and next steps.",
        conversation_id="conv_test",
    )
    interaction = _interaction(91, payload.message)

    response = asyncio.run(runner.run(payload, interaction, _user()))

    combined = "\n".join(
        [response.answer.title, response.answer.summary, *[item for section in response.answer.sections for item in section.items]]
    ).lower()
    assert "queued for immediate execution" not in combined
    assert "will be executed within 15 minutes" not in combined
    assert "automatically cc'd" not in combined
    assert "prepared for immediate follow-through" in combined
    assert "included on the prepared draft" in combined


def test_report_runner_filters_invalid_question_options_end_to_end() -> None:
    runner = _make_runner()

    async def _fake_runtime_run(*, definition, payload, interaction, current_user, routing_decision, extra_metadata):
        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=definition.workflow_type,
            response_type="report",
            status="completed",
            answer=AnswerPayload(
                title="Pricing Analysis",
                summary="Need one clean clarification path.",
                sections=[],
            ),
            trust=TrustMetadata(
                question_options=[
                    {
                        "question": "Bad clarification",
                        "offer_type": "clarification",
                        "options": [{"label": "Only one", "value": "one", "apply_text": "one"}],
                    },
                    {
                        "question": "Valid binary choice",
                        "offer_type": "clarification",
                        "options": [
                            {"label": "Scenario A", "value": "a", "apply_text": "A"},
                            {"label": "Scenario B", "value": "b", "apply_text": "B"},
                        ],
                    },
                    {
                        "question": "Empty action offer",
                        "offer_type": "action_offer",
                        "options": [],
                    },
                    {
                        "question": "Valid binary choice",
                        "offer_type": "clarification",
                        "options": [
                            {"label": "Duplicate A", "value": "a2", "apply_text": "A2"},
                            {"label": "Duplicate B", "value": "b2", "apply_text": "B2"},
                        ],
                    },
                ]
            ),
            sources=[],
            artifacts=[],
            metadata={},
        )

    runner.runtime.run = _fake_runtime_run  # type: ignore[method-assign]

    payload = AssistantQueryRequest(
        message="Analyze pricing strategy and scenario impact.",
        conversation_id="conv_test",
    )
    interaction = _interaction(92, payload.message)

    response = asyncio.run(runner.run(payload, interaction, _user()))

    assert response.trust.question_options[0].question == "Valid binary choice"
    assert response.trust.question_options[0].offer_type == "clarification"
    assert response.trust.question_options[0].priority_score >= 5.0
    assert [option.label for option in response.trust.question_options[0].options] == ["Scenario A", "Scenario B"]


def test_report_runner_injects_execution_limit_for_direct_capability_question(monkeypatch) -> None:
    runner = AssistantWorkflowRunner()
    monkeypatch.setattr(
        "src.assistant.service.get_integration_statuses",
        lambda ceo_id: [
            {"service": "gmail", "connected": False},
            {"service": "outlook_mail", "connected": False},
            {"service": "google_calendar", "connected": False},
            {"service": "outlook_calendar", "connected": False},
        ],
    )

    response = AssistantMessageResponse(
        conversation_id="conv_test",
        message_id="msg_61",
        workflow_type=WorkflowType.REPORT_GENERATION,
        response_type="report",
        status="completed",
        answer=AnswerPayload(
            title="Email Draft to James Okafor",
            summary="Executive recovery email draft is ready.",
            sections=[
                AnswerSection(
                    label="Email Draft",
                    items=[
                        "Subject: Executive commitment on AI platform timeline",
                        "Hi James,\n\nI am reaching out directly regarding the launch delay.\n\nBest,\nDana",
                    ],
                ),
                AnswerSection(
                    label="Follow-Up Actions",
                    items=["Schedule the CEO-to-CTO call within 24 hours."],
                ),
            ],
        ),
        trust=TrustMetadata(),
        sources=[],
        artifacts=[],
        metadata={},
    )

    updated = AssistantService(runner)._inject_execution_capability_disclosure(
        response=response,
        message=(
            "I need the actual email text I can send right now. "
            "Can you actually send emails and schedule calls, or do I need to do this manually?"
        ),
        current_user=_user(),
    )

    assert updated.answer.sections[0].label == "Execution Limit"
    execution_text = "\n".join(updated.answer.sections[0].items).lower()
    assert "cannot send emails from this environment" in execution_text
    assert "cannot schedule calendar events from this environment" in execution_text
    assert "send it manually" in execution_text
    assert "book it manually" in execution_text
    assert updated.metadata["execution_unavailable"]["channels"] == ["email", "calendar"]
    assert "No writable email provider" in updated.answer.summary


def test_report_runner_matches_eval_capability_question_phrasing(monkeypatch) -> None:
    runner = AssistantWorkflowRunner()
    monkeypatch.setattr(
        "src.assistant.service.get_integration_statuses",
        lambda ceo_id: [
            {"service": "gmail", "connected": False},
            {"service": "google_calendar", "connected": False},
        ],
    )

    response = AssistantMessageResponse(
        conversation_id="conv_test",
        message_id="msg_62",
        workflow_type=WorkflowType.REPORT_GENERATION,
        response_type="report",
        status="completed",
        answer=AnswerPayload(
            title="Email Draft",
            summary="Draft is ready.",
            sections=[AnswerSection(label="Email Draft", items=["Subject: Test", "Hi James,\n\nBody\n\nBest,\nDana"])],
        ),
        trust=TrustMetadata(),
        sources=[],
        artifacts=[],
        metadata={},
    )

    updated = AssistantService(runner)._inject_execution_capability_disclosure(
        response=response,
        message=(
            "Tell me explicitly whether you can send it and schedule the follow-up call, "
            "or if you cannot execute those actions, say so immediately."
        ),
        current_user=_user(),
    )

    assert updated.answer.sections[0].label == "Execution Limit"
    assert "cannot send emails from this environment" in "\n".join(updated.answer.sections[0].items).lower()


def test_report_runner_injects_execution_limit_on_first_turn_mixed_request(monkeypatch) -> None:
    runner = AssistantWorkflowRunner()
    monkeypatch.setattr(
        "src.assistant.service.get_integration_statuses",
        lambda ceo_id: [
            {"service": "gmail", "connected": False},
            {"service": "outlook_mail", "connected": False},
            {"service": "google_calendar", "connected": False},
            {"service": "outlook_calendar", "connected": False},
        ],
    )

    response = AssistantMessageResponse(
        conversation_id="conv_test",
        message_id="msg_63",
        workflow_type=WorkflowType.REPORT_GENERATION,
        response_type="report",
        status="completed",
        answer=AnswerPayload(
            title="Highest-Risk Customer Issue",
            summary="Redwood is the highest-risk account.",
            sections=[AnswerSection(label="Key Finding", items=["Redwood renewal is at risk."])],
        ),
        trust=TrustMetadata(),
        sources=[],
        artifacts=[],
        metadata={},
    )

    updated = AssistantService(runner)._inject_execution_capability_disclosure(
        response=response,
        message=(
            "I need you to identify our highest-risk customer issue right now and draft an immediate outreach email. "
            "Then send it, copy the account owner, and schedule a follow-up call for tomorrow."
        ),
        current_user=_user(),
    )

    assert updated.answer.sections[0].label == "Execution Limit"
    execution_text = "\n".join(updated.answer.sections[0].items).lower()
    assert "cannot send emails from this environment" in execution_text
    assert "cannot schedule calendar events from this environment" in execution_text


def test_detect_artifact_type_from_request_matches_outreach_email_phrase() -> None:
    assert (
        detect_artifact_type_from_request(
            "I need you to identify our highest-risk customer issue and draft an immediate outreach email."
        )
        == "email"
    )


def test_detect_artifact_type_from_context_wrapped_followup_action_uses_visible_request() -> None:
    assert (
        detect_artifact_type_from_request(
            "[Context: Prior question: Make me a schedule for next week | Prior response: next week Schedule Proposal]\n\n"
            "Follow-up action: Draft an outreach email to Robert Ross with clear ownership and next steps."
        )
        == "email"
    )


def test_runner_interrupts_for_ambiguous_schedule_request(monkeypatch) -> None:
    class _NoLLM:
        def complete_structured(self, *args, **kwargs):
            raise RuntimeError("llm unavailable in unit test")

        def complete(self, *args, **kwargs):
            raise RuntimeError("llm unavailable in unit test")

    async def _no_route(*args, **kwargs):
        return None

    runner = _make_runner()
    monkeypatch.setattr("src.workflows.intent_state.LLMClient", lambda: _NoLLM())
    monkeypatch.setattr("src.workflows.request_planner._classify_intent_semantic", lambda *args, **kwargs: None)
    runner.llm_router.classify = _no_route  # type: ignore[method-assign]
    _fake_live_ctx = type(
        "LiveContext",
        (),
        {
            "model_dump": lambda self, mode=None: {
                "current_schedule": {"meetings": [], "blocks": [], "deadlines": []},
                "pending_actions": [],
                "open_decisions": [],
                "last_agent_contributions": [],
            }
        },
    )()
    monkeypatch.setattr("src.assistant.service.get_or_create_live_context", lambda ceo_id, conversation_id: _fake_live_ctx)
    monkeypatch.setattr("src.assistant.memory.get_or_create_live_context", lambda ceo_id, conversation_id: _fake_live_ctx)
    monkeypatch.setattr("src.assistant.enrichment.get_or_create_live_context", lambda ceo_id, conversation_id: _fake_live_ctx)
    monkeypatch.setattr("src.assistant.memory.get_ceo_preferences", lambda ceo_id: {})
    monkeypatch.setattr("src.assistant.memory.get_ceo_memories", lambda ceo_id, limit=12: [])
    monkeypatch.setattr("src.assistant.memory.get_recent_signals", lambda ceo_id, limit=6: [])
    monkeypatch.setattr("src.assistant.memory.persist_latest_intent_state", lambda **kwargs: None)
    monkeypatch.setattr("src.assistant.memory.persist_latest_unified_memory", lambda **kwargs: None)
    monkeypatch.setattr("src.assistant.memory.update_live_context", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.assistant.service.persist_interaction_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.runtime.engine.persist_interaction_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.assistant.memory.build_conversation_history", lambda **kwargs: [])

    payload = AssistantQueryRequest(message="Make me a schedule", conversation_id="conv_test")
    interaction = _interaction(92, payload.message, status="PENDING")

    response = asyncio.run(runner.run(payload, interaction, _user()))

    assert response.response_type == "clarification"
    assert response.metadata["needs_clarification"] is True
    assert response.metadata["question_kind"] == "schedule"
    assert response.metadata["blocking_gaps"] == ["planning horizon"]


def test_runner_does_not_interrupt_for_deadline_list_request(monkeypatch) -> None:
    class _NoLLM:
        def complete_structured(self, *args, **kwargs):
            raise RuntimeError("llm unavailable in unit test")

        def complete(self, *args, **kwargs):
            raise RuntimeError("llm unavailable in unit test")

    async def _no_route(*args, **kwargs):
        return None

    runner = _make_runner()
    monkeypatch.setattr("src.workflows.intent_state.LLMClient", lambda: _NoLLM())
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
            rationale="Deadline list request should stay on report generation.",
        ),
    )
    runner.llm_router.classify = _no_route  # type: ignore[method-assign]
    _fake_live_ctx2 = type(
        "LiveContext",
        (),
        {
            "model_dump": lambda self, mode=None: {
                "current_schedule": {},
                "pending_actions": [],
                "open_decisions": [],
                "last_agent_contributions": [],
            }
        },
    )()
    monkeypatch.setattr("src.assistant.service.get_or_create_live_context", lambda ceo_id, conversation_id: _fake_live_ctx2)
    monkeypatch.setattr("src.assistant.memory.get_or_create_live_context", lambda ceo_id, conversation_id: _fake_live_ctx2)
    monkeypatch.setattr("src.assistant.enrichment.get_or_create_live_context", lambda ceo_id, conversation_id: _fake_live_ctx2)
    monkeypatch.setattr("src.assistant.memory.get_ceo_preferences", lambda ceo_id: {})
    monkeypatch.setattr("src.assistant.memory.get_ceo_memories", lambda ceo_id, limit=12: [])
    monkeypatch.setattr("src.assistant.memory.get_recent_signals", lambda ceo_id, limit=6: [])
    monkeypatch.setattr("src.assistant.memory.persist_latest_intent_state", lambda **kwargs: None)
    monkeypatch.setattr("src.assistant.memory.persist_latest_unified_memory", lambda **kwargs: None)
    monkeypatch.setattr("src.assistant.memory.update_live_context", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.assistant.service.persist_interaction_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.runtime.engine.persist_interaction_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.assistant.memory.build_conversation_history", lambda **kwargs: [])

    payload = AssistantQueryRequest(message="Please identify a list of deadlines for the next two weeks", conversation_id="conv_test")
    interaction = _interaction(93, payload.message, status="PENDING")

    response = asyncio.run(runner.run(payload, interaction, _user()))

    assert response.response_type == "report"
    assert response.workflow_type == WorkflowType.REPORT_GENERATION


def test_runner_skips_llm_router_when_semantic_plan_is_already_high_confidence(monkeypatch) -> None:
    runner = _make_runner()
    bundle = build_turn_semantic_bundle(message="Make me a schedule for next week")
    payload = AssistantQueryRequest(message="Make me a schedule for next week", conversation_id="conv_test")
    interaction = _interaction(194, payload.message, status="PENDING")

    async def _boom(*args, **kwargs):
        raise AssertionError("llm router should have been skipped")

    runner.llm_router.classify = _boom  # type: ignore[method-assign]
    updated_payload, route = asyncio.run(
        AssistantService(runner)._classify_route(
            payload=payload,
            interaction=interaction,
            current_user=_user(),
            history=[],
            unified_memory={},
            semantic_bundle=bundle,
        )
    )

    assert updated_payload.workflow_hint is None
    assert route.route_family == RouteFamily.PLAN
    assert route.workflow_type == WorkflowType.SCHEDULE_PLANNING


def test_clarification_gate_uses_primary_classification_result(monkeypatch) -> None:
    runner = _make_runner()
    service = AssistantService(runner)
    captured: dict[str, object] = {}

    monkeypatch.setattr("src.assistant.service.build_conversation_history", lambda **kwargs: [])
    monkeypatch.setattr("src.assistant.service.load_previous_intent_state", lambda **kwargs: None)
    monkeypatch.setattr("src.assistant.service.get_or_create_live_context", lambda ceo_id, conversation_id: type("LiveContext", (), {"model_dump": lambda self, mode="json": {}})())
    monkeypatch.setattr("src.assistant.service.build_artifact_context", lambda **kwargs: {})
    monkeypatch.setattr("src.assistant.service.parse_turn_intent", lambda **kwargs: IntentState())
    monkeypatch.setattr("src.assistant.service.resolve_intent", lambda *args, **kwargs: IntentState(workflow_preference=WorkflowType.REPORT_GENERATION))
    monkeypatch.setattr("src.assistant.service.persist_intent_state", lambda **kwargs: None)
    monkeypatch.setattr("src.assistant.service.resolve_action_reference", lambda **kwargs: None)
    monkeypatch.setattr("src.assistant.service.build_and_persist_unified_memory", lambda **kwargs: {})
    monkeypatch.setattr("src.assistant.service.persist_pending_actions", lambda **kwargs: None)
    monkeypatch.setattr("src.assistant.service.persist_interaction_state", lambda *args, **kwargs: None)
    monkeypatch.setattr("src.runtime.engine.persist_interaction_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.assistant.service.resolve_artifact_mode",
        lambda **kwargs: type("ArtifactModeStub", (), {"pin_to_report": False, "as_meta_dict": lambda self: {}})(),
    )

    async def _fake_classify_route(**kwargs):
        return kwargs["payload"], RequestIntent(
            route_family=RouteFamily.PLAN,
            workflow_type=WorkflowType.SCHEDULE_PLANNING,
            rationale="schedule request",
        )

    async def _unexpected_runtime_run(**kwargs):
        raise AssertionError("runtime.run should not be called when clarification interrupts")

    def _fake_clarification(*, route_decision, **kwargs):
        captured["workflow_chain"] = list(route_decision.workflow_chain)
        captured["request_plan"] = route_decision.request_plan
        from src.workflows.clarification_policy import ClarificationDecision
        return ClarificationDecision(
            should_interrupt=True,
            reason="Need horizon.",
            blocking_gaps=["planning horizon"],
            question="Today or next week?",
            question_kind="schedule",
        )

    def _fake_runner_clarification_response(*, workflow_type, clarification, **kwargs):
        captured["workflow_type"] = workflow_type
        return AssistantMessageResponse(
            conversation_id="conv_test",
            message_id="msg_test",
            workflow_type=workflow_type,
            response_type="clarification",
            status="completed",
            answer=AnswerPayload(title="", summary="", sections=[]),
            trust=TrustMetadata(),
            sources=[],
            artifacts=[],
            metadata={"reason": clarification.reason},
        )

    service._classify_route = _fake_classify_route  # type: ignore[method-assign]
    runner.runtime.run = _unexpected_runtime_run  # type: ignore[method-assign]
    runner.runtime.build_runner_clarification_response = _fake_runner_clarification_response  # type: ignore[method-assign]
    monkeypatch.setattr("src.assistant.service.should_interrupt_for_clarification", _fake_clarification)

    payload = AssistantQueryRequest(message="Make me a schedule", conversation_id="conv_test")
    interaction = _interaction(301, payload.message, status="PENDING")

    response = asyncio.run(service.handle(payload=payload, interaction=interaction, current_user=_user()))

    assert captured["workflow_chain"] == [WorkflowType.SCHEDULE_PLANNING]
    assert captured["request_plan"] is None
    assert captured["workflow_type"] == WorkflowType.SCHEDULE_PLANNING
    assert response.response_type == "clarification"


def test_report_runner_persists_mutated_execution_disclosure(monkeypatch) -> None:
    runner = _make_runner()
    persisted: dict[str, str] = {}
    monkeypatch.setattr(
        "src.assistant.service.get_integration_statuses",
        lambda ceo_id: [
            {"service": "gmail", "connected": False},
            {"service": "google_calendar", "connected": False},
        ],
    )
    monkeypatch.setattr(
        "src.assistant.service.persist_interaction_state",
        lambda interaction_id, **kwargs: persisted.update({"response": kwargs.get("response", "")}),
    )

    async def _fake_runtime_run(*, definition, payload, interaction, current_user, routing_decision, extra_metadata):
        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=definition.workflow_type,
            response_type="report",
            status="completed",
            answer=AnswerPayload(
                title="Email Draft",
                summary="Draft is ready.",
                sections=[AnswerSection(label="Email Draft", items=["Subject: Test", "Hi Rachel,\n\nBody\n\nBest,\nDana"])],
            ),
            trust=TrustMetadata(),
            sources=[],
            artifacts=[],
            metadata=extra_metadata,
        )

    runner.runtime.run = _fake_runtime_run  # type: ignore[method-assign]

    payload = AssistantQueryRequest(
        message=(
            "Draft the email to Rachel Lim and tell me explicitly whether you can send it "
            "and schedule the follow-up call, or if you cannot execute those actions."
        ),
        conversation_id="conv_test",
    )
    interaction = _interaction(101, payload.message, status="PENDING")

    response = asyncio.run(runner.run(payload, interaction, _user()))

    assert "execution_unavailable" in response.metadata
    assert "execution_unavailable" in persisted["response"]
    assert "Execution Limit" in persisted["response"]


def test_report_runner_offers_google_or_outlook_connect_actions_for_integration_setup_question(monkeypatch) -> None:
    runner = AssistantWorkflowRunner()
    monkeypatch.setattr(
        "src.assistant.service.get_integration_statuses",
        lambda ceo_id: [
            {"service": "gmail", "connected": False},
            {"service": "outlook_mail", "connected": False},
            {"service": "google_calendar", "connected": False},
            {"service": "outlook_calendar", "connected": False},
        ],
    )

    response = AssistantMessageResponse(
        conversation_id="conv_test",
        message_id="msg_64",
        workflow_type=WorkflowType.REPORT_GENERATION,
        response_type="report",
        status="completed",
        answer=AnswerPayload(
            title="Executive Report",
            summary="Generic fallback summary.",
            sections=[],
        ),
        trust=TrustMetadata(),
        sources=[],
        artifacts=[],
        metadata={"execution_unavailable": {"channels": ["email", "calendar"]}},
    )

    updated = AssistantService(runner)._inject_integration_setup_guidance(
        response=response,
        message=(
            "If you truly cannot access my email and calendar systems, then tell me exactly which "
            "integrations I need to set up to make this work properly."
        ),
        current_user=_user(),
    )

    assert updated.answer.title == "Connect Email + Calendar to Enable Direct Execution"
    assert any(section.label == "Setup Options" for section in updated.answer.sections)
    assert updated.trust.question_options[0]["offer_type"] == "action_offer"
    assert [option["label"] for option in updated.trust.question_options[0]["options"]] == [
        "Google Workspace",
        "Microsoft Outlook",
    ]
    assert updated.metadata["integration_setup_required"]["providers"] == ["google_workspace", "microsoft_outlook"]


def test_briefing_agent_customer_offer_is_concrete_not_generic() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]

    offers = agent._build_briefing_action_offers(  # type: ignore[attr-defined]
        workflow_type="email_watcher",
        ranked_threads=[
            {
                "subject": "Redwood renewal escalation",
                "importance_level": "high",
                "action_required": True,
                "suppressed": False,
                "category": "customer",
            }
        ],
        task_input="What is on fire with our customer renewals today?",
        company_state={},
    )

    assert len(offers) == 1
    offer = offers[0]
    assert "draft the customer escalation note" in offer["question"].lower()
    assert {item["label"] for item in offer["options"]} == {"Draft escalation note", "Build risk brief"}


# ---------------------------------------------------------------------------
# 2. CALENDAR BRIEFING
# ---------------------------------------------------------------------------


def test_calendar_briefing_briefing_sections() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "title": "Q1 Board Meeting",
        "starts_at": "2026-03-20T10:00:00",
        "attendees": ["cfo@company.com"],
        "related_threads": [{"subject": "Pre-read deck", "importance_level": "high", "importance_reasons": ["Board agenda"]}],
        "upcoming_events": [],
    }

    sections = agent._build_sections(  # type: ignore[attr-defined]
        workflow_type="calendar_briefing",
        items=["Pre-read deck (High importance)"],
        ranked_threads=[],
        structured_watch={},
        event_payload=event_payload,
    )
    labels = [s.label for s in sections]

    assert "Upcoming Meetings" in labels
    assert "Related Threads" in labels
    assert "Suggested Follow-Ups" in labels



# ---------------------------------------------------------------------------
# 3. MORNING BRIEF
# ---------------------------------------------------------------------------


def test_morning_brief_briefing_sections_include_meetings() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {"subject": "Investor ask", "importance_level": "high"},
        ],
        "structured_watch": {
            "asks": [{"ask": "Reply to investor"}],
            "deadlines": [{"deadline": "Friday"}],
            "implied_docs": [],
        },
        "upcoming_events": [
            {"title": "Board Meeting", "starts_at": "2026-03-20T10:00:00"},
        ],
    }

    sections = agent._build_sections(  # type: ignore[attr-defined]
        workflow_type="morning_brief",
        items=["Investor ask (High importance)"],
        ranked_threads=event_payload["ranked_threads"],
        structured_watch=event_payload["structured_watch"],
        event_payload=event_payload,
    )
    labels = [s.label for s in sections]
    meetings = next(s for s in sections if s.label == "Upcoming Meetings")

    assert "Important Threads" in labels
    assert "Deadlines" in labels
    assert "Upcoming Meetings" in labels
    assert any("Board Meeting" in item for item in meetings.items)


def test_morning_brief_summary_counts_threads_and_meetings() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    summary = agent._morning_watch_summary(  # type: ignore[attr-defined]
        {
            "ranked_threads": [
                {"importance_level": "high"},
                {"importance_level": "medium"},
            ],
            "upcoming_events": [{"title": "Board"}, {"title": "1:1"}],
        }
    )
    assert "2 important threads" in summary
    assert "2 upcoming meetings" in summary


def test_morning_brief_friday_target_filters_today_follow_ups() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {"subject": "Board packet", "importance_level": "high"},
        ],
        "structured_watch": {
            "asks": [
                {"ask": "Markup board packet slides today."},
                {"ask": "Finalize board packet narrative by Friday."},
            ],
            "deadlines": [
                {"deadline": "Board packet CEO markup by 2026-03-29T12:00:00-07:00."},
                {"deadline": "Friday 5 PM"},
            ],
            "implied_docs": [],
        },
        "upcoming_events": [
            {"title": "Monday standup", "starts_at": "2026-03-30T10:00:00-07:00"},
            {"title": "Friday board review", "starts_at": "2026-04-03T15:00:00-07:00"},
        ],
        "planning_context": {
            "time_horizon": "this_week",
            "target_date": "2026-04-03",
            "target_label": "Friday this week",
        },
    }

    payload = agent._generate_payload(  # type: ignore[attr-defined]
        workflow_type="morning_brief",
        event_payload=event_payload,
        prepared_context={},
        completion=None,
        task_input="Prepare a morning brief for Friday this week",
    )

    follow_ups = next(section for section in payload.answer.sections if section.label == "Suggested Follow-Ups")
    deadlines = next(section for section in payload.answer.sections if section.label == "Deadlines")
    meetings = next(section for section in payload.answer.sections if section.label == "Upcoming Meetings")

    assert payload.answer.title == "Friday this week Watch Brief"
    assert "Friday this week" in payload.answer.summary
    assert all("today" not in item.lower() for item in follow_ups.items)
    assert all("today" not in item.lower() for item in deadlines.items)
    assert any("Friday board review" in item for item in meetings.items)
    assert all("Monday standup" not in item for item in meetings.items)
    assert all("2026-03-29" not in item for item in deadlines.items)
    assert all("march 29" not in item.lower() for item in deadlines.items)


def test_schedule_planning_next_week_keeps_answer_sections_aligned_with_weekly_plan() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {
                "subject": "Board packet narrative draft for close week",
                "importance_level": "high",
                "importance_score": 91,
                "latest_sender": "cfo@company.com",
                "suppressed": False,
                "category": "finance",
            }
        ],
        "structured_watch": {
            "asks": [
                {"ask": "Markup board packet slides 4, 9, and 12 by noon today."},
                {"ask": "Prepare board prep kick-off agenda by April 10."},
            ],
            "deadlines": [
                {"deadline": "Board packet CEO markup by 2026-03-29T12:00:00-07:00."},
                {"deadline": "Board prep readout by 2026-04-10T17:00:00-07:00."},
            ],
            "implied_docs": [{"document": "Board packet CEO narrative"}],
        },
        "upcoming_events": [
            {"title": "Q1 Finance close review", "starts_at": "2026-03-29T10:30:00-07:00"},
            {"title": "Cloud spend containment decision", "starts_at": "2026-03-29T13:30:00-07:00"},
            {"title": "GTM Director panel interview — Day 1", "starts_at": "2026-04-09T09:00:00-07:00"},
            {"title": "Board prep kick-off", "starts_at": "2026-04-10T11:00:00-07:00"},
        ],
        "planning_context": {
            "time_horizon": "next_week",
            "mode": "compound_plan",
        },
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
    meetings = next(section for section in payload.answer.sections if section.label == "Upcoming Meetings")
    follow_ups = next(section for section in payload.answer.sections if section.label == "Suggested Follow-Ups")
    weekly_plan = payload.presentation.weekly_plan

    assert weekly_plan is not None
    assert weekly_plan.planning_window is not None
    assert weekly_plan.planning_window.horizon == "next_week"
    assert weekly_plan.planning_window.start_date == "2026-04-06"
    assert weekly_plan.planning_window.end_date == "2026-04-10"
    assert all("2026-03-29" not in item for item in deadlines.items)
    assert any("2026-04-10" in item for item in deadlines.items)
    assert all("2026-03-29" not in item for item in meetings.items)
    assert any("2026-04-09" in item for item in meetings.items)
    assert all("today" not in item.lower() for item in follow_ups.items)
    assert all("march 29" not in item.lower() for item in deadlines.items)
    assert all("march 29" not in item.lower() for item in follow_ups.items)
    assert [meeting.title for meeting in weekly_plan.meetings] == [
        "GTM Director panel interview — Day 1",
        "Board prep kick-off",
    ]
    assert weekly_plan.deadlines == deadlines.items[:4]


def test_morning_brief_runner_e2e() -> None:
    runner = _make_runner()
    payload = AssistantQueryRequest(message="Give me my morning brief.", conversation_id="conv_test")
    interaction = _interaction(30, payload.message)

    response = asyncio.run(runner.run(payload, interaction, _user()))

    assert response.workflow_type == WorkflowType.MORNING_BRIEF
    assert response.status == "completed"



# ---------------------------------------------------------------------------
# 4. WEEKLY RECAP
# ---------------------------------------------------------------------------


def test_weekly_recap_briefing_sections() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {"subject": "Deal closed", "importance_level": "high"},
            {"subject": "Team update", "importance_level": "medium"},
        ],
        "structured_watch": {
            "asks": [{"ask": "Follow up with legal"}],
            "deadlines": [{"deadline": "EOW"}],
            "implied_docs": [],
        },
        "upcoming_events": [
            {"title": "Leadership Sync", "starts_at": "2026-03-18T09:00:00"},
            {"title": "Investor Call", "starts_at": "2026-03-19T14:00:00"},
        ],
    }

    sections = agent._build_sections(  # type: ignore[attr-defined]
        workflow_type="weekly_recap",
        items=["Deal closed (High importance)", "Team update (Medium importance)"],
        ranked_threads=event_payload["ranked_threads"],
        structured_watch=event_payload["structured_watch"],
        event_payload=event_payload,
    )
    labels = [s.label for s in sections]

    assert "This Week's Threads" in labels
    assert "Meetings Held" in labels
    assert "Deadlines & Commitments" in labels
    assert "Still Open" in labels
    threads_section = next(s for s in sections if s.label == "This Week's Threads")
    assert any("Deal closed" in item for item in threads_section.items)
    meetings_section = next(s for s in sections if s.label == "Meetings Held")
    assert any("Leadership Sync" in item for item in meetings_section.items)


def test_weekly_recap_summary_counts_threads_and_meetings() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    summary = agent._weekly_recap_summary(  # type: ignore[attr-defined]
        {
            "ranked_threads": [{"importance_level": "high"}, {"importance_level": "medium"}],
            "upcoming_events": [{"title": "Board"}, {"title": "1:1"}, {"title": "All Hands"}],
        }
    )
    assert "2 notable threads" in summary
    assert "3 meetings" in summary
    assert "this week" in summary.lower() or "week" in summary.lower()


def test_weekly_recap_runner_e2e() -> None:
    runner = _make_runner()
    payload = AssistantQueryRequest(message="Recap my week.", conversation_id="conv_test")
    interaction = _interaction(40, payload.message)

    response = asyncio.run(runner.run(payload, interaction, _user()))

    assert response.workflow_type == WorkflowType.WEEKLY_RECAP
    assert response.status == "completed"



# ---------------------------------------------------------------------------
# 5. MEETING PREP
# ---------------------------------------------------------------------------


def test_meeting_prep_briefing_title_uses_meeting_name() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    planning_context: dict = {}
    title = agent._default_title(  # type: ignore[attr-defined]
        "meeting_prep",
        {
            "upcoming_events": [
                {"title": "Q1 Board Review", "starts_at": "2026-03-20T10:00:00", "attendees": ["cfo@company.com"]}
            ]
        },
        planning_context,
    )
    assert "Q1 Board Review" in title
    assert "Meeting Prep" in title


def test_meeting_prep_briefing_title_fallback_when_no_event() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    title = agent._default_title("meeting_prep", {}, {})  # type: ignore[attr-defined]
    assert "Meeting Prep" in title


def test_meeting_prep_briefing_sections() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "upcoming_events": [
            {
                "title": "Q1 Board Review",
                "starts_at": "2026-03-20T10:00:00",
                "attendees": ["board@company.com", "cfo@company.com"],
                "agenda": "Review board agenda, margin variance, and owner alignment.",
                "notes": "Need a decision on variance messaging and next-step owners.",
            }
        ],
        "attendee_threads": [
            {
                "subject": "Pre-read deck",
                "latest_sender": "cfo@company.com",
                "importance_level": "high",
                "importance_reasons": ["Board agenda item"],
            }
        ],
        "attendee_emails": ["board@company.com", "cfo@company.com"],
        "ranked_threads": [],
        "structured_watch": {
            "asks": [{"ask": "Review pre-read by Friday"}],
            "deadlines": [{"deadline": "Friday noon"}],
        },
    }

    items = agent._primary_items_for_workflow(  # type: ignore[attr-defined]
        workflow_type="meeting_prep",
        event_payload=event_payload,
        ranked_threads=[],
        structured_watch={},
        upcoming_events=event_payload["upcoming_events"],
        signals=[],
    )
    sections = agent._build_sections(  # type: ignore[attr-defined]
        workflow_type="meeting_prep",
        items=items,
        ranked_threads=[],
        structured_watch=event_payload["structured_watch"],
        event_payload=event_payload,
    )
    labels = [s.label for s in sections]

    assert "Meeting Overview" in labels
    assert "Meeting Objectives" in labels
    assert "Open Items" in labels
    assert "Suggested Talking Points" in labels
    assert "Desired Outcomes" in labels

    overview = next(s for s in sections if s.label == "Meeting Overview")
    assert any("Q1 Board Review" in item for item in overview.items)
    objectives = next(s for s in sections if s.label == "Meeting Objectives")
    assert any("Objective:" in item for item in objectives.items)
    assert any("agenda" in item.lower() for item in objectives.items)
    outcomes = next(s for s in sections if s.label == "Desired Outcomes")
    assert any("Desired outcome:" in item for item in outcomes.items)
    assert any("next step" in item.lower() or "decision" in item.lower() for item in outcomes.items)


def test_meeting_prep_summary_counts_attendee_threads() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    summary = agent._meeting_prep_summary(  # type: ignore[attr-defined]
        {
            "upcoming_events": [{"title": "Board Review"}],
            "attendee_threads": [
                {"subject": "Pre-read", "latest_sender": "cfo@company.com"},
                {"subject": "Agenda", "latest_sender": "board@company.com"},
            ],
            "ranked_threads": [],
        }
    )
    assert "2 threads" in summary
    assert "Board Review" in summary
    assert "objectives" in summary.lower()
    assert "outcomes" in summary.lower()


def test_meeting_prep_talking_points_derived_from_threads() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    points = agent._meeting_talking_points(  # type: ignore[attr-defined]
        {
            "attendee_threads": [
                {
                    "subject": "Q1 Forecast",
                    "importance_reasons": ["Revenue miss risk"],
                }
            ]
        },
        [],
    )
    assert any("Q1 Forecast" in p for p in points)


def test_meeting_prep_talking_points_fallback_when_no_threads() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    points = agent._meeting_talking_points({}, [])  # type: ignore[attr-defined]
    assert len(points) >= 1
    assert all(isinstance(p, str) for p in points)


def test_meeting_prep_runner_e2e() -> None:
    runner = _make_runner(
        calendar_event={
            "upcoming_events": [
                {
                    "title": "Q1 Board Meeting",
                    "starts_at": "2026-03-20T10:00:00",
                    "attendees": ["board@company.com", "cfo@company.com"],
                }
            ]
        }
    )
    payload = AssistantQueryRequest(message="Prep me for my board meeting.", conversation_id="conv_test")
    interaction = _interaction(50, payload.message)

    response = asyncio.run(runner.run(payload, interaction, _user()))

    assert response.workflow_type == WorkflowType.MEETING_PREP
    assert response.status == "completed"



# ---------------------------------------------------------------------------
# 6. DAY SCHEDULE PLANNING
# ---------------------------------------------------------------------------


def test_day_schedule_runner_e2e() -> None:
    runner = _make_runner()
    payload = AssistantQueryRequest(message="Plan my day.", conversation_id="conv_test")
    interaction = _interaction(60, payload.message)

    response = asyncio.run(runner.run(payload, interaction, _user()))

    assert response.workflow_type == WorkflowType.SCHEDULE_PLANNING
    assert response.status == "completed"




# ---------------------------------------------------------------------------
# 7. WEEK SCHEDULE PLANNING
# ---------------------------------------------------------------------------


def test_week_schedule_runner_e2e() -> None:
    runner = _make_runner()
    payload = AssistantQueryRequest(message="Plan my week based on my emails and calendar.", conversation_id="conv_test")
    interaction = _interaction(70, payload.message)

    response = asyncio.run(runner.run(payload, interaction, _user()))

    assert response.workflow_type == WorkflowType.SCHEDULE_PLANNING
    assert response.status == "completed"




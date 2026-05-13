from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.workflows.planning_types import RequestPlan
from src.workflows.assistant_dispatch import _runtime_routing_decision, generate_native_assistant_response
from src.workflows.routing import RouteDecision, RouteFamily
from src.agents.schemas import TaskIntent


def test_generate_native_assistant_response_uses_runtime_for_structured_workflow(monkeypatch):
    payload = SimpleNamespace(
        message="Explain the attached covenant document and what it means for us.",
        attachments=[{"document_id": "doc_1", "filename": "Covenant.pdf"}],
        conversation_id="conv_1",
    )
    interaction = SimpleNamespace(id=1)
    current_user = SimpleNamespace(ceo_id="ceo_test")

    runtime_run = AsyncMock(return_value="runtime-result")
    agent_handle = AsyncMock(return_value="agent-result")
    monkeypatch.setattr(
        "src.workflows.assistant_dispatch.plan_request",
        lambda message, has_attachments=False, reference_dt=None: RequestPlan(
            mode="direct_workflow",
            target_workflow="document_explanation",
            direct_workflow="document_explanation",
        ),
    )
    runtime = SimpleNamespace(run=runtime_run, _definition_for_type=lambda workflow_type: "definition")
    agent = SimpleNamespace(handle=agent_handle)

    result = asyncio.run(
        generate_native_assistant_response(payload, interaction, current_user, runtime=runtime, agent=agent)
    )

    assert result == "runtime-result"
    runtime_run.assert_awaited_once()
    agent_handle.assert_not_awaited()
    extra_metadata = runtime_run.call_args.kwargs["extra_metadata"]
    assert extra_metadata["request_plan"]["target_workflow"] == "document_explanation"


def test_generate_native_assistant_response_uses_runtime_for_schedule(monkeypatch):
    payload = SimpleNamespace(message="Plan my week with inbox and calendar context.", attachments=[], conversation_id="conv_1")
    interaction = SimpleNamespace(id=1)
    current_user = SimpleNamespace(ceo_id="ceo_test")

    runtime_run = AsyncMock(return_value="runtime-result")
    agent_handle = AsyncMock(return_value="agent-result")
    monkeypatch.setattr(
        "src.workflows.assistant_dispatch.plan_request",
        lambda message, has_attachments=False, reference_dt=None: RequestPlan(
            mode="direct_workflow",
            target_workflow="schedule_planning",
            direct_workflow="schedule_planning",
            time_horizon="this_week",
        ),
    )
    runtime = SimpleNamespace(run=runtime_run, _definition_for_type=lambda workflow_type: "definition")
    agent = SimpleNamespace(handle=agent_handle)

    result = asyncio.run(
        generate_native_assistant_response(payload, interaction, current_user, runtime=runtime, agent=agent)
    )

    assert result == "runtime-result"
    runtime_run.assert_awaited_once()
    agent_handle.assert_not_awaited()
    extra_metadata = runtime_run.call_args.kwargs["extra_metadata"]
    assert extra_metadata["request_plan"]["target_workflow"] == "schedule_planning"
    assert extra_metadata["event_payload"]["planning_context"]["time_horizon"] == "this_week"


def test_generate_native_assistant_response_uses_agent_for_conversational(monkeypatch):
    payload = SimpleNamespace(message="What do you know about our company right now?", attachments=[], conversation_id="conv_1")
    interaction = SimpleNamespace(id=1)
    current_user = SimpleNamespace(ceo_id="ceo_test")

    runtime_run = AsyncMock(return_value="runtime-result")
    agent_handle = AsyncMock(return_value="agent-result")
    monkeypatch.setattr(
        "src.workflows.assistant_dispatch.plan_request",
        lambda message, has_attachments=False, reference_dt=None: RequestPlan(
            mode="direct_workflow",
            target_workflow="conversational",
            direct_workflow="conversational",
        ),
    )
    runtime = SimpleNamespace(run=runtime_run, _definition_for_type=lambda workflow_type: "definition")
    agent = SimpleNamespace(handle=agent_handle)

    result = asyncio.run(
        generate_native_assistant_response(payload, interaction, current_user, runtime=runtime, agent=agent)
    )

    assert result == "agent-result"
    agent_handle.assert_awaited_once()
    runtime_run.assert_not_awaited()


def test_generate_native_assistant_response_preserves_follow_up_workflow(monkeypatch):
    payload = SimpleNamespace(
        message="Render this as a compact list of priorities and actions.",
        attachments=[],
        conversation_id="conv_1",
        follow_up_context=SimpleNamespace(source_interaction_id=42),
    )
    interaction = SimpleNamespace(id=2)
    current_user = SimpleNamespace(ceo_id="ceo_test")

    runtime_run = AsyncMock(return_value="runtime-result")
    agent_handle = AsyncMock(return_value="agent-result")
    routed_workflows: list[str] = []

    monkeypatch.setattr(
        "src.workflows.assistant_dispatch.plan_request",
        lambda message, has_attachments=False, reference_dt=None: RequestPlan(
            mode="direct_workflow",
            target_workflow="conversational",
            direct_workflow="conversational",
        ),
    )
    monkeypatch.setattr(
        "src.workflows.assistant_dispatch.get_assistant_conversation",
        lambda ceo_id, conversation_id: SimpleNamespace(interaction_ids=[42]),
    )
    monkeypatch.setattr(
        "src.workflows.assistant_dispatch.get_interactions_for_conversation",
        lambda ceo_id, interaction_ids: [
            SimpleNamespace(
                id=42,
                response='{"workflow_type":"schedule_planning","response_type":"clarification"}',
            )
        ],
    )
    monkeypatch.setattr(
        "src.workflows.assistant_dispatch.get_or_create_live_context",
        lambda ceo_id, conversation_id: SimpleNamespace(resolved_clarifications={"presentation_style": "list_form"}),
    )

    def _definition_for_type(workflow_type: str) -> str:
        routed_workflows.append(workflow_type)
        return f"definition:{workflow_type}"

    runtime = SimpleNamespace(run=runtime_run, _definition_for_type=_definition_for_type)
    agent = SimpleNamespace(handle=agent_handle)

    result = asyncio.run(
        generate_native_assistant_response(payload, interaction, current_user, runtime=runtime, agent=agent)
    )

    assert result == "runtime-result"
    assert routed_workflows == ["schedule_planning"]
    runtime_run.assert_awaited_once()
    agent_handle.assert_not_awaited()
    extra_metadata = runtime_run.call_args.kwargs["extra_metadata"]
    assert extra_metadata["resolved_clarifications"] == {"presentation_style": "list_form"}


def test_generate_native_assistant_response_uses_hidden_follow_up_context(monkeypatch):
    payload = SimpleNamespace(
        message="Review Q1 board packet and decide the next step.",
        attachments=[],
        conversation_id="conv_1",
        follow_up_context=SimpleNamespace(
            source_interaction_id=42,
            source_response_type="brief",
            source_context="Prior question: Morning brief | Prior response: Morning Brief • Wed, May 13 | Suggested follow-ups: Review Q1 board packet and decide the next step.",
        ),
    )
    interaction = SimpleNamespace(id=3)
    current_user = SimpleNamespace(ceo_id="ceo_test")

    runtime_run = AsyncMock(return_value="runtime-result")
    agent_handle = AsyncMock(return_value="agent-result")
    captured_messages: list[str] = []

    def _plan_request(message, has_attachments=False, reference_dt=None):
        captured_messages.append(message)
        return RequestPlan(
            mode="direct_workflow",
            target_workflow="schedule_planning",
            direct_workflow="schedule_planning",
        )

    monkeypatch.setattr("src.workflows.assistant_dispatch.plan_request", _plan_request)
    monkeypatch.setattr(
        "src.workflows.assistant_dispatch.get_assistant_conversation",
        lambda ceo_id, conversation_id: SimpleNamespace(interaction_ids=[42]),
    )
    monkeypatch.setattr(
        "src.workflows.assistant_dispatch.get_interactions_for_conversation",
        lambda ceo_id, interaction_ids: [
            SimpleNamespace(id=42, response='{"workflow_type":"schedule_planning","response_type":"clarification"}')
        ],
    )

    runtime = SimpleNamespace(run=runtime_run, _definition_for_type=lambda workflow_type: "definition")
    agent = SimpleNamespace(handle=agent_handle)

    result = asyncio.run(
        generate_native_assistant_response(payload, interaction, current_user, runtime=runtime, agent=agent)
    )

    assert result == "runtime-result"
    assert captured_messages and "[Internal context:" in captured_messages[0]
    extra_metadata = runtime_run.call_args.kwargs["extra_metadata"]
    assert extra_metadata["follow_up_context"]["source_context"].startswith("Prior question:")
    agent_handle.assert_not_awaited()


def test_runtime_routing_decision_uses_route_family_not_workflow_name() -> None:
    decision = _runtime_routing_decision(
        "morning_brief",
        RequestPlan(mode="direct_workflow", target_workflow="morning_brief", direct_workflow="morning_brief"),
        RouteDecision(primary_intent=RouteFamily.WATCH, subintents=[]),
    )

    assert decision.intent == TaskIntent.FACT_FINDING
    assert decision.specialist_required == "morning_brief"

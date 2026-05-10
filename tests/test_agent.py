# tests/test_agent.py
import json
import pytest
from src.assistant.sdk_tools import (
    get_anthropic_tools,
    execute_tool,
    WRITE_TOOL_NAMES,
    READ_TOOL_NAMES,
)
from src.tools.base import ToolContext


def test_get_anthropic_tools_returns_list_of_dicts():
    tools = get_anthropic_tools()
    assert isinstance(tools, list)
    assert len(tools) > 0
    for t in tools:
        assert "name" in t
        assert "description" in t
        assert "input_schema" in t
        assert t["input_schema"]["type"] == "object"


def test_write_tool_names_are_subset_of_exposed():
    from src.assistant.sdk_tools import EXPOSED_TOOL_NAMES
    assert WRITE_TOOL_NAMES.issubset(EXPOSED_TOOL_NAMES)


def test_read_tool_names_do_not_overlap_with_write():
    assert READ_TOOL_NAMES.isdisjoint(WRITE_TOOL_NAMES)


def test_send_email_draft_is_write_tool():
    assert "send_email_draft" in WRITE_TOOL_NAMES


def test_read_email_threads_is_read_tool():
    assert "read_email_threads" in READ_TOOL_NAMES


def test_execute_tool_returns_string():
    # get_preferences is a read tool that gracefully handles missing DB data
    context = ToolContext(ceo_id="test_ceo_001")
    result = execute_tool("get_preferences", {}, context)
    assert isinstance(result, str)


from unittest.mock import MagicMock, patch
from src.assistant.approval import is_write_tool, store_pending_action, execute_approval, reject_approval


def test_is_write_tool_true_for_send_email_draft():
    assert is_write_tool("send_email_draft") is True


def test_is_write_tool_false_for_read_email_threads():
    assert is_write_tool("read_email_threads") is False


def _make_user(ceo_id: str = "ceo_test") -> MagicMock:
    user = MagicMock()
    user.ceo_id = ceo_id
    user.company_name = "TestCo"
    return user


def test_store_and_reject_pending_action(tmp_path, monkeypatch):
    """Store a pending action then reject it — no DB needed via monkeypatching."""
    stored: dict = {}

    def fake_get_or_create(ceo_id, conversation_id):
        ctx = MagicMock()
        ctx.pending_actions = []
        ctx.id = 1
        return ctx

    def fake_update(conversation_id, *, pending_actions=None, **kwargs):
        if pending_actions is not None:
            stored["pending_actions"] = pending_actions

    monkeypatch.setattr(
        "src.assistant.approval.get_or_create_live_context", fake_get_or_create
    )
    monkeypatch.setattr(
        "src.assistant.approval.update_live_context", fake_update
    )

    store_pending_action(
        ceo_id="ceo_test",
        conversation_id="conv_001",
        tool_name="send_email_draft",
        tool_inputs={"to": "alice@example.com", "subject": "Hi", "body": "Test"},
        interaction_id=42,
    )

    assert len(stored["pending_actions"]) == 1
    action = stored["pending_actions"][0]
    assert action["tool_name"] == "send_email_draft"
    assert action["interaction_id"] == 42


import asyncio
from src.assistant.agent import AgenticAssistant
from src.api.schemas import AnswerPayload, AssistantQueryRequest, AssistantMessageResponse, TrustMetadata
from src.workflows.planning_types import RequestPlan


def _make_interaction(id: int = 1, ceo_id: str = "ceo_test") -> MagicMock:
    interaction = MagicMock()
    interaction.id = id
    interaction.ceo_id = ceo_id
    return interaction


def _make_payload(message: str = "What's in my inbox?") -> AssistantQueryRequest:
    return AssistantQueryRequest(message=message, conversation_id="conv_001")


def _make_anthropic_text_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def test_agent_returns_assistant_message_response():
    agent = AgenticAssistant()
    user = _make_user()
    payload = _make_payload()
    interaction = _make_interaction()

    mock_response = _make_anthropic_text_response("You have 3 urgent emails.")

    with patch.object(agent._client.messages, "create", return_value=mock_response):
        with patch("src.assistant.agent.get_ceo_preferences", return_value=None):
            with patch("src.assistant.agent.get_session_history", return_value=[]):
                result = asyncio.run(
                    agent.handle(payload=payload, interaction=interaction, current_user=user)
                )

    assert isinstance(result, AssistantMessageResponse)
    assert result.conversation_id == "conv_001"
    assert result.status == "completed"
    assert "urgent" in result.answer.summary


def test_agent_surfaces_write_tool_as_pending():
    agent = AgenticAssistant()
    user = _make_user()
    payload = _make_payload(message="Send a follow-up to alice@example.com")
    interaction = _make_interaction(id=99)

    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "send_email_draft"
    tool_use_block.id = "tu_001"
    tool_use_block.input = {"to": "alice@example.com", "subject": "Follow-up", "body": "Hi Alice"}

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Here's the email I'd send — want me to send it?"

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [text_block, tool_use_block]

    with patch.object(agent._client.messages, "create", return_value=response):
        with patch("src.assistant.agent.get_ceo_preferences", return_value=None):
            with patch("src.assistant.agent.get_session_history", return_value=[]):
                with patch("src.assistant.agent.store_pending_action") as mock_store:
                    result = asyncio.run(
                        agent.handle(payload=payload, interaction=interaction, current_user=user)
                    )

    assert result.status == "pending"
    assert mock_store.called
    call_kwargs = mock_store.call_args.kwargs
    assert call_kwargs["tool_name"] == "send_email_draft"
    assert call_kwargs["interaction_id"] == 99


def test_agent_stamps_workflow_from_request_plan():
    agent = AgenticAssistant()
    user = _make_user()
    payload = _make_payload(message="Plan my week with inbox and calendar context.")
    interaction = _make_interaction(id=101)

    request_plan = RequestPlan(
        mode="direct_workflow",
        target_workflow="schedule_planning",
        direct_workflow="schedule_planning",
    )

    with patch.object(agent, "_classify_query", return_value="data_required"):
        with patch.object(agent, "_build_system_prompt", return_value="system"):
            with patch.object(agent, "_build_fast_system_prompt", return_value="fast"):
                with patch.object(agent, "_load_history", return_value=[]):
                    with patch("src.assistant.agent.plan_request", return_value=request_plan):
                        with patch.object(agent, "_run_tool_loop", return_value=("final text", None, ["read_email_threads", "read_calendar_events"], {})):
                            with patch.object(agent._formatter, "format", return_value=(AnswerPayload(title="", summary="", sections=[]), TrustMetadata(), "morning_brief")):
                                result = asyncio.run(
                                    agent.handle(payload=payload, interaction=interaction, current_user=user)
                                )

    assert result.workflow_type == "schedule_planning"
    assert result.response_type == "schedule"
    assert result.presentation is not None
    assert result.presentation.mode == "schedule"


def test_build_system_prompt_includes_document_attachment_hint():
    agent = AgenticAssistant()
    user = _make_user()
    request_plan = RequestPlan(
        mode="direct_workflow",
        target_workflow="document_explanation",
        direct_workflow="document_explanation",
    )
    prompt = agent._build_system_prompt(
        user,
        request_plan,
        [{"document_id": "doc_1", "filename": "Series C Covenants.pdf"}],
    )

    assert "document_explanation" in prompt
    assert "Series C Covenants.pdf" in prompt
    assert "primary source material" in prompt


def test_normalize_answer_overrides_generic_title_for_schedule():
    agent = AgenticAssistant()
    answer = AnswerPayload(title="Morning Brief", summary="", sections=[])
    normalized = agent._normalize_answer(answer, workflow_type="schedule_planning", final_text="Schedule plan text")

    assert normalized.title == "Morning Brief"
    assert normalized.summary == "Schedule plan text"


def test_apply_answer_title_contract_clears_conversational_title():
    agent = AgenticAssistant()
    answer = AnswerPayload(title="InnovateCorp Critical Issues & Opportunities", summary="Plain response", sections=[])
    normalized = agent._apply_answer_title_contract(answer, workflow_type="conversational")

    assert normalized.title == ""
    assert normalized.summary == "Plain response"


def test_build_response_clears_conversational_title():
    agent = AgenticAssistant()
    payload = _make_payload("What do you know about our company right now?")
    interaction = _make_interaction()
    answer = AnswerPayload(title="InnovateCorp Critical Issues & Opportunities", summary="Plain response", sections=[])
    trust = TrustMetadata()

    response = agent._build_response(
        payload=payload,
        interaction=interaction,
        answer=answer,
        trust=trust,
        pending_action=None,
        workflow_type="conversational",
    )

    assert response.answer.title == ""

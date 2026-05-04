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
from src.api.schemas import AssistantQueryRequest, AssistantMessageResponse


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

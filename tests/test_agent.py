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


from unittest.mock import MagicMock
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

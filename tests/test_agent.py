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

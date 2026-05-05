"""Structural tests for ResponseFormatter.

Tests three things:
  1. _detect_response_type — deterministic mapping from tool list to response type
  2. ResponseFormatter.format() — correct AnswerPayload shape given mocked Haiku output
  3. Skip / fallback logic — short text, low-value types, bad JSON from Haiku

No real Anthropic API calls are made. The Haiku client is patched in every test
that exercises the extraction path.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.api.schemas import AnswerPayload, AnswerSection
from src.assistant.formatter import (
    ResponseFormatter,
    _detect_response_type,
    _SKIP_FORMAT_TYPES,
    _MIN_FORMAT_LENGTH,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _haiku_response(payload: dict) -> MagicMock:
    """Build a mock Anthropic messages.create response containing JSON."""
    msg = MagicMock()
    msg.content = [SimpleNamespace(text=json.dumps(payload))]
    return msg


def _make_formatter(haiku_return=None) -> tuple[ResponseFormatter, MagicMock]:
    """Return a formatter whose Anthropic client is mocked."""
    fmt = ResponseFormatter.__new__(ResponseFormatter)
    mock_client = MagicMock()
    fmt._client = mock_client
    if haiku_return is not None:
        mock_client.messages.create.return_value = haiku_return
    return fmt, mock_client


LONG_TEXT = "A" * (_MIN_FORMAT_LENGTH + 10)


# ---------------------------------------------------------------------------
# 1. _detect_response_type
# ---------------------------------------------------------------------------

class TestDetectResponseType:
    def test_inbox_summary(self):
        assert _detect_response_type(["read_email_threads"]) == "inbox_summary"

    def test_calendar_summary(self):
        assert _detect_response_type(["read_calendar_events"]) == "calendar_summary"

    def test_morning_brief_all_three(self):
        tools = ["read_email_threads", "read_calendar_events", "get_recent_signals"]
        assert _detect_response_type(tools) == "morning_brief"

    def test_morning_brief_two_of_three(self):
        # Any 2 of the 3 morning signals → morning_brief
        assert _detect_response_type(["read_email_threads", "read_calendar_events"]) == "morning_brief"
        assert _detect_response_type(["read_email_threads", "get_recent_signals"]) == "morning_brief"
        assert _detect_response_type(["read_calendar_events", "get_recent_signals"]) == "morning_brief"

    def test_morning_brief_beats_inbox(self):
        # Morning brief rule takes priority when enough signals present
        tools = ["read_email_threads", "read_calendar_events"]
        assert _detect_response_type(tools) == "morning_brief"

    def test_pipeline_summary(self):
        assert _detect_response_type(["crm_deal_context"]) == "pipeline_summary"

    def test_entity_lookup(self):
        assert _detect_response_type(["get_entity_context"]) == "entity_lookup"

    def test_memory_response(self):
        assert _detect_response_type(["memory_management"]) == "memory_response"

    def test_document_created(self):
        for tool in ["create_docx_memo", "create_pptx_deck", "create_workbook", "create_canvas"]:
            assert _detect_response_type([tool]) == "document_created"

    def test_slack_response(self):
        assert _detect_response_type(["slack_read"]) == "slack_response"
        assert _detect_response_type(["slack_post"]) == "slack_response"

    def test_drive_response(self):
        assert _detect_response_type(["google_drive_search"]) == "drive_response"
        assert _detect_response_type(["google_drive_read"]) == "drive_response"

    def test_no_tools_is_conversational(self):
        assert _detect_response_type([]) == "conversational"

    def test_unknown_tool_is_conversational(self):
        assert _detect_response_type(["semantic_search"]) == "conversational"
        assert _detect_response_type(["get_company_state"]) == "conversational"

    def test_mixed_unknown_and_known(self):
        # Known tool present → correct type
        assert _detect_response_type(["semantic_search", "crm_deal_context"]) == "pipeline_summary"


# ---------------------------------------------------------------------------
# 2. Format returns correct AnswerPayload shape
# ---------------------------------------------------------------------------

class TestFormatShape:
    def test_returns_answer_payload(self):
        fmt, _ = _make_formatter(_haiku_response({
            "title": "Test Title",
            "sections": [],
            "action_items": [],
            "one_liner": "Short summary.",
        }))
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert isinstance(result, AnswerPayload)

    def test_title_populated(self):
        fmt, _ = _make_formatter(_haiku_response({
            "title": "3 emails need attention",
            "sections": [],
            "action_items": [],
            "one_liner": "Busy inbox.",
        }))
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert result.title == "3 emails need attention"

    def test_summary_uses_one_liner(self):
        fmt, _ = _make_formatter(_haiku_response({
            "title": "T",
            "sections": [],
            "action_items": [],
            "one_liner": "One liner text.",
        }))
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert result.summary == "One liner text."

    def test_sections_mapped_correctly(self):
        fmt, _ = _make_formatter(_haiku_response({
            "title": "T",
            "sections": [
                {"label": "Urgent", "content": "Two emails flagged.", "items": ["Reply to CEO", "Review contract"]},
                {"label": "FYI", "content": "Newsletter arrived.", "items": []},
            ],
            "action_items": [],
            "one_liner": "S",
        }))
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert len(result.sections) == 2
        assert result.sections[0].label == "Urgent"
        assert result.sections[0].content == "Two emails flagged."
        assert result.sections[0].items == ["Reply to CEO", "Review contract"]
        assert result.sections[1].label == "FYI"

    def test_action_items_appended_as_section(self):
        fmt, _ = _make_formatter(_haiku_response({
            "title": "T",
            "sections": [{"label": "Summary", "content": "All good.", "items": []}],
            "action_items": ["Send the brief", "Block time for prep"],
            "one_liner": "S",
        }))
        result = fmt.format(LONG_TEXT, ["read_calendar_events"])
        labels = [s.label for s in result.sections]
        assert "Action Items" in labels
        action_section = next(s for s in result.sections if s.label == "Action Items")
        assert "Send the brief" in action_section.items
        assert "Block time for prep" in action_section.items

    def test_sections_always_list(self):
        fmt, _ = _make_formatter(_haiku_response({
            "title": "T",
            "sections": [],
            "action_items": [],
            "one_liner": "S",
        }))
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert isinstance(result.sections, list)

    def test_sections_with_missing_label_filtered(self):
        fmt, _ = _make_formatter(_haiku_response({
            "title": "T",
            "sections": [
                {"label": "Keep", "content": "Good.", "items": []},
                {"content": "No label here.", "items": []},   # no label — should be dropped
                {"label": "", "content": "Empty label.", "items": []},  # empty label — dropped
            ],
            "action_items": [],
            "one_liner": "S",
        }))
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert len(result.sections) == 1
        assert result.sections[0].label == "Keep"

    def test_haiku_called_once_per_format(self):
        fmt, mock_client = _make_formatter(_haiku_response({
            "title": "T", "sections": [], "action_items": [], "one_liner": "S",
        }))
        fmt.format(LONG_TEXT, ["read_email_threads"])
        assert mock_client.messages.create.call_count == 1


# ---------------------------------------------------------------------------
# 3. Skip logic — no Haiku call expected
# ---------------------------------------------------------------------------

class TestSkipLogic:
    def test_short_text_skips_haiku(self):
        fmt, mock_client = _make_formatter()
        short_text = "A" * (_MIN_FORMAT_LENGTH - 1)
        result = fmt.format(short_text, ["read_email_threads"])
        mock_client.messages.create.assert_not_called()
        assert result.summary == short_text

    def test_memory_response_skips_haiku(self):
        fmt, mock_client = _make_formatter()
        result = fmt.format(LONG_TEXT, ["memory_management"])
        mock_client.messages.create.assert_not_called()

    def test_document_created_skips_haiku(self):
        fmt, mock_client = _make_formatter()
        result = fmt.format(LONG_TEXT, ["create_docx_memo"])
        mock_client.messages.create.assert_not_called()

    def test_skip_types_coverage(self):
        # All declared skip types must not invoke Haiku
        for skip_type in _SKIP_FORMAT_TYPES:
            fmt, mock_client = _make_formatter()
            # Find a tool that maps to this type
            type_to_tool = {
                "memory_response": "memory_management",
                "document_created": "create_docx_memo",
                "slack_response": "slack_read",
                "drive_response": "google_drive_search",
            }
            tool = type_to_tool.get(skip_type)
            if tool:
                fmt.format(LONG_TEXT, [tool])
                mock_client.messages.create.assert_not_called()

    def test_skip_returns_raw_text_in_summary(self):
        fmt, _ = _make_formatter()
        result = fmt.format(LONG_TEXT, ["memory_management"])
        assert result.summary == LONG_TEXT

    def test_skip_sections_is_empty_list(self):
        fmt, _ = _make_formatter()
        result = fmt.format(LONG_TEXT, ["memory_management"])
        assert result.sections == []


# ---------------------------------------------------------------------------
# 4. Fallback — bad/missing Haiku response
# ---------------------------------------------------------------------------

class TestFallback:
    def test_invalid_json_falls_back_to_raw(self):
        fmt, mock_client = _make_formatter()
        bad_response = MagicMock()
        bad_response.content = [SimpleNamespace(text="this is not json {{{{")]
        mock_client.messages.create.return_value = bad_response
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert isinstance(result, AnswerPayload)
        assert result.summary == LONG_TEXT

    def test_haiku_exception_falls_back_to_raw(self):
        fmt, mock_client = _make_formatter()
        mock_client.messages.create.side_effect = Exception("API timeout")
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert isinstance(result, AnswerPayload)
        assert result.summary == LONG_TEXT

    def test_fallback_sections_is_empty_list(self):
        fmt, mock_client = _make_formatter()
        mock_client.messages.create.side_effect = Exception("boom")
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert result.sections == []

    def test_markdown_fenced_json_is_parsed(self):
        """Haiku sometimes wraps output in ```json fences — must be stripped."""
        fmt, mock_client = _make_formatter()
        fenced = "```json\n" + json.dumps({
            "title": "Fenced Title",
            "sections": [],
            "action_items": [],
            "one_liner": "Fenced summary.",
        }) + "\n```"
        mock_client.messages.create.return_value = MagicMock(
            content=[SimpleNamespace(text=fenced)]
        )
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert result.title == "Fenced Title"
        assert result.summary == "Fenced summary."

    def test_empty_sections_and_action_items_in_response(self):
        """Haiku returning null/missing keys must not crash."""
        fmt, _ = _make_formatter(_haiku_response({
            "title": "Title Only",
            # sections and action_items deliberately absent
        }))
        result = fmt.format(LONG_TEXT, ["read_email_threads"])
        assert isinstance(result, AnswerPayload)
        assert result.sections == []

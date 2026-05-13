from __future__ import annotations

from types import SimpleNamespace

from src.tools.base import ToolContext
from src.tools.slack_tools import SlackPostTool
from src.tools.thread_tools import WriteThreadEntryTool


def test_slack_post_tool_records_world_event(monkeypatch) -> None:
    tool = SlackPostTool()
    context = ToolContext(ceo_id="ceo_test", interaction_id=42)
    captured: list[tuple[tuple, dict]] = []

    monkeypatch.setattr(
        "src.tools.slack_tools.post_slack_message",
        lambda channel_id, text, thread_ts=None: {"ts": "123.45"},
    )
    monkeypatch.setattr(
        "src.workflows.world_simulation.record_world_event",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    result = tool.invoke(
        context,
        approved=True,
        channel_id="C123",
        text="Approve the memo",
        thread_ts="111.222",
    )

    assert result.success is True
    assert captured
    args, kwargs = captured[0]
    assert args[0] == "ceo_test"
    assert kwargs["domain"] == "signals"
    assert kwargs["event_type"] == "slack_message_posted"
    assert kwargs["payload"]["channel_id"] == "C123"
    assert kwargs["payload"]["thread_ts"] == "111.222"
    assert kwargs["payload"]["ts"] == "123.45"


def test_write_thread_entry_records_world_event(monkeypatch) -> None:
    tool = WriteThreadEntryTool()
    context = ToolContext(ceo_id="ceo_test", conversation_id="conv-1", interaction_id=7)
    captured: list[tuple[tuple, dict]] = []

    monkeypatch.setattr(
        "src.tools.thread_tools.append_thread_entry",
        lambda entry: SimpleNamespace(id=77, timestamp="2026-05-13T09:00:00Z"),
    )
    monkeypatch.setattr("src.tools.thread_tools.update_live_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.workflows.world_simulation.record_world_event",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    result = tool.invoke(
        context,
        entry_type="decision",
        content="Approve Q2 hiring plan",
        entities=["Q2 hiring plan"],
        structured_payload={"decision": "Approve Q2 hiring plan"},
        turn=3,
        actor="assistant",
    )

    assert result.success is True
    assert captured
    args, kwargs = captured[0]
    assert args[0] == "ceo_test"
    assert kwargs["domain"] == "memory"
    assert kwargs["event_type"] == "thread_entry_written"
    assert kwargs["payload"]["conversation_id"] == "conv-1"
    assert kwargs["payload"]["entry_id"] == 77
    assert kwargs["payload"]["entry_type"] == "decision"

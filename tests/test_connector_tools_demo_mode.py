from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.tools.connector_tools import ReadCalendarEventsTool, ReadEmailThreadsTool
from src.tools.base import ToolContext


def _make_demo_account(*, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(provider_metadata={"event_payload": payload})


def test_read_email_threads_uses_demo_seed_first(monkeypatch):
    tool = ReadEmailThreadsTool()
    context = ToolContext(ceo_id="ceo_test")

    monkeypatch.setattr("src.tools.connector_tools.DEV_DEMO_MODE", True)
    monkeypatch.setattr(
        "src.tools.connector_tools.load_fixture",
        MagicMock(return_value={"ranked_threads": [{"id": "t1"}, {"id": "t2"}]}),
    )
    get_account = MagicMock(side_effect=AssertionError("live account lookup should not run in demo mode"))
    monkeypatch.setattr("src.tools.connector_tools.get_connected_account", get_account)
    monkeypatch.setattr(
        "src.tools.connector_tools._get_valid_account",
        MagicMock(side_effect=AssertionError("live account lookup should not run in demo mode")),
    )
    monkeypatch.setattr(
        "src.tools.connector_tools._fetch_gmail_threads",
        MagicMock(side_effect=AssertionError("live gmail fetch should not run in demo mode")),
    )
    monkeypatch.setattr(
        "src.tools.connector_tools._fetch_outlook_threads",
        MagicMock(side_effect=AssertionError("live outlook fetch should not run in demo mode")),
    )

    result = tool.invoke(context, limit=1)

    assert result.success is True
    assert result.data["service"] == "demo_gmail"
    assert result.data["count"] == 1
    assert result.data["threads"] == [{"id": "t1"}]
    get_account.assert_not_called()


def test_read_calendar_events_uses_demo_seed_first(monkeypatch):
    tool = ReadCalendarEventsTool()
    context = ToolContext(ceo_id="ceo_test")

    monkeypatch.setattr("src.tools.connector_tools.DEV_DEMO_MODE", True)
    monkeypatch.setattr(
        "src.tools.connector_tools.load_fixture",
        MagicMock(return_value={"upcoming_events": [{"id": "e1"}, {"id": "e2"}]}),
    )
    get_account = MagicMock(side_effect=AssertionError("live account lookup should not run in demo mode"))
    monkeypatch.setattr("src.tools.connector_tools.get_connected_account", get_account)
    monkeypatch.setattr(
        "src.tools.connector_tools._get_valid_account",
        MagicMock(side_effect=AssertionError("live account lookup should not run in demo mode")),
    )
    monkeypatch.setattr(
        "src.tools.connector_tools._fetch_google_calendar_events",
        MagicMock(side_effect=AssertionError("live google calendar fetch should not run in demo mode")),
    )
    monkeypatch.setattr(
        "src.tools.connector_tools._fetch_outlook_calendar_events",
        MagicMock(side_effect=AssertionError("live outlook calendar fetch should not run in demo mode")),
    )

    result = tool.invoke(context, max_results=1)

    assert result.success is True
    assert result.data["service"] == "demo_calendar"
    assert result.data["count"] == 1
    assert result.data["events"] == [{"id": "e1"}]
    get_account.assert_not_called()

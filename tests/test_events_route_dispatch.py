from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.api.routes.events import morning_briefing
from src.api.schemas import MorningBriefRequest


def test_morning_briefing_delegates_to_event_runner(monkeypatch) -> None:
    payload = MorningBriefRequest(
        scheduled_for="2026-03-29T09:00:00-07:00",
        timezone="America/Los_Angeles",
    )
    current_user = SimpleNamespace(ceo_id="ceo_test")
    runner = AsyncMock(return_value="runner-result")
    monkeypatch.setattr("src.api.routes.events.EventWorkflowRunner.run_morning_brief", runner)

    result = asyncio.run(morning_briefing(payload, current_user))

    assert result == "runner-result"
    runner.assert_awaited_once_with(payload, current_user)

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from src.api.schemas import MorningBriefRequest
from src.core.database import get_world_reference_datetime
from src.workflows.event_runner import EventWorkflowRunner


def test_world_reference_datetime_uses_current_datetime() -> None:
    reference_dt = get_world_reference_datetime("ceo_001")

    assert reference_dt is not None
    assert reference_dt.date() == datetime.now(reference_dt.tzinfo).date()


def test_morning_brief_message_uses_world_anchor(monkeypatch) -> None:
    runner = EventWorkflowRunner()
    request = MorningBriefRequest(
        scheduled_for="2026-03-29T09:00:00-07:00",
        timezone="America/Los_Angeles",
    )
    monkeypatch.setattr(
        "src.workflows.event_runner.get_world_reference_datetime",
        lambda ceo_id, tzinfo_value=None: datetime.fromisoformat("2026-03-28T09:00:00-07:00"),
    )

    message, plan = runner._morning_brief_message(request, ceo_id="ceo_001")

    assert "tomorrow" in message.lower()
    assert plan.target_workflow == "morning_brief"
    assert plan.direct_workflow == "morning_brief"
    assert plan.time_horizon == "tomorrow"

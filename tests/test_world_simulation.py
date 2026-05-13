from __future__ import annotations

from datetime import datetime, timedelta

from src.core.database import get_world_state, init_db
from src.workflows.world_simulation import (
    WorldSnapshot,
    advance_world_day,
    build_seed_world_snapshot,
    record_world_event,
    save_world_snapshot,
)


def test_build_seed_world_snapshot_loads_current_demo_world() -> None:
    today = datetime.now().astimezone().date()

    snapshot = build_seed_world_snapshot("ceo_test", simulation_day=today)

    assert isinstance(snapshot, WorldSnapshot)
    assert snapshot.ceo_id == "ceo_test"
    assert snapshot.world_version == "world_sim_v1"
    assert snapshot.simulation_day == today
    assert snapshot.calendar_events
    assert snapshot.email_threads
    assert snapshot.crm.get("deals")
    assert snapshot.finance.get("current_metrics")


def test_advance_world_day_labels_time_sensitive_state_and_logs_mutations() -> None:
    today = datetime.now().astimezone().date()

    snapshot = advance_world_day(None, ceo_id="ceo_test", current_date=today)

    assert snapshot.derived_state["summary"]["simulation_day"] == today.isoformat()
    assert snapshot.calendar_events
    assert snapshot.calendar_events[0]["status"] in {"past", "today", "upcoming"}
    assert snapshot.crm.get("deals")
    assert snapshot.crm["deals"][0]["status"] in {"closed", "past_due", "due_today", "open", "unknown"}
    assert snapshot.email_threads
    assert snapshot.email_threads[0]["status"] in {"replied", "stale", "read", "unread"}
    assert snapshot.mutation_log
    assert snapshot.derived_state["top_calendar_item"] is not None
    assert snapshot.derived_state["top_email_thread"] is not None


def test_advance_world_day_persists_day_n_into_day_n_plus_one() -> None:
    init_db()
    ceo_id = "world_state_test"
    today = datetime.now().astimezone().date()
    next_day = today + timedelta(days=1)

    day_one = build_seed_world_snapshot(ceo_id, simulation_day=today)
    day_one.crm["persisted_marker"] = "day_one"
    save_world_snapshot(day_one)

    day_two = advance_world_day(None, ceo_id=ceo_id, current_date=next_day)
    stored = get_world_state(ceo_id)

    assert day_two.simulation_day == next_day
    assert day_two.crm["persisted_marker"] == "day_one"
    assert stored is not None
    assert stored.simulation_day == next_day.isoformat()
    assert stored.snapshot_data["crm"]["persisted_marker"] == "day_one"


def test_recorded_world_events_apply_once_on_next_tick() -> None:
    init_db()
    ceo_id = "world_event_test"
    today = datetime.now().astimezone().date()
    next_day = today + timedelta(days=1)
    later_day = next_day + timedelta(days=1)

    baseline = build_seed_world_snapshot(ceo_id, simulation_day=today)
    baseline_outbound_threads = [
        thread for thread in baseline.email_threads if thread.get("direction") == "outbound"
    ]

    record_world_event(
        ceo_id,
        domain="email",
        event_type="assistant_action_executed",
        description="CEO approved an outbound email action.",
        source_ids=["99"],
        payload={
            "tool_name": "send_email_draft",
            "tool_inputs": {
                "to": "ops@example.com",
                "subject": "Check-in",
                "body": "Hello",
                "cc": [],
            },
            "result": {"draft_id": "draft_99"},
            "interaction_id": 99,
        },
    )

    advanced_once = advance_world_day(None, ceo_id=ceo_id, current_date=next_day)
    advanced_twice = advance_world_day(None, ceo_id=ceo_id, current_date=later_day)

    once_outbound_threads = [
        thread for thread in advanced_once.email_threads if thread.get("direction") == "outbound"
    ]
    twice_outbound_threads = [
        thread for thread in advanced_twice.email_threads if thread.get("direction") == "outbound"
    ]

    assert len(once_outbound_threads) == len(baseline_outbound_threads) + 1
    assert len(twice_outbound_threads) == len(once_outbound_threads)
    assert any(thread.get("subject") == "Check-in" for thread in once_outbound_threads)
    assert advanced_once.derived_state.get("applied_world_events")
    assert advanced_once.derived_state["applied_world_events"][0]["payload"]["tool_name"] == "send_email_draft"

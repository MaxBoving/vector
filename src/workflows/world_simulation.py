"""Deterministic world simulation contracts for the seeded CEO world.

The initial seed gives us a current snapshot. The simulation layer advances that
snapshot day by day without relying on scenario-specific scripts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.core.database import get_world_state, save_world_state
from src.core.models import WorldState
from src.tools.demo_config import load_fixture

WorldDomain = Literal["email", "calendar", "crm", "finance", "memory", "signals"]


class WorldEvent(BaseModel):
    domain: WorldDomain
    event_type: str
    effective_date: date
    description: str
    source_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class WorldSnapshot(BaseModel):
    world_version: str = "world_sim_v1"
    ceo_id: str
    simulation_day: date
    last_tick_at: datetime | None = None
    mutation_log: list[WorldEvent] = Field(default_factory=list)
    email_threads: list[dict[str, Any]] = Field(default_factory=list)
    calendar_events: list[dict[str, Any]] = Field(default_factory=list)
    crm: dict[str, Any] = Field(default_factory=dict)
    finance: dict[str, Any] = Field(default_factory=dict)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    derived_state: dict[str, Any] = Field(default_factory=dict)


def record_world_event(
    ceo_id: str,
    *,
    domain: WorldDomain,
    event_type: str,
    description: str,
    effective_date: date | None = None,
    source_ids: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> WorldSnapshot:
    """Append a structured event to the persistent world log."""
    snapshot = load_world_snapshot(ceo_id)
    if snapshot is None:
        snapshot = _build_raw_seed_snapshot(ceo_id)
    event = WorldEvent(
        domain=domain,
        event_type=event_type,
        effective_date=effective_date or snapshot.simulation_day,
        description=description,
        source_ids=[str(item).strip() for item in (source_ids or []) if str(item).strip()],
        payload=dict(payload or {}),
    )
    snapshot.mutation_log = list(snapshot.mutation_log) + [event]
    snapshot.derived_state = dict(snapshot.derived_state or {})
    snapshot.derived_state["pending_event_count"] = max(
        len(snapshot.mutation_log) - int(snapshot.derived_state.get("applied_event_count") or 0),
        0,
    )
    save_world_snapshot(snapshot)
    return snapshot


def build_seed_world_snapshot(ceo_id: str, *, simulation_day: date | None = None) -> WorldSnapshot:
    """Load the current demo fixtures into a typed simulation snapshot."""
    today = simulation_day or datetime.now().astimezone().date()
    gmail = load_fixture("gmail_threads")
    gcal = load_fixture("gcal_events")
    crm = load_fixture("crm_data")
    finance = load_fixture("financials")

    snapshot = WorldSnapshot(
        ceo_id=ceo_id,
        simulation_day=today,
        last_tick_at=datetime.now(timezone.utc),
        email_threads=[dict(thread) for thread in _as_list(gmail.get("ranked_threads"))],
        calendar_events=[dict(event) for event in _as_list(gcal.get("upcoming_events"))],
        crm={key: deepcopy(value) for key, value in crm.items()},
        finance={key: deepcopy(value) for key, value in finance.items()},
        signals=[],
    )
    return advance_world_day(snapshot, current_date=today)


def advance_world_day(
    snapshot: WorldSnapshot | None = None,
    *,
    ceo_id: str = "ceo_001",
    current_date: date | None = None,
) -> WorldSnapshot:
    """Advance the world by one simulation day.

    The current scaffold normalizes time-sensitive fields and records a few
    deterministic rollover events so the world visibly changes from day to day.
    """
    if snapshot is None:
        snapshot = load_world_snapshot(ceo_id)
    if snapshot is None:
        snapshot = _build_raw_seed_snapshot(ceo_id, simulation_day=current_date)

    target_date = current_date or snapshot.simulation_day
    if target_date < snapshot.simulation_day:
        target_date = snapshot.simulation_day
    next_snapshot = snapshot.model_copy(deep=True)
    next_snapshot.simulation_day = target_date
    next_snapshot.last_tick_at = datetime.now(timezone.utc)
    next_snapshot.mutation_log = list(next_snapshot.mutation_log)
    next_snapshot, pending_events = _apply_pending_events(next_snapshot, target_date)

    calendar_events, calendar_events_log = _advance_calendar(next_snapshot.calendar_events, target_date)
    crm_state, crm_log = _advance_crm(next_snapshot.crm, target_date)
    email_threads, email_log = _advance_email(next_snapshot.email_threads, target_date)

    next_snapshot.calendar_events = calendar_events
    next_snapshot.crm = crm_state
    next_snapshot.email_threads = email_threads
    next_snapshot.mutation_log.extend(calendar_events_log)
    next_snapshot.mutation_log.extend(crm_log)
    next_snapshot.mutation_log.extend(email_log)
    derived_state = _derive_day_view(next_snapshot, target_date)
    derived_state["applied_event_count"] = len(next_snapshot.mutation_log)
    derived_state["pending_event_count"] = 0
    if pending_events:
        derived_state["applied_world_events"] = [event.model_dump(mode="json") for event in pending_events]
    next_snapshot.derived_state = derived_state
    save_world_snapshot(next_snapshot)
    return next_snapshot


def load_world_snapshot(ceo_id: str) -> WorldSnapshot | None:
    world_state = get_world_state(ceo_id)
    if not world_state:
        return None
    payload = dict(world_state.snapshot_data or {})
    if not payload:
        payload = _world_state_to_payload(world_state)
    try:
        return WorldSnapshot.model_validate(payload)
    except Exception:
        payload = _world_state_to_payload(world_state)
        return WorldSnapshot.model_validate(payload)


def save_world_snapshot(snapshot: WorldSnapshot) -> WorldState:
    world_state = _world_state_from_snapshot(snapshot)
    return save_world_state(world_state)


def _build_raw_seed_snapshot(ceo_id: str, *, simulation_day: date | None = None) -> WorldSnapshot:
    today = simulation_day or datetime.now().astimezone().date()
    gmail = load_fixture("gmail_threads")
    gcal = load_fixture("gcal_events")
    crm = load_fixture("crm_data")
    finance = load_fixture("financials")
    return WorldSnapshot(
        ceo_id=ceo_id,
        simulation_day=today,
        last_tick_at=datetime.now(timezone.utc),
        email_threads=[dict(thread) for thread in _as_list(gmail.get("ranked_threads"))],
        calendar_events=[dict(event) for event in _as_list(gcal.get("upcoming_events"))],
        crm={key: deepcopy(value) for key, value in crm.items()},
        finance={key: deepcopy(value) for key, value in finance.items()},
        signals=[],
    )


def _world_state_from_snapshot(snapshot: WorldSnapshot) -> WorldState:
    payload = snapshot.model_dump(mode="json")
    mutation_log = payload.get("mutation_log") or []
    derived_state = payload.get("derived_state") or {}
    last_tick_at = payload.get("last_tick_at") or datetime.now(timezone.utc).isoformat()
    return WorldState(
        ceo_id=snapshot.ceo_id,
        world_version=snapshot.world_version,
        simulation_day=snapshot.simulation_day.isoformat(),
        last_tick_at=last_tick_at,
        snapshot_data=payload,
        mutation_log=[dict(item) if isinstance(item, dict) else item.model_dump(mode="json") for item in mutation_log],
        derived_state=derived_state,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def _world_state_to_payload(world_state: WorldState) -> dict[str, Any]:
    payload = dict(world_state.snapshot_data or {})
    if payload:
        payload.setdefault("world_version", world_state.world_version)
        payload.setdefault("ceo_id", world_state.ceo_id)
        payload.setdefault("simulation_day", world_state.simulation_day)
        payload.setdefault("last_tick_at", world_state.last_tick_at)
        payload.setdefault("mutation_log", world_state.mutation_log or [])
        payload.setdefault("derived_state", world_state.derived_state or {})
        return payload
    return {
        "world_version": world_state.world_version,
        "ceo_id": world_state.ceo_id,
        "simulation_day": world_state.simulation_day,
        "last_tick_at": world_state.last_tick_at,
        "mutation_log": world_state.mutation_log or [],
        "email_threads": [],
        "calendar_events": [],
        "crm": {},
        "finance": {},
        "signals": [],
        "derived_state": world_state.derived_state or {},
    }


def _apply_pending_events(snapshot: WorldSnapshot, target_date: date) -> tuple[WorldSnapshot, list[WorldEvent]]:
    derived_state = dict(snapshot.derived_state or {})
    applied_count = int(derived_state.get("applied_event_count") or 0)
    pending_items = list(snapshot.mutation_log[applied_count:])
    if not pending_items:
        return snapshot, []

    pending_events: list[WorldEvent] = []
    for raw_event in pending_items:
        event = _coerce_world_event(raw_event)
        if event is None:
            continue
        pending_events.append(event)
        snapshot = _apply_world_event(snapshot, event, target_date)

    derived_state["applied_event_count"] = len(snapshot.mutation_log)
    derived_state["pending_event_count"] = 0
    snapshot.derived_state = derived_state
    return snapshot, pending_events


def _apply_world_event(snapshot: WorldSnapshot, event: WorldEvent, target_date: date) -> WorldSnapshot:
    payload = dict(event.payload or {})
    if event.domain == "email" and event.event_type == "assistant_action_executed":
        tool_name = str(payload.get("tool_name") or "").strip()
        if tool_name == "send_email_draft":
            snapshot.email_threads = _apply_sent_email_action(snapshot.email_threads, event, target_date)
    elif event.domain == "calendar" and event.event_type == "assistant_action_executed":
        tool_name = str(payload.get("tool_name") or "").strip()
        if tool_name == "create_calendar_event":
            snapshot.calendar_events = _apply_created_calendar_event(snapshot.calendar_events, event, target_date)
    elif event.domain == "signals" and event.event_type == "slack_message_posted":
        snapshot.signals = _append_signal_record(snapshot.signals, event, signal_type="slack_post")
    elif event.domain == "memory" and event.event_type == "clarification_resolved":
        snapshot.signals = _append_signal_record(snapshot.signals, event, signal_type="clarification_resolved")
    elif event.domain == "memory" and event.event_type == "thread_entry_written":
        snapshot.signals = _append_signal_record(snapshot.signals, event, signal_type="thread_entry_written")
    elif event.domain == "artifact" and event.event_type == "artifact_written":
        snapshot.signals = _append_signal_record(snapshot.signals, event, signal_type="artifact_written")
    return snapshot


def _append_signal_record(signals: list[dict[str, Any]], event: WorldEvent, *, signal_type: str) -> list[dict[str, Any]]:
    updated = [dict(item) for item in signals]
    updated.append(
        {
            "signal_type": signal_type,
            "domain": event.domain,
            "event_type": event.event_type,
            "description": event.description,
            "effective_date": event.effective_date.isoformat(),
            "source_ids": list(event.source_ids),
            "payload": dict(event.payload or {}),
        }
    )
    return updated


def _apply_sent_email_action(
    threads: list[dict[str, Any]],
    event: WorldEvent,
    target_date: date,
) -> list[dict[str, Any]]:
    payload = dict(event.payload or {})
    tool_inputs = dict(payload.get("tool_inputs") or {})
    updated = [dict(thread) for thread in threads]
    updated.append(
        {
            "thread_id": payload.get("draft_id") or payload.get("message_id") or tool_inputs.get("draft_id") or f"outbound:{len(updated) + 1}",
            "subject": tool_inputs.get("subject") or payload.get("subject") or "Assistant follow-up",
            "body_preview": str(tool_inputs.get("body") or payload.get("body") or "")[:220],
            "sender": "assistant",
            "recipient": tool_inputs.get("to") or payload.get("to"),
            "cc": tool_inputs.get("cc") or payload.get("cc") or [],
            "direction": "outbound",
            "has_replied": True,
            "is_read": True,
            "status": "replied",
            "received_at": datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc).isoformat(),
            "simulation_day": target_date.isoformat(),
            "source_event": event.event_type,
        }
    )
    return updated


def _apply_created_calendar_event(
    events: list[dict[str, Any]],
    event: WorldEvent,
    target_date: date,
) -> list[dict[str, Any]]:
    payload = dict(event.payload or {})
    tool_inputs = dict(payload.get("tool_inputs") or {})
    updated = [dict(item) for item in events]
    updated.append(
        {
            "meeting_id": payload.get("meeting_id") or tool_inputs.get("meeting_id") or f"assistant_calendar:{len(updated) + 1}",
            "title": tool_inputs.get("title") or payload.get("title") or "Assistant-created event",
            "start_time": tool_inputs.get("starts_at") or payload.get("starts_at"),
            "end_time": tool_inputs.get("ends_at") or payload.get("ends_at"),
            "attendees": list(tool_inputs.get("attendees") or payload.get("attendees") or []),
            "description": tool_inputs.get("description") or payload.get("description"),
            "status": "upcoming",
            "simulation_day": target_date.isoformat(),
            "source_event": event.event_type,
        }
    )
    return updated


def _coerce_world_event(raw_event: Any) -> WorldEvent | None:
    if isinstance(raw_event, WorldEvent):
        return raw_event
    if isinstance(raw_event, dict):
        try:
            return WorldEvent.model_validate(raw_event)
        except Exception:
            return None
    return None


def _advance_calendar(events: list[dict[str, Any]], target_date: date) -> tuple[list[dict[str, Any]], list[WorldEvent]]:
    updated: list[dict[str, Any]] = []
    mutations: list[WorldEvent] = []
    for event in events:
        current = dict(event)
        event_date = _extract_date(current.get("start_time") or current.get("starts_at") or current.get("date"))
        if event_date is None:
            updated.append(current)
            continue
        new_status = "past" if event_date < target_date else "today" if event_date == target_date else "upcoming"
        previous_status = current.get("status")
        current["status"] = new_status
        current["simulation_day"] = target_date.isoformat()
        updated.append(current)
        if previous_status != new_status:
            mutations.append(
                WorldEvent(
                    domain="calendar",
                    event_type="status_rollover",
                    effective_date=target_date,
                    description=f"Calendar event {current.get('meeting_id') or current.get('title') or 'unknown'} moved to {new_status}.",
                    source_ids=[str(current.get("meeting_id") or current.get("title") or "").strip()] if str(current.get("meeting_id") or current.get("title") or "").strip() else [],
                    payload={"previous_status": previous_status, "new_status": new_status},
                )
            )
    return updated, mutations


def _advance_crm(crm: dict[str, Any], target_date: date) -> tuple[dict[str, Any], list[WorldEvent]]:
    updated = deepcopy(crm)
    mutations: list[WorldEvent] = []
    deals = _as_list(updated.get("deals"))
    normalized_deals: list[dict[str, Any]] = []
    for deal in deals:
        current = dict(deal)
        close_date = _extract_date(current.get("close_date"))
        stage = str(current.get("stage") or "").strip().lower()
        previous_status = current.get("status")
        if close_date is None:
            new_status = "unknown"
        elif stage in {"closed won", "closed lost"}:
            new_status = "closed"
        elif close_date < target_date:
            new_status = "past_due"
        elif close_date == target_date:
            new_status = "due_today"
        else:
            new_status = "open"
        current["status"] = new_status
        normalized_deals.append(current)
        if previous_status != new_status:
            mutations.append(
                WorldEvent(
                    domain="crm",
                    event_type="status_rollover",
                    effective_date=target_date,
                    description=f"Deal {current.get('deal_id') or current.get('account_name') or 'unknown'} moved to {new_status}.",
                    source_ids=[str(current.get("deal_id") or "").strip()] if str(current.get("deal_id") or "").strip() else [],
                    payload={"previous_status": previous_status, "new_status": new_status, "stage": current.get("stage")},
                )
            )
    if "deals" in updated:
        updated["deals"] = normalized_deals
    if "closed_deals" in updated:
        updated["closed_deals"] = [dict(item) for item in _as_list(updated.get("closed_deals"))]
    if "duplicate_accounts" in updated:
        updated["duplicate_accounts"] = [dict(item) for item in _as_list(updated.get("duplicate_accounts"))]
    if "stale_contacts" in updated:
        updated["stale_contacts"] = [dict(item) for item in _as_list(updated.get("stale_contacts"))]
    return updated, mutations


def _advance_email(threads: list[dict[str, Any]], target_date: date) -> tuple[list[dict[str, Any]], list[WorldEvent]]:
    updated: list[dict[str, Any]] = []
    mutations: list[WorldEvent] = []
    for thread in threads:
        current = dict(thread)
        received_at = _extract_datetime(current.get("received_at"))
        previous_status = current.get("status")
        if received_at is None:
            updated.append(current)
            continue
        age_days = max((target_date - received_at.date()).days, 0)
        if current.get("has_replied") is True:
            new_status = "replied"
        elif current.get("is_read") is True and age_days >= 7:
            new_status = "stale"
        elif current.get("is_read") is True:
            new_status = "read"
        else:
            new_status = "unread"
        current["status"] = new_status
        current["age_days"] = age_days
        updated.append(current)
        if previous_status != new_status:
            mutations.append(
                WorldEvent(
                    domain="email",
                    event_type="status_rollover",
                    effective_date=target_date,
                    description=f"Thread {current.get('thread_id') or current.get('subject') or 'unknown'} moved to {new_status}.",
                    source_ids=[str(current.get("thread_id") or "").strip()] if str(current.get("thread_id") or "").strip() else [],
                    payload={"previous_status": previous_status, "new_status": new_status, "age_days": age_days},
                )
            )
    return updated, mutations


def _derive_day_view(snapshot: WorldSnapshot, target_date: date) -> dict[str, Any]:
    next_meeting = _choose_next_calendar_event(snapshot.calendar_events, target_date)
    top_thread = _choose_top_email_thread(snapshot.email_threads)
    top_deal = _choose_top_crm_deal(snapshot.crm)
    overdue_deals = [
        {
            "deal_id": deal.get("deal_id"),
            "account_name": deal.get("account_name"),
            "close_date": deal.get("close_date"),
            "stage": deal.get("stage"),
        }
        for deal in _as_list(snapshot.crm.get("deals"))
        if deal.get("status") == "past_due"
    ]

    return {
        "top_calendar_item": next_meeting,
        "top_email_thread": top_thread,
        "top_crm_deal": top_deal,
        "overdue_deals": overdue_deals,
        "summary": {
            "calendar_count": len(snapshot.calendar_events),
            "email_thread_count": len(snapshot.email_threads),
            "deal_count": len(_as_list(snapshot.crm.get("deals"))),
            "mutation_count": len(snapshot.mutation_log),
            "simulation_day": target_date.isoformat(),
        },
    }


def _choose_next_calendar_event(events: list[dict[str, Any]], target_date: date) -> dict[str, Any] | None:
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for event in events:
        starts_at = _extract_datetime(event.get("start_time") or event.get("starts_at"))
        if starts_at is None:
            continue
        if starts_at.date() < target_date:
            continue
        candidates.append((starts_at, event))
    if not candidates:
        return None
    _, event = min(candidates, key=lambda item: item[0])
    return dict(event)


def _choose_top_email_thread(threads: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not threads:
        return None

    def _thread_rank(thread: dict[str, Any]) -> tuple[int, int, datetime]:
        importance = thread.get("importance_score")
        score = int(float(importance) * 100) if isinstance(importance, (int, float)) else 0
        unread_bonus = 1 if not thread.get("is_read") else 0
        received_at = _extract_datetime(thread.get("received_at")) or datetime.min.replace(tzinfo=timezone.utc)
        return (score, unread_bonus, received_at)

    return dict(max(threads, key=_thread_rank))


def _choose_top_crm_deal(crm: dict[str, Any]) -> dict[str, Any] | None:
    deals = [dict(item) for item in _as_list(crm.get("deals"))]
    if not deals:
        return None

    def _deal_rank(deal: dict[str, Any]) -> tuple[int, float, date]:
        stage_rank = {
            "Negotiation": 4,
            "Proposal Sent": 3,
            "Demo Scheduled": 2,
            "Qualified": 1,
            "Discovery": 0,
            "Prospecting": -1,
            "Stalled": -2,
        }
        stage = str(deal.get("stage") or "").strip()
        amount = float(deal.get("amount") or 0.0)
        close_date = _extract_date(deal.get("close_date")) or date.min
        return (stage_rank.get(stage, -5), amount, close_date)

    return max(deals, key=_deal_rank)


def _extract_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(text)
            except ValueError:
                return None
    return None


def _extract_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []

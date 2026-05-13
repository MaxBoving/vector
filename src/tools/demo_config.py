"""Shared dev/demo configuration for connector tools."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

DEV_DEMO_CEO_ID: str = os.getenv("DEV_DEMO_CEO_ID", "ceo_001")
APP_MODE: str = os.getenv("AGENTICMIND_MODE", "dev").strip().lower()


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


DEV_DEMO_MODE: bool = _env_flag("DEV_DEMO_MODE", APP_MODE == "dev")

_FIXTURES_DIR = Path(__file__).parent.parent / "dev" / "fixtures"


def demo_lookup_id(ceo_id: str) -> str:
    return DEV_DEMO_CEO_ID if DEV_DEMO_MODE else ceo_id


def _fixture_origin_date(value: object) -> date | None:
    found: list[date] = []

    def _collect(item: object) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                _collect(nested)
            return
        if isinstance(item, list):
            for nested in item:
                _collect(nested)
            return
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return
            try:
                found.append(datetime.fromisoformat(text.replace("Z", "+00:00")).date())
                return
            except ValueError:
                try:
                    found.append(date.fromisoformat(text))
                except ValueError:
                    return

    _collect(value)
    return min(found) if found else None


def _world_date_shift(value: object) -> timedelta:
    origin = _fixture_origin_date(value)
    if origin is None:
        return timedelta(0)
    return datetime.now().astimezone().date() - origin


def _shift_iso_value(value: str, delta: timedelta) -> str:
    try:
        parsed_dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError:
            return value
        return (parsed_date + delta).isoformat()
    return (parsed_dt + delta).isoformat()


def _shift_fixture_dates(value: object, delta: timedelta) -> object:
    if isinstance(value, dict):
        return {key: _shift_fixture_dates(item, delta) for key, item in value.items()}
    if isinstance(value, list):
        return [_shift_fixture_dates(item, delta) for item in value]
    if isinstance(value, str):
        return _shift_iso_value(value, delta)
    return value


def _dedupe_calendar_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str | None, str | None, str | None]] = set()
    deduped: list[dict[str, object]] = []
    for event in events:
        title = str(event.get("title") or "").strip().lower() or None
        starts_at = str(event.get("starts_at") or event.get("start_time") or "").strip() or None
        ends_at = str(event.get("ends_at") or event.get("end_time") or "").strip() or None
        key = (title, starts_at, ends_at)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file from src/dev/fixtures/<name>.json.

    Returns empty dict if file not found — connector tools handle missing
    fixtures gracefully by falling through to their error path.
    """
    path = _FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        return {}
    with path.open() as f:
        data = json.load(f)
    shifted = _shift_fixture_dates(data, _world_date_shift(data))
    if name == "gcal_events" and isinstance(shifted, dict):
        events = shifted.get("upcoming_events")
        if isinstance(events, list):
            shifted["upcoming_events"] = _dedupe_calendar_events([event for event in events if isinstance(event, dict)])
    return shifted

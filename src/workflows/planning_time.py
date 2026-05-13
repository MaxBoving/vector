from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from src.workflows.planning_types import PlanningWindow


def resolve_date_window_semantic(text: str, today: date) -> tuple[str, str | None] | None:
    """Use a small LLM call to classify any natural-language time reference into
    a horizon string and a human-readable label.

    Returns (horizon, label) where horizon is one of:
      today | tomorrow | this_week | next_week | week_after_next | unspecified

    Returns None if the LLM is unavailable, so callers must handle that.
    """
    try:
        from src.core.llm import LLMClient  # local import to avoid circular deps at module load

        weekday_name = today.strftime("%A")
        next_mon = (today + timedelta(days=7 - today.weekday())).isoformat()
        week_after_mon = (today + timedelta(days=14 - today.weekday())).isoformat()

        system = "You are a precise date classifier for a calendar assistant."
        prompt = (
            f"Today is {today.isoformat()} ({weekday_name}).\n"
            f"Next work-week starts {next_mon}. The week after that starts {week_after_mon}.\n\n"
            f'User text: "{text}"\n\n'
            "Classify any time/date reference in the text into exactly one horizon:\n"
            "  today | tomorrow | this_week | next_week | week_after_next | unspecified\n\n"
            "Return JSON only, no prose:\n"
            '{"horizon": "<value>", "label": "<short human label, e.g. Week of Apr 12>"}\n\n'
            "If no time reference is present, use unspecified with null label."
        )

        client = LLMClient()
        raw = client.complete(prompt, system)
        match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group(0))
        horizon = data.get("horizon", "unspecified")
        label = data.get("label") or None
        if horizon not in {"today", "tomorrow", "this_week", "next_week", "week_after_next", "unspecified"}:
            horizon = "unspecified"
        return horizon, label
    except Exception:
        return None

def build_planning_window(
    time_horizon: str,
    *,
    reference_dt: datetime | None = None,
    target_date: date | None = None,
    target_label: str | None = None,
    workday_start: str = "08:30",
    workday_end: str = "17:00",
) -> PlanningWindow:
    now = (reference_dt or datetime.now().astimezone()).astimezone()
    today = now.date()
    horizon = (
        time_horizon
        if time_horizon in {"today", "tomorrow", "this_week", "next_week", "week_after_next"}
        else "unspecified"
    )

    if target_date is not None and horizon not in {"this_week", "next_week", "week_after_next"}:
        start_date = target_date
        end_date = target_date
    elif horizon == "tomorrow":
        start_date = today + timedelta(days=1)
        end_date = start_date
    elif horizon == "this_week":
        workweek_start = current_workweek_start(today)
        start_date = today if today.weekday() < 5 else workweek_start
        end_date = workweek_start + timedelta(days=4)
    elif horizon == "next_week":
        start_date = next_workweek_start(today)
        end_date = start_date + timedelta(days=4)
    elif horizon == "week_after_next":
        start_date = next_workweek_start(today) + timedelta(days=7)
        end_date = start_date + timedelta(days=4)
    else:
        start_date = today
        end_date = today

    return PlanningWindow(
        horizon=horizon,
        start_date=start_date,
        end_date=end_date,
        timezone=str(now.tzinfo or "UTC"),
        workday_start=workday_start,
        workday_end=workday_end,
        target_date=target_date,
        target_label=target_label,
        span_days=(end_date - start_date).days + 1,
    )


def current_workweek_start(today: date) -> date:
    if today.weekday() >= 5:
        return today + timedelta(days=7 - today.weekday())
    return today - timedelta(days=today.weekday())


def next_workweek_start(today: date) -> date:
    return current_workweek_start(today) + timedelta(days=7)

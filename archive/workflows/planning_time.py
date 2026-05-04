from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from src.workflows.planning_types import PlanningWindow


WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def resolve_time_horizon_target(
    text: str,
    *,
    reference_dt: datetime | None = None,
    this_week_patterns: tuple[str, ...] = (),
    next_week_patterns: tuple[str, ...] = (),
) -> tuple[str, date | None, str | None]:
    lowered = str(text or "").strip().lower()
    today = (reference_dt or datetime.now().astimezone()).date()

    explicit_horizon = "unspecified"
    if any(pattern in lowered for pattern in next_week_patterns):
        explicit_horizon = "next_week"
    elif any(pattern in lowered for pattern in this_week_patterns) or "week" in lowered:
        explicit_horizon = "this_week"
    elif "tomorrow" in lowered:
        explicit_horizon = "tomorrow"
    elif "today" in lowered:
        explicit_horizon = "today"

    if explicit_horizon == "today":
        return "today", today, "Today"
    if explicit_horizon == "tomorrow":
        return "tomorrow", today + timedelta(days=1), "Tomorrow"

    weekday_name = next((name for name in WEEKDAY_INDEX if name in lowered), None)
    if weekday_name:
        weekday_index = WEEKDAY_INDEX[weekday_name]
        if explicit_horizon == "next_week":
            monday = next_workweek_start(today)
            target = monday + timedelta(days=weekday_index)
            return "next_week", target, f"{weekday_name.title()} next week"
        if explicit_horizon == "this_week":
            monday = current_workweek_start(today)
            target = monday + timedelta(days=weekday_index)
            return "this_week", target, f"{weekday_name.title()} this week"

        this_week_target = current_workweek_start(today) + timedelta(days=weekday_index)
        if today.weekday() >= 5:
            return "this_week", this_week_target, f"{weekday_name.title()} this week"
        if this_week_target >= today:
            return "this_week", this_week_target, weekday_name.title()
        next_week_target = next_workweek_start(today) + timedelta(days=weekday_index)
        return "next_week", next_week_target, f"Next {weekday_name.title()}"

    return explicit_horizon, None, None


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


_TIME_CUES = re.compile(
    r"\b(today|tomorrow|week|monday|tuesday|wednesday|thursday|friday|"
    r"next|this|after|following|fortnight|two weeks|month)\b",
    re.IGNORECASE,
)


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
    )


def current_workweek_start(today: date) -> date:
    if today.weekday() >= 5:
        return today + timedelta(days=7 - today.weekday())
    return today - timedelta(days=today.weekday())


def next_workweek_start(today: date) -> date:
    return current_workweek_start(today) + timedelta(days=7)

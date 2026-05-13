from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Iterable

from src.workflows.action_items import filter_structured_watch_for_window, normalize_structured_watch
from src.workflows.planning_types import (
    AvailableSlot,
    BusyInterval,
    PlanExecutionResult,
    PlanExecutionStepResult,
    PlanningCandidate,
    PlanningWindow,
    RequestPlan,
    ScheduledCandidate,
)
from src.workflows.planning_time import build_planning_window


def execute_planner_path(
    *,
    message: str,
    request_plan: RequestPlan,
    email_event: dict[str, Any],
    calendar_event: dict[str, Any],
    document_context: dict[str, Any] | None = None,
    reference_dt: datetime | None = None,
) -> PlanExecutionResult:
    now = (reference_dt or datetime.now().astimezone()).astimezone()
    planning_window = build_planning_window(
        request_plan.time_horizon,
        reference_dt=now,
        target_date=request_plan.target_date,
        target_label=request_plan.target_label,
    )
    document_context = document_context or {}
    requested_sources = request_plan.requested_context_sources
    gather_email = "email" in requested_sources or bool(request_plan.planning_metadata.get("mentions_inbox"))
    gather_calendar = "calendar" in requested_sources or request_plan.time_horizon in {"today", "tomorrow", "this_week", "next_week", "week_after_next"}
    gather_documents = "documents" in requested_sources or bool(document_context)

    ranked_threads = _actionable_threads(email_event.get("ranked_threads", [])) if gather_email else []
    structured_watch = normalize_structured_watch(
        dict(email_event.get("structured_watch", {})),
        upcoming_events=list(calendar_event.get("upcoming_events", [])),
        reference_dt=now,
    ) if gather_email else {}
    upcoming_events = _events_in_window(calendar_event.get("upcoming_events", []), planning_window) if gather_calendar else []
    structured_watch = filter_structured_watch_for_window(structured_watch, planning_window=planning_window) if gather_email else {}
    execution_steps: list[PlanExecutionStepResult] = [
        PlanExecutionStepResult(
            key="gather_email",
            status="completed" if gather_email else "skipped",
            details={"actionable_thread_count": len(ranked_threads)},
        ),
        PlanExecutionStepResult(
            key="gather_calendar",
            status="completed" if gather_calendar else "skipped",
            details={"meeting_count": len(upcoming_events)},
        ),
        PlanExecutionStepResult(
            key="gather_documents",
            status="completed" if gather_documents else "skipped",
            details={"attachment_count": len(document_context.get("attachments", []))},
        ),
    ]

    candidates = _build_planning_candidates(
        ranked_threads=ranked_threads,
        structured_watch=structured_watch,
        upcoming_events=upcoming_events,
        document_context=document_context,
        planning_window=planning_window,
    )
    execution_steps.append(
        PlanExecutionStepResult(
            key="build_candidates",
            status="completed",
            details={"candidate_count": len(candidates)},
        )
    )

    busy_intervals = _busy_intervals(upcoming_events)
    available_slots, scheduled_candidates = _place_candidates_into_slots(
        planning_window=planning_window,
        busy_intervals=busy_intervals,
        candidates=candidates,
    )
    schedule_blocks = _render_schedule_blocks(
        planning_window=planning_window,
        scheduled_candidates=scheduled_candidates,
    )
    sparse_guidance = len(candidates) > 0 and len(scheduled_candidates) == 0
    fallback_reasons = []
    if sparse_guidance:
        fallback_reasons.append(
            f"Not enough placeable planning evidence was available for {planning_window.horizon.replace('_', ' ')}."
        )
    execution_steps.append(
        PlanExecutionStepResult(
            key="place_schedule",
            status="completed",
            details={
                "available_slot_count": len(available_slots),
                "placed_candidate_count": len(scheduled_candidates),
                "sparse_guidance": sparse_guidance,
            },
        )
    )
    execution_steps.append(
        PlanExecutionStepResult(
            key="synthesize_response",
            status="completed",
            details={"schedule_block_count": len(schedule_blocks)},
        )
    )

    evidence_summary = {
        "actionable_thread_count": len(ranked_threads),
        "meeting_count": len(upcoming_events),
        "deadline_count": len(
            [
                deadline
                for deadline in (structured_watch.get("deadlines", []) or [])
                if isinstance(deadline, dict) and deadline.get("deadline")
            ]
        ),
        "context_source_count": len(
            [
                source
                for source in (
                    "email" if ranked_threads else None,
                    "calendar" if upcoming_events else None,
                    "documents" if document_context else None,
                )
                if source
            ]
        ),
        "candidate_count": len(candidates),
        "placed_candidate_count": len(scheduled_candidates),
        "unplaced_candidate_count": max(len(candidates) - len(scheduled_candidates), 0),
        "available_slot_count": len(available_slots),
        "sparse_guidance": sparse_guidance,
    }
    return PlanExecutionResult(
        planning_window=planning_window,
        execution_steps=execution_steps,
        ranked_threads=ranked_threads,
        structured_watch=structured_watch,
        upcoming_events=upcoming_events,
        document_context=document_context,
        candidates=candidates,
        available_slots=available_slots,
        scheduled_candidates=scheduled_candidates,
        schedule_blocks=schedule_blocks,
        evidence_summary=evidence_summary,
        sparse_guidance=sparse_guidance,
        fallback_reasons=fallback_reasons,
    )


def execute_weekly_compound_plan(
    *,
    message: str,
    request_plan: RequestPlan,
    email_event: dict[str, Any],
    calendar_event: dict[str, Any],
    document_context: dict[str, Any] | None = None,
    reference_dt: datetime | None = None,
) -> PlanExecutionResult:
    return execute_planner_path(
        message=message,
        request_plan=request_plan,
        email_event=email_event,
        calendar_event=calendar_event,
        document_context=document_context,
        reference_dt=reference_dt,
    )


def initialize_planner_execution(
    *,
    request_plan: RequestPlan,
    reference_dt: datetime | None = None,
) -> dict[str, Any]:
    planning_window = build_planning_window(request_plan.time_horizon, reference_dt=reference_dt)
    return {
        "execution_mode": request_plan.planning_metadata.get("execution_model", "carrier_workflow_with_planner_execution"),
        "planning_horizon": request_plan.time_horizon,
        "planning_window": planning_window.model_dump(mode="json"),
        "executed_plan_steps": [],
        "evidence_summary": {},
        "sparse_guidance": False,
        "candidates": [],
        "available_slots": [],
        "scheduled_candidates": [],
        "schedule_blocks": [],
    }


def run_planner_stage(
    *,
    stage_key: str,
    request_plan: RequestPlan,
    event_payload: dict[str, Any],
    planner_execution: dict[str, Any] | None = None,
    reference_dt: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    planner_execution = dict(planner_execution or initialize_planner_execution(request_plan=request_plan, reference_dt=reference_dt))
    planning_window = PlanningWindow(**planner_execution["planning_window"])
    updated_payload = dict(event_payload)
    step_details: dict[str, Any]
    status = "completed"

    if stage_key == "gather_email":
        gather_email = "email" in request_plan.requested_context_sources or bool(request_plan.planning_metadata.get("mentions_inbox"))
        ranked_threads = _actionable_threads((updated_payload.get("email_watch", {}) or {}).get("ranked_threads", [])) if gather_email else []
        structured_watch = normalize_structured_watch(
            dict((updated_payload.get("email_watch", {}) or {}).get("structured_watch", {})),
            upcoming_events=list((updated_payload.get("calendar_watch", {}) or {}).get("upcoming_events", [])),
            reference_dt=(reference_dt or datetime.now().astimezone()).astimezone(),
        ) if gather_email else {}
        # If calendar was already gathered (meeting prep order), filter threads by attendees
        upcoming_events = list(updated_payload.get("upcoming_events", []) or [])
        attendee_threads: list[dict[str, Any]] = []
        if upcoming_events and ranked_threads:
            attendee_emails = _extract_meeting_attendees(upcoming_events)
            if attendee_emails:
                attendee_threads, general_threads = _split_threads_by_attendees(ranked_threads, attendee_emails)
                # Attendee threads surface first so build_candidates picks them up
                ranked_threads = attendee_threads + general_threads
                updated_payload["attendee_emails"] = sorted(attendee_emails)
        updated_payload["ranked_threads"] = ranked_threads
        updated_payload["attendee_threads"] = attendee_threads
        updated_payload["structured_watch"] = filter_structured_watch_for_window(structured_watch, planning_window=planning_window)
        step_details = {"actionable_thread_count": len(ranked_threads), "attendee_thread_count": len(attendee_threads)}
        status = "completed" if gather_email else "skipped"
    elif stage_key == "gather_calendar":
        gather_calendar = "calendar" in request_plan.requested_context_sources or request_plan.time_horizon in {"today", "tomorrow", "this_week", "next_week", "week_after_next"}
        upcoming_events = _events_in_window((updated_payload.get("calendar_watch", {}) or {}).get("upcoming_events", []), planning_window) if gather_calendar else []
        updated_payload["upcoming_events"] = upcoming_events
        step_details = {"meeting_count": len(upcoming_events)}
        status = "completed" if gather_calendar else "skipped"
    elif stage_key == "gather_documents":
        document_context = dict(updated_payload.get("document_context", {}) or {})
        updated_payload["document_context"] = document_context
        step_details = {"attachment_count": len(document_context.get("attachments", []))}
        status = "completed" if document_context or "documents" in request_plan.requested_context_sources else "skipped"
    elif stage_key == "build_candidates":
        candidates = _build_planning_candidates(
            ranked_threads=list(updated_payload.get("ranked_threads", []) or []),
            structured_watch=dict(updated_payload.get("structured_watch", {}) or {}),
            upcoming_events=list(updated_payload.get("upcoming_events", []) or []),
            document_context=dict(updated_payload.get("document_context", {}) or {}),
            planning_window=planning_window,
        )
        planner_execution["candidates"] = [candidate.model_dump() for candidate in candidates]
        step_details = {"candidate_count": len(candidates)}
    elif stage_key == "place_schedule":
        candidates = [PlanningCandidate(**candidate) for candidate in (planner_execution.get("candidates", []) or [])]
        available_slots, scheduled_candidates = _place_candidates_into_slots(
            planning_window=planning_window,
            busy_intervals=_busy_intervals(list(updated_payload.get("upcoming_events", []) or [])),
            candidates=candidates,
        )
        schedule_blocks = _render_schedule_blocks(
            planning_window=planning_window,
            scheduled_candidates=scheduled_candidates,
        )
        # Only flag sparse for schedule workflows where slot placement is meaningful.
        # meeting_prep builds prep-item candidates, not time-blocking candidates —
        # an empty schedule result is expected and should not flag as sparse.
        is_schedule_workflow = request_plan.target_workflow not in {"meeting_prep"}
        sparse_guidance = is_schedule_workflow and len(candidates) > 0 and len(scheduled_candidates) == 0
        evidence_summary = {
            "actionable_thread_count": len(list(updated_payload.get("ranked_threads", []) or [])),
            "meeting_count": len(list(updated_payload.get("upcoming_events", []) or [])),
            "deadline_count": len(
                [
                    deadline
                    for deadline in (dict(updated_payload.get("structured_watch", {}) or {}).get("deadlines", []) or [])
                    if isinstance(deadline, dict) and deadline.get("deadline")
                ]
            ),
            "context_source_count": len(
                [
                    source
                    for source in (
                        "email" if updated_payload.get("ranked_threads") else None,
                        "calendar" if updated_payload.get("upcoming_events") else None,
                        "documents" if updated_payload.get("document_context") else None,
                    )
                    if source
                ]
            ),
            "candidate_count": len(candidates),
            "placed_candidate_count": len(scheduled_candidates),
            "unplaced_candidate_count": max(len(candidates) - len(scheduled_candidates), 0),
            "available_slot_count": len(available_slots),
            "sparse_guidance": sparse_guidance,
        }
        planner_execution["available_slots"] = [slot.model_dump() for slot in available_slots]
        planner_execution["scheduled_candidates"] = [candidate.model_dump() for candidate in scheduled_candidates]
        planner_execution["schedule_blocks"] = schedule_blocks
        planner_execution["evidence_summary"] = evidence_summary
        planner_execution["sparse_guidance"] = sparse_guidance
        step_details = {
            "available_slot_count": len(available_slots),
            "placed_candidate_count": len(scheduled_candidates),
            "sparse_guidance": sparse_guidance,
        }
    elif stage_key == "synthesize_response":
        planning_context = dict(updated_payload.get("planning_context", {}) or {})
        planning_context.update(
            {
                "execution_steps": planner_execution.get("executed_plan_steps", []),
                "planning_window": planner_execution.get("planning_window"),
                "evidence_summary": planner_execution.get("evidence_summary", {}),
                "sparse_guidance": planner_execution.get("sparse_guidance", False),
                "execution_model": planner_execution.get("execution_mode", "carrier_workflow_with_planner_execution"),
                "attendee_threads": updated_payload.get("attendee_threads", []),
                "attendee_emails": updated_payload.get("attendee_emails", []),
            }
        )
        updated_payload["planning_context"] = planning_context
        updated_payload["plan_execution"] = {
            "planning_window": planner_execution.get("planning_window"),
            "schedule_blocks": planner_execution.get("schedule_blocks", []),
            "scheduled_candidates": planner_execution.get("scheduled_candidates", []),
            "evidence_summary": planner_execution.get("evidence_summary", {}),
            "sparse_guidance": planner_execution.get("sparse_guidance", False),
        }
        updated_payload["compound_execution"] = {
            "path": planner_execution.get("execution_mode", "carrier_workflow_with_planner_execution"),
            "status": "completed",
            "steps": planner_execution.get("executed_plan_steps", []),
        }
        step_details = {"schedule_block_count": len(planner_execution.get("schedule_blocks", []) or [])}
    else:
        raise ValueError(f"Unsupported planner stage: {stage_key}")

    step_record = {
        "key": stage_key,
        "status": status,
        "details": step_details,
    }
    planner_execution.setdefault("executed_plan_steps", [])
    planner_execution["executed_plan_steps"].append(step_record)
    return updated_payload, planner_execution, step_record


def _actionable_threads(ranked_threads: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(thread)
        for thread in ranked_threads
        if not thread.get("suppressed") and thread.get("category") != "promotional"
    ]


def _events_in_window(upcoming_events: Iterable[dict[str, Any]], planning_window: PlanningWindow) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for event in upcoming_events:
        starts_at = _parse_datetime(event.get("starts_at"))
        if not starts_at:
            continue
        event_date = starts_at.date()
        if planning_window.start_date <= event_date <= planning_window.end_date and starts_at.weekday() < 5:
            results.append(dict(event))
    return results


def _build_planning_candidates(
    *,
    ranked_threads: list[dict[str, Any]],
    structured_watch: dict[str, Any],
    upcoming_events: list[dict[str, Any]],
    document_context: dict[str, Any],
    planning_window: PlanningWindow,
) -> list[PlanningCandidate]:
    asks = [item.get("ask") for item in (structured_watch.get("asks", []) or []) if item.get("ask")]
    deadlines = [item.get("deadline") for item in (structured_watch.get("deadlines", []) or []) if item.get("deadline")]
    documents = [
        item.get("document")
        for item in (structured_watch.get("implied_docs", []) or [])
        if item.get("document")
    ]
    if document_context.get("attachments"):
        documents.extend(
            attachment.get("filename", "attached document")
            for attachment in document_context.get("attachments", [])
            if attachment.get("filename")
        )

    candidates: list[PlanningCandidate] = []
    for index, thread in enumerate(ranked_threads[:3]):
        urgency = int(thread.get("importance_score", 0)) + (20 if index == 0 else 0)
        candidates.append(
            PlanningCandidate(
                title=str(thread.get("subject") or "Priority thread"),
                content=(
                    f"Review '{thread.get('subject', 'priority thread')}' from "
                    f"{thread.get('latest_sender', 'a sender')} and decide the next step."
                ),
                urgency=urgency,
                duration_minutes=30,
                source_refs=[f"thread:{thread.get('subject', 'priority_thread')}"],
                rationale="Executive inbox thread with actionable signal.",
            )
        )

    for index, deadline in enumerate(deadlines[:2]):
        candidates.append(
            PlanningCandidate(
                title=f"Deadline: {deadline}",
                content=f"Work the items tied to the deadline '{deadline}' so nothing slips.",
                urgency=_deadline_urgency(deadline),
                duration_minutes=30 if index == 0 else 15,
                constraints=["deadline"],
                source_refs=[f"deadline:{deadline}"],
                rationale="Structured watch detected a deadline in the inbox evidence.",
            )
        )

    for index, ask in enumerate(asks[:2]):
        candidates.append(
            PlanningCandidate(
                title=f"Ask: {ask}",
                content=f"Address the ask: {ask}.",
                urgency=78 - (index * 8),
                duration_minutes=30 if index == 0 else 15,
                constraints=["ask"],
                source_refs=[f"ask:{ask}"],
                rationale="Structured watch detected an explicit ask.",
            )
        )

    for index, event in enumerate(upcoming_events[:2]):
        title = str(event.get("title") or "Upcoming meeting")
        candidates.append(
            PlanningCandidate(
                title=title,
                content=f"Prepare for {title} at {_format_schedule_time(event.get('starts_at'))}.",
                urgency=_meeting_prep_urgency(event),
                duration_minutes=30 if index == 0 else 15,
                constraints=["meeting_prep"],
                source_refs=[f"meeting:{title}"],
                rationale="Calendar evidence falls inside the requested planning window.",
            )
        )

    for index, document in enumerate(documents[:2]):
        candidates.append(
            PlanningCandidate(
                title=f"Document prep: {document}",
                content=f"Outline the needed {document} so it is ready within {planning_window.horizon.replace('_', ' ')}.",
                urgency=46 - (index * 6),
                duration_minutes=15,
                constraints=["document_prep"],
                source_refs=[f"document:{document}"],
                rationale="Document context should influence the weekly plan.",
            )
        )

    if candidates:
        candidates.append(
            PlanningCandidate(
                title="Close the planning window",
                content=(
                    f"Close {planning_window.horizon.replace('_', ' ')} by deciding which items should become "
                    "approved calendar or email actions."
                ),
                urgency=24,
                duration_minutes=15,
                source_refs=["planner:wrap_up"],
                rationale="Reserve time to convert the plan into explicit follow-up actions.",
            )
        )

    candidates.sort(key=lambda item: (item.urgency, item.duration_minutes), reverse=True)
    return candidates[:5]


def _place_candidates_into_slots(
    *,
    planning_window: PlanningWindow,
    busy_intervals: list[BusyInterval],
    candidates: list[PlanningCandidate],
) -> tuple[list[AvailableSlot], list[ScheduledCandidate]]:
    mutable_busy = list(busy_intervals)
    available_slots: list[AvailableSlot] = []
    scheduled_candidates: list[ScheduledCandidate] = []
    for candidate in candidates:
        placed_slot = _find_slot_for_candidate(
            planning_window=planning_window,
            busy_intervals=mutable_busy,
            duration_minutes=candidate.duration_minutes,
        )
        if not placed_slot:
            continue
        available_slots.append(placed_slot)
        scheduled_candidates.append(ScheduledCandidate(candidate=candidate, slot=placed_slot))
        mutable_busy.append(BusyInterval(starts_at=placed_slot.starts_at, ends_at=placed_slot.ends_at, title=candidate.title))
        mutable_busy.sort(key=lambda interval: interval.starts_at)
    return available_slots, scheduled_candidates


def _find_slot_for_candidate(
    *,
    planning_window: PlanningWindow,
    busy_intervals: list[BusyInterval],
    duration_minutes: int,
) -> AvailableSlot | None:
    duration = timedelta(minutes=duration_minutes)
    for workday in _workdays_in_window(planning_window):
        day_start = datetime.combine(workday, _parse_clock(planning_window.workday_start))
        day_end = datetime.combine(workday, _parse_clock(planning_window.workday_end))
        if busy_intervals and _parse_datetime(busy_intervals[0].starts_at):
            tzinfo = _parse_datetime(busy_intervals[0].starts_at).tzinfo
            if tzinfo:
                day_start = day_start.replace(tzinfo=tzinfo)
                day_end = day_end.replace(tzinfo=tzinfo)
        cursor = day_start
        day_busy = [
            interval
            for interval in busy_intervals
            if (parsed_start := _parse_datetime(interval.starts_at)) and parsed_start.date() == workday
        ]
        day_busy.sort(key=lambda interval: interval.starts_at)
        for interval in day_busy:
            busy_start = _parse_datetime(interval.starts_at)
            busy_end = _parse_datetime(interval.ends_at)
            if not busy_start or not busy_end:
                continue
            if cursor + duration <= busy_start:
                return AvailableSlot(
                    starts_at=cursor.isoformat(),
                    ends_at=(cursor + duration).isoformat(),
                    label=_format_slot_label(planning_window.horizon, cursor, cursor + duration),
                )
            if cursor < busy_end:
                cursor = busy_end
        if cursor + duration <= day_end:
            return AvailableSlot(
                starts_at=cursor.isoformat(),
                ends_at=(cursor + duration).isoformat(),
                label=_format_slot_label(planning_window.horizon, cursor, cursor + duration),
            )
    return None


def _busy_intervals(upcoming_events: list[dict[str, Any]]) -> list[BusyInterval]:
    intervals: list[BusyInterval] = []
    for event in upcoming_events:
        start = _parse_datetime(event.get("starts_at"))
        if not start:
            continue
        end = _parse_datetime(event.get("ends_at")) or (start + timedelta(minutes=30))
        intervals.append(
            BusyInterval(
                starts_at=start.isoformat(),
                ends_at=end.isoformat(),
                title=str(event.get("title") or "Busy"),
            )
        )
    intervals.sort(key=lambda interval: interval.starts_at)
    return intervals


def _render_schedule_blocks(
    *,
    planning_window: PlanningWindow,
    scheduled_candidates: list[ScheduledCandidate],
) -> list[str]:
    blocks: list[str] = []
    for scheduled in scheduled_candidates:
        blocks.append(f"{scheduled.slot.label}: {scheduled.candidate.content}")
    if blocks:
        return blocks
    return [
        f"There is not enough actionable inbox or calendar evidence to build a concrete schedule for {planning_window.horizon.replace('_', ' ')}."
    ]


def _workdays_in_window(planning_window: PlanningWindow) -> list[date]:
    workdays: list[date] = []
    cursor = planning_window.start_date
    while cursor <= planning_window.end_date:
        if cursor.weekday() < 5:
            workdays.append(cursor)
        cursor += timedelta(days=1)
    return workdays


def _parse_clock(value: str) -> time:
    hours, minutes = value.split(":", maxsplit=1)
    return time(hour=int(hours), minute=int(minutes))


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.astimezone()
    except ValueError:
        return None


def _format_schedule_time(value: Any) -> str:
    parsed = _parse_datetime(value)
    if not parsed:
        return "scheduled"
    return parsed.astimezone().strftime("%-I:%M %p")


def _format_slot_label(horizon: str, start: datetime, end: datetime) -> str:
    time_label = f"{start.astimezone().strftime('%-I:%M %p')}-{end.astimezone().strftime('%-I:%M %p')}"
    if horizon in {"this_week", "next_week"}:
        return f"{start.astimezone().strftime('%a %b %d')} {time_label}"
    return time_label


def _extract_meeting_attendees(upcoming_events: list[dict[str, Any]]) -> set[str]:
    """Extract unique attendee email addresses from a list of calendar events."""
    attendees: set[str] = set()
    for event in upcoming_events:
        for entry in event.get("attendees") or []:
            if isinstance(entry, str) and "@" in entry:
                attendees.add(entry.lower())
            elif isinstance(entry, dict):
                email = entry.get("email") or entry.get("address") or ""
                if "@" in email:
                    attendees.add(email.lower())
    return attendees


def _split_threads_by_attendees(
    threads: list[dict[str, Any]],
    attendee_emails: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split threads into attendee-relevant and general buckets.

    A thread is attendee-relevant if its sender or any participant matches
    a meeting attendee email. Attendee threads are returned first so
    build_candidates naturally picks them as the highest-priority items.
    """
    attendee_threads: list[dict[str, Any]] = []
    general_threads: list[dict[str, Any]] = []
    for thread in threads:
        sender = (thread.get("latest_sender") or thread.get("sender") or "").lower()
        participants = [p.lower() for p in (thread.get("participants") or []) if isinstance(p, str)]
        thread_emails = {sender} | set(participants)
        if thread_emails & attendee_emails:
            attendee_threads.append(thread)
        else:
            general_threads.append(thread)
    return attendee_threads, general_threads


def _deadline_urgency(deadline: str) -> int:
    lowered = deadline.lower()
    if "today" in lowered or "eod" in lowered or "end of day" in lowered:
        return 96
    if "tomorrow" in lowered:
        return 90
    if "this week" in lowered:
        return 82
    if "next week" in lowered:
        return 68
    return 74


def _meeting_prep_urgency(event: dict[str, Any]) -> int:
    start = _parse_datetime(event.get("starts_at"))
    if not start:
        return 58
    hours_until = max((start.astimezone() - datetime.now().astimezone()).total_seconds() / 3600, 0)
    if hours_until <= 2:
        return 88
    if hours_until <= 6:
        return 76
    if hours_until <= 12:
        return 66
    return 54

from __future__ import annotations

from typing import Any

from src.workflows.action_items import normalize_structured_watch
from src.workflows.planning_types import RequestPlan


def ranked_threads_for_request(email_event: dict[str, Any], request_plan: RequestPlan | None) -> list[dict[str, Any]]:
    ranked_threads = list(email_event.get("ranked_threads", []))
    if not request_plan or not request_plan.is_compound:
        return ranked_threads
    actionable_threads = [thread for thread in ranked_threads if not thread.get("suppressed")]
    return actionable_threads or ranked_threads


def build_planning_context(
    *,
    message: str,
    request_plan: RequestPlan | None,
    ranked_threads: list[dict[str, Any]],
    upcoming_events: list[dict[str, Any]],
    structured_watch: dict[str, Any] | None = None,
    document_context: dict[str, Any] | None = None,
    execution_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not request_plan:
        return {}
    actionable_threads = [thread for thread in ranked_threads if not thread.get("suppressed")]
    deadlines = (structured_watch or {}).get("deadlines", []) or []
    retrieval_plan = (
        request_plan.retrieval_plan.model_dump(mode="json")
        if request_plan.retrieval_plan.sources
        else {
            "sources": [
                {
                    "source": source,
                    "required": True,
                    "priority": idx,
                    "rationale": f"Planner marked {source} as required for this request.",
                }
                for idx, source in enumerate(request_plan.requested_context_sources)
            ],
            "time_horizon": request_plan.time_horizon,
            "target_date": str(request_plan.target_date) if request_plan.target_date else None,
            "target_label": request_plan.target_label,
            "rationale": request_plan.rationale,
            "planner_version": request_plan.planning_metadata.get("planner_version", ""),
            "execution_model": request_plan.planning_metadata.get("execution_model", "single_workflow"),
        }
    )
    context_source_count = len(
        [
            source
            for source in (
                "email" if ranked_threads else None,
                "calendar" if upcoming_events else None,
                "documents" if document_context else None,
            )
            if source
        ]
    )
    return {
        "mode": request_plan.mode,
        "time_horizon": request_plan.time_horizon,
        "target_date": str(request_plan.target_date) if request_plan.target_date else None,
        "target_label": request_plan.target_label,
        "needed_context_sources": list(request_plan.requested_context_sources),
        "retrieval_plan": retrieval_plan,
        "subtasks": [subtask.model_dump() for subtask in request_plan.subtasks],
        "rationale": request_plan.rationale,
        "query": message,
        "execution_model": request_plan.planning_metadata.get("execution_model", "single_workflow"),
        "execution_steps": execution_steps or [],
        "evidence_summary": {
            "actionable_thread_count": len(actionable_threads),
            "meeting_count": len(upcoming_events),
            "deadline_count": len([deadline for deadline in deadlines if isinstance(deadline, dict) and deadline.get("deadline")]),
            "context_source_count": context_source_count,
        },
    }


def build_watch_event_payload(
    *,
    email_event: dict[str, Any],
    calendar_event: dict[str, Any],
    message: str | None = None,
    request_plan: RequestPlan | None = None,
    document_context: dict[str, Any] | None = None,
    route_decision_payload: dict[str, Any] | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ranked_threads = ranked_threads_for_request(email_event, request_plan)
    structured_watch = normalize_structured_watch(
        dict(email_event.get("structured_watch", {})),
        upcoming_events=list(calendar_event.get("upcoming_events", [])),
    )
    upcoming_events = list(calendar_event.get("upcoming_events", []))
    primary_event = dict(calendar_event)
    payload: dict[str, Any] = {
        "email_watch": email_event,
        "calendar_watch": calendar_event,
        "ranked_threads": ranked_threads,
        "structured_watch": structured_watch,
        "upcoming_events": upcoming_events,
        # Use `or` fallbacks so empty-event errors don't store explicit None
        # values that would bypass .get(key, default) patterns downstream.
        "title": primary_event.get("title") or "This week and next week",
        "subject": email_event.get("subject") or "Important inbox threads",
        "importance_reasons": email_event.get("importance_reasons") or [],
        "related_threads": primary_event.get("related_threads") or [],
        "attendees": primary_event.get("attendees") or [],
    }
    # Optional metadata — only include when present so that absent values simply
    # fall through to .get(key, default) callers rather than returning None.
    for _key, _val in (
        ("sender", email_event.get("sender")),
        ("importance", email_event.get("importance")),
        ("importance_score", email_event.get("importance_score")),
        ("starts_at", primary_event.get("starts_at")),
    ):
        if _val is not None:
            payload[_key] = _val
    if extra_payload:
        payload.update(extra_payload)
    if message is not None:
        payload["query"] = message
        payload["planning_context"] = build_planning_context(
            message=message,
            request_plan=request_plan,
            ranked_threads=ranked_threads,
            upcoming_events=upcoming_events,
            structured_watch=structured_watch,
            document_context=document_context,
        )
    if document_context:
        payload["document_context"] = document_context
    if route_decision_payload is not None:
        payload["route_decision"] = route_decision_payload
    return payload

from __future__ import annotations

from typing import Any

from src.workflows.types import WorkflowDefinition


_WORKFLOW_TITLES: dict[str, str] = {
    "report_generation": "Executive Report",
    "document_explanation": "Business Implication Brief",
    "email_ingestion": "Inbox Brief",
    "email_watcher": "Inbox Brief",
    "calendar_briefing": "Calendar Brief",
    "morning_brief": "Morning Brief",
    "schedule_planning": "Executive Schedule",
    "day_schedule_planning": "Day Schedule",
    "week_schedule_planning": "Week Schedule",
    "meeting_prep": "Meeting Brief",
    "weekly_recap": "Week in Review",
}

_PRESENTATION_TO_WORKFLOW: dict[tuple[str, str], str] = {
    ("brief", "weekly_watch"): "morning_brief",
    ("brief", "weekly_recap"): "weekly_recap",
    ("brief", "meeting_prep"): "meeting_prep",
    ("calendar", "day_grid"): "calendar_briefing",
    ("schedule", "timeline"): "schedule_planning",
    ("report", "document"): "document_explanation",
}


def workflow_definition_for_type(workflow_type: str) -> WorkflowDefinition | None:
    from src.workflows.calendar_briefing import CALENDAR_BRIEFING_WORKFLOW
    from src.workflows.day_schedule_planning import DAY_SCHEDULE_PLANNING_WORKFLOW
    from src.workflows.document_explanation import DOCUMENT_EXPLANATION_WORKFLOW
    from src.workflows.email_ingestion import EMAIL_INGESTION_WORKFLOW
    from src.workflows.email_watcher import EMAIL_WATCHER_WORKFLOW
    from src.workflows.meeting_prep import MEETING_PREP_WORKFLOW
    from src.workflows.morning_brief import MORNING_BRIEF_WORKFLOW
    from src.workflows.report_generation import REPORT_GENERATION_WORKFLOW
    from src.workflows.schedule_planning import SCHEDULE_PLANNING_WORKFLOW
    from src.workflows.week_schedule_planning import WEEK_SCHEDULE_PLANNING_WORKFLOW
    from src.workflows.weekly_recap import WEEKLY_RECAP_WORKFLOW

    registry: dict[str, WorkflowDefinition] = {
        "report_generation": REPORT_GENERATION_WORKFLOW,
        "document_explanation": DOCUMENT_EXPLANATION_WORKFLOW,
        "email_ingestion": EMAIL_INGESTION_WORKFLOW,
        "email_watcher": EMAIL_WATCHER_WORKFLOW,
        "calendar_briefing": CALENDAR_BRIEFING_WORKFLOW,
        "morning_brief": MORNING_BRIEF_WORKFLOW,
        "schedule_planning": SCHEDULE_PLANNING_WORKFLOW,
        "day_schedule_planning": DAY_SCHEDULE_PLANNING_WORKFLOW,
        "week_schedule_planning": WEEK_SCHEDULE_PLANNING_WORKFLOW,
        "meeting_prep": MEETING_PREP_WORKFLOW,
        "weekly_recap": WEEKLY_RECAP_WORKFLOW,
    }
    return registry.get(workflow_type)


def workflow_title(workflow_type: str) -> str:
    return _WORKFLOW_TITLES.get(workflow_type, "Executive Report")


def workflow_response_type(workflow_type: str) -> str:
    definition = workflow_definition_for_type(workflow_type)
    if definition:
        return str(definition.metadata.get("response_type") or "report")
    return "report"


def workflow_presentation_mode(workflow_type: str) -> str | None:
    definition = workflow_definition_for_type(workflow_type)
    if definition:
        mode = str(definition.metadata.get("presentation_mode") or "").strip()
        if mode:
            return mode

    response_type = workflow_response_type(workflow_type)
    if response_type == "explanation":
        return "report"
    if response_type in {"brief", "report", "schedule", "decision", "draft", "finance", "artifact", "media", "calendar", "clarification", "canvas"}:
        return response_type
    return None


def workflow_presentation_variant(workflow_type: str) -> str | None:
    definition = workflow_definition_for_type(workflow_type)
    if not definition:
        return None
    variant = str(definition.metadata.get("presentation_variant") or "").strip()
    return variant or None


def workflow_type_from_presentation(mode: str | None, variant: str | None) -> str | None:
    normalized_mode = str(mode or "").strip().lower()
    normalized_variant = str(variant or "").strip().lower()
    if not normalized_mode and not normalized_variant:
        return None
    return _PRESENTATION_TO_WORKFLOW.get((normalized_mode, normalized_variant))


def default_presentation_payload(
    workflow_type: str,
    *,
    summary: str | None = None,
) -> dict[str, Any] | None:
    mode = workflow_presentation_mode(workflow_type)
    variant = workflow_presentation_variant(workflow_type)
    if not mode and not variant and not summary:
        return None

    payload: dict[str, Any] = {}
    if mode:
        payload["mode"] = mode
    if variant:
        payload["variant"] = variant
    if summary:
        payload["summary"] = summary
    return payload

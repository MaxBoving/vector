from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EventTriggerType = Literal["webhook", "schedule"]


class EventSurfaceDefinition(BaseModel):
    workflow_type: str
    surface: str
    trigger_type: EventTriggerType
    title: str
    summary: str
    entry_steps: list[str] = Field(default_factory=list)
    future_agent: str | None = None
    example_inputs: list[str] = Field(default_factory=list)


EMAIL_EVENT_SURFACE = EventSurfaceDefinition(
    workflow_type="email_ingestion",
    surface="email",
    trigger_type="webhook",
    title="Executive Email Triage",
    summary="Digest important inbound threads into one advisor-style brief with implications and next actions.",
    entry_steps=["receive_event", "load_company_state", "classify_email", "prepare_brief"],
    future_agent="briefing_agent",
    example_inputs=[
        "Board member email asking for updated runway assumptions.",
        "Customer escalation thread that may affect the quarterly narrative.",
    ],
)


CALENDAR_EVENT_SURFACE = EventSurfaceDefinition(
    workflow_type="calendar_briefing",
    surface="calendar",
    trigger_type="webhook",
    title="Calendar Meeting Prep",
    summary="Generate a compact pre-meeting brief from company state, prior documents, and meeting context.",
    entry_steps=["receive_event", "load_company_state", "retrieve_related_context", "prepare_meeting_brief"],
    future_agent="briefing_agent",
    example_inputs=[
        "Weekly exec staff meeting with CFO and COO.",
        "Board committee review with finance and legal attendees.",
    ],
)


MORNING_BRIEF_EVENT_SURFACE = EventSurfaceDefinition(
    workflow_type="morning_brief",
    surface="morning_brief",
    trigger_type="schedule",
    title="Morning Executive Brief",
    summary="Deliver a scheduled start-of-day digest with priorities, open risks, and the most important follow-ups.",
    entry_steps=["schedule_trigger", "load_company_state", "collect_signals", "prepare_digest"],
    future_agent="briefing_agent",
    example_inputs=[
        "Weekday 6:30 AM local briefing.",
        "Monday strategic priorities digest before leadership meetings.",
    ],
)


EVENT_SURFACE_REGISTRY = {
    EMAIL_EVENT_SURFACE.workflow_type: EMAIL_EVENT_SURFACE,
    CALENDAR_EVENT_SURFACE.workflow_type: CALENDAR_EVENT_SURFACE,
    MORNING_BRIEF_EVENT_SURFACE.workflow_type: MORNING_BRIEF_EVENT_SURFACE,
}


def list_event_surfaces() -> list[EventSurfaceDefinition]:
    return list(EVENT_SURFACE_REGISTRY.values())


def get_event_surface_definition(workflow_type: str) -> EventSurfaceDefinition | None:
    return EVENT_SURFACE_REGISTRY.get(workflow_type)

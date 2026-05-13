from src.workflows.briefing_workflow_factory import build_briefing_workflow


CALENDAR_BRIEFING_WORKFLOW = build_briefing_workflow(
    workflow_type="calendar_briefing",
    failure_title="Calendar Brief Failed",
    failure_summary="The system could not complete this meeting briefing.",
    response_type="brief",
    presentation_mode="calendar",
    presentation_variant="day_grid",
    include_session_history=True,
    include_signals=False,
)

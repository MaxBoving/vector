from src.workflows.briefing_workflow_factory import build_briefing_workflow

SCHEDULE_PLANNING_WORKFLOW = build_briefing_workflow(
    workflow_type="schedule_planning",
    failure_title="Schedule Planning Failed",
    failure_summary="The system could not complete this schedule planning request.",
    response_type="schedule",
    presentation_mode="schedule",
    presentation_variant="timeline",
    include_session_history=True,
    include_signals=True,
    include_live_connector=True,
)

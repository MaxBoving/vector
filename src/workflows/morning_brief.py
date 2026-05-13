from src.workflows.briefing_workflow_factory import build_briefing_workflow


MORNING_BRIEF_WORKFLOW = build_briefing_workflow(
    workflow_type="morning_brief",
    failure_title="Morning Brief Failed",
    failure_summary="The system could not complete this morning briefing.",
    response_type="brief",
    presentation_mode="brief",
    presentation_variant="weekly_watch",
    include_session_history=True,
    include_signals=True,
)

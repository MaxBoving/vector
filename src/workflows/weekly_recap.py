from src.workflows.briefing_workflow_factory import build_briefing_workflow


WEEKLY_RECAP_WORKFLOW = build_briefing_workflow(
    workflow_type="weekly_recap",
    failure_title="Weekly Recap Failed",
    failure_summary="The system could not complete this weekly recap.",
    response_type="brief",
    presentation_mode="brief",
    presentation_variant="weekly_recap",
    include_session_history=True,
    include_signals=True,
)

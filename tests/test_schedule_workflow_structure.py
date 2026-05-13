from __future__ import annotations

from src.workflows.briefing_workflow_factory import build_briefing_workflow


def test_schedule_planning_includes_live_connector_stage() -> None:
    workflow = build_briefing_workflow(
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
    step_names = [step.name for step in workflow.steps]

    assert "load_live_connector" in step_names
    assert step_names.index("load_live_connector") < step_names.index("retrieve_documents")

import json

from src.workflows.context_loading import EVENT_BRIEFING_CONTEXT_STAGES
from src.workflows.types import WorkflowDefinition, WorkflowStepDefinition


MEETING_PREP_WORKFLOW = WorkflowDefinition(
    workflow_type="meeting_prep",
    entry_step="load_company_state",
    steps=[
        WorkflowStepDefinition(
            name="load_company_state",
            next_steps=["load_preferences"],
            metadata={"context_stage": "load_company_state"},
        ),
        WorkflowStepDefinition(
            name="load_preferences",
            next_steps=["load_conversation_thread"],
            metadata={"context_stage": "load_preferences"},
        ),
        WorkflowStepDefinition(
            name="load_conversation_thread",
            next_steps=["load_situational_profile"],
            metadata={"context_stage": "load_conversation_thread"},
        ),
        WorkflowStepDefinition(
            name="load_situational_profile",
            next_steps=["load_session_history"],
            metadata={"context_stage": "load_situational_profile"},
        ),
        WorkflowStepDefinition(
            name="load_session_history",
            next_steps=["load_signals"],
            metadata={"context_stage": "load_session_history"},
        ),
        WorkflowStepDefinition(
            name="load_signals",
            next_steps=["retrieve_documents"],
            metadata={"context_stage": "load_signals"},
        ),
        WorkflowStepDefinition(
            name="retrieve_documents",
            next_steps=["prepare_context"],
            metadata={"context_stage": "retrieve_documents"},
        ),
        WorkflowStepDefinition(
            name="prepare_context",
            next_steps=["gather_calendar"],
            metadata={"context_stage": "prepare_context"},
        ),
        # Calendar runs first so attendee emails are available when gather_email filters threads
        WorkflowStepDefinition(
            name="gather_calendar",
            agent_name="planner_agent",
            next_steps=["gather_email"],
            metadata={"planner_stage": "gather_calendar"},
        ),
        WorkflowStepDefinition(
            name="gather_email",
            agent_name="planner_agent",
            next_steps=["gather_documents"],
            metadata={"planner_stage": "gather_email"},
        ),
        WorkflowStepDefinition(
            name="gather_documents",
            agent_name="planner_agent",
            next_steps=["build_candidates"],
            metadata={"planner_stage": "gather_documents"},
        ),
        WorkflowStepDefinition(
            name="build_candidates",
            agent_name="planner_agent",
            next_steps=["place_schedule"],
            metadata={"planner_stage": "build_candidates"},
        ),
        WorkflowStepDefinition(
            name="place_schedule",
            agent_name="planner_agent",
            next_steps=["synthesize_response"],
            metadata={"planner_stage": "place_schedule"},
        ),
        WorkflowStepDefinition(
            name="synthesize_response",
            agent_name="planner_agent",
            next_steps=["synthesizer"],
            metadata={"planner_stage": "synthesize_response"},
        ),
        WorkflowStepDefinition(
            name="synthesizer",
            agent_name="briefing_agent",
            next_steps=["complete"],
            retry_limit=1,
            retry_backoff_seconds=1,
            failure_step="failed",
            artifact_name="executive_summary.md",
        ),
        WorkflowStepDefinition(name="complete"),
        WorkflowStepDefinition(name="failed"),
    ],
    terminal_steps=["complete", "failed"],
    supports_retry=True,
    metadata={
        "response_type": "brief",
        "presentation_mode": "brief",
        "presentation_variant": "meeting_prep",
        "context_stages": json.dumps(EVENT_BRIEFING_CONTEXT_STAGES),
        "execution_model": "carrier_workflow_with_planner_execution",
        "failure_title": "Meeting Prep Failed",
        "failure_summary": "The system could not prepare meeting context from your current calendar and documents.",
    },
)

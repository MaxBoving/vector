import json

from src.workflows.types import WorkflowDefinition, WorkflowStepDefinition, WorkflowType
from src.workflows.context_loading import (
    REPORT_GENERATION_CONTEXT_STAGE_DEFINITIONS,
    REPORT_GENERATION_CONTEXT_STAGES,
    serialize_context_stage_definitions,
)


REPORT_GENERATION_WORKFLOW = WorkflowDefinition(
    workflow_type=WorkflowType.REPORT_GENERATION,
    entry_step="planning",
    steps=[
        WorkflowStepDefinition(name="planning", agent_name="router_agent", next_steps=["load_company_state"], retry_limit=0),
        WorkflowStepDefinition(
            name="load_company_state",
            next_steps=["load_company_identity"],
            metadata={"context_stage": "load_company_state"},
        ),
        WorkflowStepDefinition(
            name="load_company_identity",
            next_steps=["load_preferences"],
            metadata={"context_stage": "load_company_identity"},
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
            next_steps=["load_project_context"],
            metadata={"context_stage": "load_situational_profile"},
        ),
        WorkflowStepDefinition(
            name="load_project_context",
            next_steps=["load_session_history"],
            metadata={"context_stage": "load_project_context"},
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
            next_steps=["load_memories"],
            metadata={"context_stage": "retrieve_documents"},
        ),
        WorkflowStepDefinition(
            name="load_memories",
            next_steps=["prepare_context"],
            metadata={"context_stage": "load_memories"},
        ),
        WorkflowStepDefinition(
            name="prepare_context",
            next_steps=["synthesizer"],
            metadata={"context_stage": "prepare_context"},
        ),
        WorkflowStepDefinition(
            name="synthesizer",
            agent_name="report_agent",
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
        "response_type": "report",
        "presentation_mode": "report",
        "context_stages": json.dumps(REPORT_GENERATION_CONTEXT_STAGES),
        "context_stage_definitions": serialize_context_stage_definitions(REPORT_GENERATION_CONTEXT_STAGE_DEFINITIONS),
        "failure_title": "Report Generation Failed",
        "failure_summary": "The system could not complete this executive report.",
    },
)


class ReportGenerationWorkflow:
    definition = REPORT_GENERATION_WORKFLOW

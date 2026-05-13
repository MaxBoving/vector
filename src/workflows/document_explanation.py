import json

from src.workflows.types import WorkflowDefinition, WorkflowStepDefinition, WorkflowType
from src.workflows.context_loading import (
    DOCUMENT_EXPLANATION_CONTEXT_STAGE_DEFINITIONS,
    DOCUMENT_EXPLANATION_CONTEXT_STAGES,
    serialize_context_stage_definitions,
)


DOCUMENT_EXPLANATION_WORKFLOW = WorkflowDefinition(
    workflow_type=WorkflowType.DOCUMENT_EXPLANATION,
    entry_step="planning",
    steps=[
        WorkflowStepDefinition(name="planning", agent_name="router_agent", next_steps=["load_company_state"], retry_limit=0),
        WorkflowStepDefinition(
            name="load_company_state",
            next_steps=["load_preferences"],
            metadata={"context_stage": "load_company_state"},
        ),
        WorkflowStepDefinition(
            name="load_preferences",
            next_steps=["load_project_context"],
            metadata={"context_stage": "load_preferences"},
        ),
        WorkflowStepDefinition(
            name="load_project_context",
            next_steps=["retrieve_documents"],
            metadata={"context_stage": "load_project_context"},
        ),
        WorkflowStepDefinition(
            name="retrieve_documents",
            next_steps=["prepare_context"],
            metadata={"context_stage": "retrieve_documents"},
        ),
        WorkflowStepDefinition(
            name="prepare_context",
            next_steps=["synthesizer"],
            metadata={"context_stage": "prepare_context"},
        ),
        WorkflowStepDefinition(
            name="synthesizer",
            agent_name="explainer_agent",
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
        "response_type": "explanation",
        "presentation_mode": "report",
        "presentation_variant": "document",
        "context_stages": json.dumps(DOCUMENT_EXPLANATION_CONTEXT_STAGES),
        "context_stage_definitions": serialize_context_stage_definitions(DOCUMENT_EXPLANATION_CONTEXT_STAGE_DEFINITIONS),
        "failure_title": "Document Explanation Failed",
        "failure_summary": "The system could not complete this explanation.",
    },
)


class DocumentExplanationWorkflow:
    definition = DOCUMENT_EXPLANATION_WORKFLOW

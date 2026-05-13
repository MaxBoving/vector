from src.workflows.types import WorkflowDefinition, WorkflowStepDefinition, WorkflowType

CONVERSATIONAL_WORKFLOW = WorkflowDefinition(
    workflow_type=WorkflowType.CONVERSATIONAL,
    entry_step="synthesizer",
    steps=[
        WorkflowStepDefinition(
            name="synthesizer",
            agent_name="conversational_agent",
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
        "response_type": "conversational",
        "presentation_mode": "conversational",
        "presentation_variant": "plain",
        "failure_title": "Could not respond",
        "failure_summary": "The assistant could not complete this response.",
    },
)

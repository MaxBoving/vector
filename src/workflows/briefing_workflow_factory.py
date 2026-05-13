import json

from src.workflows.context_loading import EVENT_BRIEFING_CONTEXT_STAGES
from src.workflows.types import WorkflowDefinition, WorkflowStepDefinition


def build_briefing_workflow(
    *,
    workflow_type: str,
    failure_title: str,
    failure_summary: str,
    response_type: str = "brief",
    presentation_mode: str = "brief",
    presentation_variant: str | None = None,
    include_session_history: bool = True,
    include_signals: bool = True,
    include_live_connector: bool = True,
) -> WorkflowDefinition:
    steps: list[WorkflowStepDefinition] = [
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
            next_steps=["load_session_history" if include_session_history else ("load_signals" if include_signals else "retrieve_documents")],
            metadata={"context_stage": "load_situational_profile"},
        ),
    ]

    if include_session_history:
        steps.extend(
            [
                WorkflowStepDefinition(
                    name="load_session_history",
                    next_steps=["load_signals" if include_signals else "retrieve_documents"],
                    metadata={"context_stage": "load_session_history"},
                ),
            ]
        )

    if include_signals:
        steps.append(
            WorkflowStepDefinition(
                name="load_signals",
                next_steps=["retrieve_documents"],
                metadata={"context_stage": "load_signals"},
            )
        )

    if include_live_connector:
        steps.append(
            WorkflowStepDefinition(
                name="load_live_connector",
                next_steps=["retrieve_documents"],
                metadata={"context_stage": "load_live_connector"},
            )
        )

    steps.extend(
        [
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
                agent_name="briefing_agent",
                next_steps=["complete"],
                retry_limit=1,
                retry_backoff_seconds=1,
                failure_step="failed",
                artifact_name="executive_summary.md",
            ),
            WorkflowStepDefinition(name="complete"),
            WorkflowStepDefinition(name="failed"),
        ]
    )

    return WorkflowDefinition(
        workflow_type=workflow_type,
        entry_step="load_company_state",
        steps=steps,
        terminal_steps=["complete", "failed"],
        supports_retry=True,
        metadata={
            "response_type": response_type,
            "presentation_mode": presentation_mode,
            "presentation_variant": presentation_variant,
            "context_stages": json.dumps(EVENT_BRIEFING_CONTEXT_STAGES),
            "failure_title": failure_title,
            "failure_summary": failure_summary,
        },
    )

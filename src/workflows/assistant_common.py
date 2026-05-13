from datetime import datetime
from typing import Optional

from sqlmodel import Session

from src.api.schemas import ArtifactRef, AssistantMessageResponse
from src.core.database import engine
from src.core.models import SessionInteraction, User
from src.runtime.state import StageStatus, WorkflowStageState, WorkflowState, WorkflowStatus
from src.workflows.interaction_persistence import serialize_interaction_response


def build_assistant_workflow_state(
    interaction: SessionInteraction,
    current_user: User,
    workflow_type: str,
    stage_names: Optional[list[str]] = None,
) -> WorkflowState:
    stages = [
        WorkflowStageState(
            name=stage_name,
            status=StageStatus.RUNNING if index == 0 else StageStatus.NOT_STARTED,
        )
        for index, stage_name in enumerate(stage_names or [])
    ]
    return WorkflowState(
        workflow_id=f"assistant:{workflow_type}:{current_user.ceo_id}:{interaction.id}",
        workflow_type=workflow_type,
        interaction_id=interaction.id,
        ceo_id=current_user.ceo_id,
        company_name=current_user.company_name,
        status=WorkflowStatus.RUNNING,
        current_stage="planning",
        metadata={"query": interaction.query},
        stages=stages,
    )


def persist_assistant_response(
    interaction_id: int,
    response: AssistantMessageResponse,
    intent: Optional[str] = None,
) -> None:
    with Session(engine) as session:
        stored_interaction = session.get(SessionInteraction, interaction_id)
        if not stored_interaction:
            return

        stored_interaction.status = "COMPLETED" if response.status == "completed" else "FAILED"
        stored_interaction.current_stage = "complete"
        stored_interaction.response = serialize_interaction_response(response)
        stored_interaction.intent = intent
        stored_interaction.last_updated = datetime.now().isoformat()
        session.add(stored_interaction)
        session.commit()


def artifact_ref_for_stage(
    interaction_id: int,
    stage: str,
    *,
    label: Optional[str] = None,
    format: Optional[str] = None,
    status: Optional[str] = None,
    metadata: Optional[dict[str, object]] = None,
) -> ArtifactRef:
    return ArtifactRef(
        artifact_type=stage,
        artifact_id=f"interaction:{interaction_id}:{stage}",
        label=label or stage.replace("_", " ").title(),
        format=format,
        status=status,
        metadata=metadata or {},
    )

import json
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.core.models import SessionInteraction
from src.tools.artifact_tools import (
    STAGE_ARTIFACT_FILENAMES,
    STAGE_SEQUENCE,
    hydrate_stage_artifact_refs,
    hydrate_stage_artifacts,
)
from src.workflows.types import WorkflowType


class WorkflowStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AWAITING_INPUT = "AWAITING_INPUT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StageStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


class GateState(BaseModel):
    gate_type: str
    reason: Optional[str] = None
    options: List[Dict[str, Any]] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)
    triggered_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    resolved_at: Optional[str] = None
    resolution: Optional[str] = None


class WorkflowStageState(BaseModel):
    name: str
    status: StageStatus = StageStatus.NOT_STARTED
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    artifact_key: Optional[str] = None
    output_ref: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkflowState(BaseModel):
    workflow_id: str
    workflow_type: str
    interaction_id: Optional[int] = None
    ceo_id: str
    company_name: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    current_stage: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    routing_decision: Optional[Dict[str, Any]] = None
    retries: Dict[str, int] = Field(default_factory=dict)
    gate: Optional[GateState] = None
    stage_outputs: Dict[str, Any] = Field(default_factory=dict)
    artifacts: Dict[str, str] = Field(default_factory=dict)
    stages: List[WorkflowStageState] = Field(default_factory=list)
    agent_events: List[Dict[str, Any]] = Field(default_factory=list)
    tool_events: List[Dict[str, Any]] = Field(default_factory=list)
    final_response: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


def hydrate_workflow_state(
    interaction: SessionInteraction,
    company_name: str,
    workflow_type: str = WorkflowType.REPORT_GENERATION,
    include_artifacts: bool = True,
    last_stage: Optional[str] = None,
) -> WorkflowState:
    artifacts = {}
    artifact_refs = {}
    if interaction.id is not None and include_artifacts:
        artifacts = hydrate_stage_artifacts(interaction.id, interaction.ceo_id, last_stage=last_stage)
        artifact_refs = hydrate_stage_artifact_refs(interaction.id, interaction.ceo_id)

    gate = _hydrate_gate_state(interaction)
    stages = _hydrate_stage_states(
        current_stage=interaction.current_stage,
        interaction_status=interaction.status,
        artifacts=artifacts,
        artifact_refs=artifact_refs,
    )

    routing_decision = {"intent": interaction.intent} if interaction.intent else None

    return WorkflowState(
        workflow_id=_workflow_id_for_interaction(interaction),
        workflow_type=workflow_type,
        interaction_id=interaction.id,
        ceo_id=interaction.ceo_id,
        company_name=company_name,
        status=_coerce_workflow_status(interaction.status),
        current_stage=interaction.current_stage,
        created_at=interaction.timestamp,
        updated_at=interaction.last_updated,
        routing_decision=routing_decision,
        gate=gate,
        stage_outputs=artifacts,
        artifacts=artifact_refs,
        stages=stages,
        final_response=interaction.response,
        metadata={"query": interaction.query},
    )


def _workflow_id_for_interaction(interaction: SessionInteraction) -> str:
    interaction_id = interaction.id if interaction.id is not None else "unknown"
    return f"strategic_inquiry:{interaction.ceo_id}:{interaction_id}"


def _coerce_workflow_status(status: str) -> WorkflowStatus:
    try:
        return WorkflowStatus(status)
    except ValueError:
        return WorkflowStatus.PENDING


def _hydrate_gate_state(interaction: SessionInteraction) -> Optional[GateState]:
    if not interaction.gate_type:
        return None

    context = _parse_missing_data_context(interaction.missing_data_context)
    return GateState(
        gate_type=interaction.gate_type,
        reason=context.get("reason"),
        options=context.get("options", []),
        context=context,
    )


def _parse_missing_data_context(raw_context: Optional[str]) -> Dict[str, Any]:
    if not raw_context:
        return {}
    if isinstance(raw_context, dict):
        return raw_context
    try:
        parsed = json.loads(raw_context)
        return parsed if isinstance(parsed, dict) else {"raw": parsed}
    except (json.JSONDecodeError, TypeError):
        return {"raw": raw_context}


def _hydrate_stage_states(
    current_stage: Optional[str],
    interaction_status: str,
    artifacts: Dict[str, str],
    artifact_refs: Dict[str, str],
) -> List[WorkflowStageState]:
    current_idx = STAGE_SEQUENCE.index(current_stage) if current_stage in STAGE_SEQUENCE else -1
    workflow_status = _coerce_workflow_status(interaction_status)
    stages: List[WorkflowStageState] = []

    for idx, stage_name in enumerate(STAGE_SEQUENCE):
        status = StageStatus.NOT_STARTED

        if stage_name in artifacts:
            status = StageStatus.COMPLETED
        elif idx < current_idx:
            status = StageStatus.COMPLETED
        elif stage_name == current_stage:
            if workflow_status == WorkflowStatus.FAILED:
                status = StageStatus.FAILED
            elif workflow_status == WorkflowStatus.AWAITING_INPUT:
                status = StageStatus.BLOCKED
            elif workflow_status in (WorkflowStatus.PENDING, WorkflowStatus.RUNNING):
                status = StageStatus.RUNNING
            elif workflow_status == WorkflowStatus.COMPLETED:
                status = StageStatus.COMPLETED
        elif current_idx == -1 and stage_name == "planning" and workflow_status in (
            WorkflowStatus.PENDING,
            WorkflowStatus.RUNNING,
        ):
            status = StageStatus.RUNNING

        stages.append(
            WorkflowStageState(
                name=stage_name,
                status=status,
                artifact_key=STAGE_ARTIFACT_FILENAMES.get(stage_name),
                output_ref=artifact_refs.get(stage_name),
            )
        )

    return stages

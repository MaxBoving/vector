from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.runtime.state import WorkflowState


class TaskIntent(str, Enum):
    STRATEGIC_ANALYSIS = "strategic_analysis"
    LIVE_RESEARCH = "live_research"
    DOCUMENT_REVIEW = "document_review"
    EXECUTION_REQUEST = "execution_request"
    FACT_FINDING = "fact_finding"
    QUANT_ANALYSIS = "quant_analysis"
    EXECUTION_DRAFT = "execution_draft"


class ActionType(str, Enum):
    CALL_TOOL = "call_tool"
    UPDATE_STATE = "update_state"
    WRITE_ARTIFACT = "write_artifact"
    EMIT_GATE = "emit_gate"
    COMPLETE_STAGE = "complete_stage"
    COMPLETE_WORKFLOW = "complete_workflow"
    FAIL_STAGE = "fail_stage"
    NOOP = "noop"


class AgentMetadata(BaseModel):
    name: str
    description: str
    version: str = "1.0"
    stage: Optional[str] = None
    allowed_tools: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class RoutingDecision(BaseModel):
    intent: TaskIntent
    specialist_required: str
    relevant_state_keys: List[str]
    requires_approval: bool = False
    rationale: str


class AuditResult(BaseModel):
    passed: bool
    report: str
    discrepancies: List[str]


class AgentAction(BaseModel):
    action_type: ActionType
    target: Optional[str] = None
    args: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class AgentInput(BaseModel):
    workflow_state: WorkflowState
    stage: str
    task_input: Optional[str] = None
    prompt: Optional[str] = None
    system_prompt: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    traits: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    agent_name: str
    stage: str
    success: bool = True
    summary: Optional[str] = None
    content: Optional[str] = None
    structured_output: Dict[str, Any] = Field(default_factory=dict)
    actions: List[AgentAction] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ArtifactWriteMetadata(BaseModel):
    source: Optional[str] = None
    label: Optional[str] = None
    format: Optional[str] = None
    status: Optional[str] = None
    hidden: bool = False


class ArtifactWriteActionArgs(BaseModel):
    stage: str
    filename: str
    content: str
    metadata: ArtifactWriteMetadata


class GeneratedArtifactToolActionArgs(BaseModel):
    artifact_stage: str
    filename: str
    label: str
    format: str
    preview_stage: Optional[str] = None
    preview_filename: Optional[str] = None


def tool_action(tool_name: str, **kwargs: Any) -> AgentAction:
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        target=tool_name,
        args=kwargs,
        description=f"Invoke tool: {tool_name}",
    )


def update_state_action(**kwargs: Any) -> AgentAction:
    return AgentAction(
        action_type=ActionType.UPDATE_STATE,
        target="workflow_state",
        args=kwargs,
        description="Update workflow state",
    )


def write_artifact_action(stage: str, filename: str, content: str, **metadata: Any) -> AgentAction:
    action_args = ArtifactWriteActionArgs(
        stage=stage,
        filename=filename,
        content=content,
        metadata=ArtifactWriteMetadata(**metadata),
    )
    return AgentAction(
        action_type=ActionType.WRITE_ARTIFACT,
        target=stage,
        args=action_args.model_dump(),
        description=f"Write artifact for stage: {stage}",
    )


def generated_artifact_tool_action(
    tool_name: str,
    *,
    artifact_stage: str,
    filename: str,
    label: str,
    format: str,
    preview_stage: Optional[str] = None,
    preview_filename: Optional[str] = None,
    **tool_kwargs: Any,
) -> AgentAction:
    action_args = GeneratedArtifactToolActionArgs(
        artifact_stage=artifact_stage,
        filename=filename,
        label=label,
        format=format,
        preview_stage=preview_stage,
        preview_filename=preview_filename,
    )
    serialized_tool_kwargs = {
        key: value.model_dump() if isinstance(value, BaseModel) else value
        for key, value in tool_kwargs.items()
    }
    return AgentAction(
        action_type=ActionType.CALL_TOOL,
        target=tool_name,
        args={
            **action_args.model_dump(exclude_none=True),
            **serialized_tool_kwargs,
        },
        description=f"Invoke tool: {tool_name}",
    )


def create_canvas_action(
    *,
    artifact_stage: str,
    filename: str,
    label: str,
    canvas_spec: Any,
    preview_stage: Optional[str] = None,
    preview_filename: Optional[str] = None,
) -> AgentAction:
    return generated_artifact_tool_action(
        "create_canvas",
        artifact_stage=artifact_stage,
        filename=filename,
        label=label,
        format="html",
        preview_stage=preview_stage,
        preview_filename=preview_filename,
        canvas_spec=canvas_spec,
    )


def create_docx_memo_action(
    *,
    artifact_stage: str,
    filename: str,
    label: str,
    memo_spec: Any,
    preview_stage: Optional[str] = None,
    preview_filename: Optional[str] = None,
) -> AgentAction:
    return generated_artifact_tool_action(
        "create_docx_memo",
        artifact_stage=artifact_stage,
        filename=filename,
        label=label,
        format="docx",
        preview_stage=preview_stage,
        preview_filename=preview_filename,
        memo_spec=memo_spec,
    )


def create_pptx_deck_action(
    *,
    artifact_stage: str,
    filename: str,
    label: str,
    deck_spec: Any,
    preview_stage: Optional[str] = None,
    preview_filename: Optional[str] = None,
) -> AgentAction:
    return generated_artifact_tool_action(
        "create_pptx_deck",
        artifact_stage=artifact_stage,
        filename=filename,
        label=label,
        format="pptx",
        preview_stage=preview_stage,
        preview_filename=preview_filename,
        deck_spec=deck_spec,
    )


def create_workbook_action(
    *,
    artifact_stage: str,
    filename: str,
    label: str,
    workbook_spec: Any,
    preview_stage: Optional[str] = None,
    preview_filename: Optional[str] = None,
) -> AgentAction:
    return generated_artifact_tool_action(
        "create_workbook",
        artifact_stage=artifact_stage,
        filename=filename,
        label=label,
        format="xlsx",
        preview_stage=preview_stage,
        preview_filename=preview_filename,
        workbook_spec=workbook_spec,
    )


def gate_action(gate_type: str, **context: Any) -> AgentAction:
    return AgentAction(
        action_type=ActionType.EMIT_GATE,
        target=gate_type,
        args=context,
        description=f"Emit gate: {gate_type}",
    )


def complete_stage_action(stage: str, **kwargs: Any) -> AgentAction:
    return AgentAction(
        action_type=ActionType.COMPLETE_STAGE,
        target=stage,
        args=kwargs,
        description=f"Complete stage: {stage}",
    )


def complete_workflow_action(**kwargs: Any) -> AgentAction:
    return AgentAction(
        action_type=ActionType.COMPLETE_WORKFLOW,
        target="workflow",
        args=kwargs,
        description="Complete workflow",
    )

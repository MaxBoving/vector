from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class WorkflowEventType(str, Enum):
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_RESUMED = "workflow_resumed"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    GATE_TRIGGERED = "gate_triggered"
    GATE_RESOLVED = "gate_resolved"
    AGENT_INVOKED = "agent_invoked"
    AGENT_COMPLETED = "agent_completed"
    TOOL_INVOKED = "tool_invoked"
    TOOL_COMPLETED = "tool_completed"


class WorkflowEvent(BaseModel):
    event_type: WorkflowEventType
    workflow_id: str
    interaction_id: Optional[int] = None
    stage: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    payload: Dict[str, Any] = Field(default_factory=dict)


class AgentEvent(BaseModel):
    workflow_id: str
    agent_name: str
    stage: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    status: str = "completed"
    action: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class ToolEvent(BaseModel):
    workflow_id: str
    tool_name: str
    stage: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    status: str = "completed"
    payload: Dict[str, Any] = Field(default_factory=dict)

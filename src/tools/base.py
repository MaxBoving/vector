from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ToolContext(BaseModel):
    workflow_id: Optional[str] = None
    interaction_id: Optional[int] = None
    ceo_id: Optional[str] = None
    company_name: Optional[str] = None
    conversation_id: Optional[str] = None
    stage: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolMetadata(BaseModel):
    name: str
    description: str
    read_only: bool = False
    side_effects: bool = False
    version: str = "1.0"
    tags: list[str] = Field(default_factory=list)


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    data: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseTool(ABC):
    metadata: ToolMetadata

    @abstractmethod
    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        """Run the deterministic tool implementation."""


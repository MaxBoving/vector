"""Assistant-runtime tool adapters and registry."""

from .artifact_tools import ListArtifactsTool, ReadArtifactTool, WriteArtifactTool
from .base import BaseTool, ToolContext, ToolMetadata, ToolResult
from .document_tools import (
    CreateDocxMemoTool,
    CreatePptxDeckTool,
    CreateWorkbookTool,
    ExtractPdfTool,
    UpdateWorkbookTool,
)
from .execution_tools import ExecuteMathTool, ScanFileTool, TagDocumentTool
from .knowledge_tools import SemanticSearchTool
from .llm_tools import StructuredCompletionTool
from .registry import ToolNotFoundError, ToolRegistry, build_default_tool_registry
from .state_tools import (
    GetCompanyStateTool,
    GetPreferencesTool,
    GetRecentSignalsTool,
    GetSessionHistoryTool,
    GetUnreadSignalsTool,
    SaveIncomingSignalTool,
)

__all__ = [
    "BaseTool",
    "CreateDocxMemoTool",
    "CreatePptxDeckTool",
    "CreateWorkbookTool",
    "ExecuteMathTool",
    "ExtractPdfTool",
    "GetCompanyStateTool",
    "GetPreferencesTool",
    "GetRecentSignalsTool",
    "GetSessionHistoryTool",
    "GetUnreadSignalsTool",
    "SaveIncomingSignalTool",
    "ListArtifactsTool",
    "ReadArtifactTool",
    "ScanFileTool",
    "SemanticSearchTool",
    "StructuredCompletionTool",
    "TagDocumentTool",
    "ToolContext",
    "ToolMetadata",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "UpdateWorkbookTool",
    "WriteArtifactTool",
    "build_default_tool_registry",
]

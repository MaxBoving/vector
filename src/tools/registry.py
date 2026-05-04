from typing import Any, Dict, Iterable, Optional

from .base import BaseTool, ToolContext, ToolResult


class ToolNotFoundError(KeyError):
    """Raised when a requested tool is not registered."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool, *, overwrite: bool = False) -> None:
        name = tool.metadata.name
        if name in self._tools and not overwrite:
            raise ValueError(f"Tool already registered: {name}")
        self._tools[name] = tool

    def register_many(self, tools: Iterable[BaseTool], *, overwrite: bool = False) -> None:
        for tool in tools:
            self.register(tool, overwrite=overwrite)

    def get(self, name: str) -> BaseTool:
        tool = self._tools.get(name)
        if not tool:
            raise ToolNotFoundError(name)
        return tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def describe(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: tool.metadata.model_dump()
            for name, tool in sorted(self._tools.items(), key=lambda item: item[0])
        }

    def invoke(self, name: str, context: Optional[ToolContext] = None, **kwargs: Any) -> ToolResult:
        tool = self.get(name)
        tool_context = context or ToolContext()
        return tool.invoke(tool_context, **kwargs)


def build_default_tool_registry() -> ToolRegistry:
    from .artifact_tools import ListArtifactsTool, ReadArtifactTool, WriteArtifactTool
    from .connector_tools import ConnectorStatusTool, ReadCalendarEventsTool, ReadEmailThreadsTool, SendEmailDraftTool
    from .coauthoring_tool import CoauthoringTool
    from .document_tools import (
        CreateCanvasTool,
        CreateDocxMemoTool,
        CreatePptxDeckTool,
        CreateWorkbookTool,
        ExtractPdfTool,
        UpdateWorkbookTool,
    )
    from .crm_tools import CRMDealContextTool
    from .drive_tools import GoogleDriveReadTool, GoogleDriveSearchTool
    from .entity_tools import GetEntityContextTool
    from .execution_tools import ExecuteMathTool, ScanFileTool, TagDocumentTool
    from .finance_tools import VarianceAnalysisTool
    from .knowledge_tools import SemanticSearchTool
    from .llm_tools import StructuredCompletionTool
    from .memory_tools import MemoryManagementTool
    from .situational_tools import GetSituationalProfileTool, UpdateSituationalProfileTool
    from .slack_tools import SlackPostTool, SlackReadTool
    from .thread_tools import GetLiveContextTool, GetThreadEntriesTool, ResolveThreadEntryTool, WriteThreadEntryTool
    from .webhook_tools import WebhookSignalIngesterTool
    from .state_tools import (
        GetCompanyIdentityProfileTool,
        GetCompanyStateTool,
        GetPreferencesTool,
        GetProjectContextTool,
        GetRecentSignalsTool,
        GetSessionHistoryTool,
        GetUnreadSignalsTool,
        SaveIncomingSignalTool,
    )

    registry = ToolRegistry()
    registry.register_many(
        [
            # State
            GetCompanyStateTool(),
            GetCompanyIdentityProfileTool(),
            GetPreferencesTool(),
            GetProjectContextTool(),
            GetSessionHistoryTool(),
            GetUnreadSignalsTool(),
            GetRecentSignalsTool(),
            SaveIncomingSignalTool(),
            GetLiveContextTool(),
            GetThreadEntriesTool(),
            GetSituationalProfileTool(),
            # Knowledge
            SemanticSearchTool(),
            # Artifacts
            ReadArtifactTool(),
            WriteArtifactTool(),
            ListArtifactsTool(),
            # Documents
            ExtractPdfTool(),
            CreateWorkbookTool(),
            UpdateWorkbookTool(),
            CreateDocxMemoTool(),
            CreatePptxDeckTool(),
            CreateCanvasTool(),
            # Finance
            VarianceAnalysisTool(),
            # Execution
            ExecuteMathTool(),
            ScanFileTool(),
            TagDocumentTool(),
            # LLM
            StructuredCompletionTool(),
            # Coauthoring
            CoauthoringTool(),
            # Connectors — email & calendar
            ConnectorStatusTool(),
            ReadEmailThreadsTool(),
            ReadCalendarEventsTool(),
            SendEmailDraftTool(),
            # Connectors — Google Drive
            GoogleDriveSearchTool(),
            GoogleDriveReadTool(),
            # Connectors — Slack
            SlackReadTool(),
            SlackPostTool(),
            # Connectors — CRM
            CRMDealContextTool(),
            # Webhooks
            WebhookSignalIngesterTool(),
            # Memory + Entity context
            MemoryManagementTool(),
            GetEntityContextTool(),
            # Conversation thread / situational state
            WriteThreadEntryTool(),
            ResolveThreadEntryTool(),
            UpdateSituationalProfileTool(),
        ]
    )
    return registry

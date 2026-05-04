"""
AssistantWorkflowRunner — narrow compatibility adapter.

All orchestration logic (classify → select workflow → run → persist) now lives
in src/assistant/service.py::AssistantService.

This module is kept for backward-compat:
  - AssistantWorkflowRunner is imported by src/api/main.py and tests.
  - ResolvedAssistantRequest is imported by AssistantService.
  - detect_artifact_type_from_request is imported by tests/test_workflow_mode_eval.py.
  - _WORKFLOW_REGISTRY is imported by AssistantService._resolve_workflow_definition.

AssistantWorkflowRunner.run() delegates directly to AssistantService.handle().
"""
from dataclasses import dataclass
from typing import Any

from src.api.schemas import AssistantMessageResponse, AssistantQueryRequest
from src.core.models import SessionInteraction, User
from src.runtime.engine import RuntimeEngine
from src.tools.registry import ToolRegistry, build_default_tool_registry
from src.workflows.calendar_action import CALENDAR_ACTION_WORKFLOW
from src.workflows.conversational import CONVERSATIONAL_WORKFLOW
from src.workflows.calendar_briefing import CALENDAR_BRIEFING_WORKFLOW
from src.workflows.document_explanation import DOCUMENT_EXPLANATION_WORKFLOW
from src.workflows.email_action import EMAIL_ACTION_WORKFLOW
from src.workflows.email_ingestion import EMAIL_INGESTION_WORKFLOW
from src.workflows.email_watcher import EMAIL_WATCHER_WORKFLOW
from src.workflows.llm_router import LLMRouter
from src.workflows.meeting_prep import MEETING_PREP_WORKFLOW
from src.workflows.morning_brief import MORNING_BRIEF_WORKFLOW
from src.workflows.report_generation import REPORT_GENERATION_WORKFLOW
from src.workflows.schedule_planning import SCHEDULE_PLANNING_WORKFLOW
from src.workflows.types import WorkflowType
from src.workflows.watch_context import WatchContextAssembler
from src.workflows.weekly_recap import WEEKLY_RECAP_WORKFLOW
from src.agents.schemas import RoutingDecision

# Re-export for backward-compat: tests import these directly from this module.
from src.assistant.artifact_mode import detect_artifact_type_from_request  # noqa: F401

__all__ = [
    "AssistantWorkflowRunner",
    "ResolvedAssistantRequest",
    "detect_artifact_type_from_request",
    "_WORKFLOW_REGISTRY",
]

# ---------------------------------------------------------------------------
# Workflow registry — imported by AssistantService._resolve_workflow_definition.
# ---------------------------------------------------------------------------

_WORKFLOW_REGISTRY: dict[str, object] = {
    WorkflowType.CONVERSATIONAL: CONVERSATIONAL_WORKFLOW,
    WorkflowType.DOCUMENT_EXPLANATION: DOCUMENT_EXPLANATION_WORKFLOW,
    WorkflowType.EMAIL_WATCHER: EMAIL_WATCHER_WORKFLOW,
    WorkflowType.EMAIL_INGESTION: EMAIL_INGESTION_WORKFLOW,
    WorkflowType.EMAIL_ACTION: EMAIL_ACTION_WORKFLOW,
    WorkflowType.CALENDAR_BRIEFING: CALENDAR_BRIEFING_WORKFLOW,
    WorkflowType.CALENDAR_ACTION: CALENDAR_ACTION_WORKFLOW,
    WorkflowType.MORNING_BRIEF: MORNING_BRIEF_WORKFLOW,
    WorkflowType.SCHEDULE_PLANNING: SCHEDULE_PLANNING_WORKFLOW,
    # Backward-compat aliases for existing persisted workflow_type values
    WorkflowType.DAY_SCHEDULE_PLANNING: SCHEDULE_PLANNING_WORKFLOW,
    WorkflowType.WEEK_SCHEDULE_PLANNING: SCHEDULE_PLANNING_WORKFLOW,
    WorkflowType.MEETING_PREP: MEETING_PREP_WORKFLOW,
    WorkflowType.WEEKLY_RECAP: WEEKLY_RECAP_WORKFLOW,
    WorkflowType.REPORT_GENERATION: REPORT_GENERATION_WORKFLOW,
}


@dataclass(frozen=True)
class ResolvedAssistantRequest:
    """Intermediate value object used by AssistantService._resolve_request."""
    workflow_type: str
    definition: object
    routing_decision: RoutingDecision
    extra_metadata: dict


class AssistantWorkflowRunner:
    """
    Infrastructure holder.  Owns runtime engine, tool registry, router agents.

    All pipeline orchestration is delegated to AssistantService.handle().
    This class is kept as a compatibility shim so existing callers
    (src/api/main.py, tests) continue to work without changes.
    """

    def __init__(
        self,
        tools: ToolRegistry | None = None,
        assembler: WatchContextAssembler | None = None,
    ) -> None:
        self.tools = tools or build_default_tool_registry()
        self.runtime = RuntimeEngine(self.tools)
        self.assembler = assembler or WatchContextAssembler()
        self.llm_router = LLMRouter()

    async def run(
        self,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        current_user: User,
    ) -> AssistantMessageResponse:
        """Delegate to AssistantService — the canonical orchestration entry point."""
        from src.assistant.service import AssistantService
        return await AssistantService(self).handle(
            payload=payload,
            interaction=interaction,
            current_user=current_user,
        )

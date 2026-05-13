from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from src.agents import RoutingDecision
from src.api.schemas import AssistantQueryRequest
from src.core.models import User, WorkflowRun
from src.workflows.types import WorkflowStepDefinition

from .state import WorkflowState

if TYPE_CHECKING:
    from .engine import RuntimeEngine


class RuntimeStageHandlers:
    def __init__(self, engine: "RuntimeEngine") -> None:
        self.engine = engine

    async def handle_router_stage(
        self,
        *,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
        routing_decision: Optional[RoutingDecision],
    ) -> dict[str, Any]:
        if routing_decision is None:
            raise ValueError("handle_router_stage requires a pre-computed routing_decision; RouterAgent has been removed.")
        resolved_routing = routing_decision
        workflow_state.routing_decision = resolved_routing.model_dump()
        workflow_state.stage_outputs[step.name] = workflow_state.routing_decision
        return {
            "completed_without_agent_output": True,
            "routing_decision": resolved_routing,
        }

    async def handle_context_stage(
        self,
        *,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
        routing_decision: Optional[RoutingDecision],
    ) -> dict[str, Any]:
        self.engine._execute_context_stage(
            step=step,
            workflow_state=workflow_state,
            workflow_run=workflow_run,
            payload=payload,
            current_user=current_user,
        )
        return {"completed_without_agent_output": True, "routing_decision": routing_decision}

    async def handle_agent_stage(
        self,
        *,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
        routing_decision: Optional[RoutingDecision],
    ) -> dict[str, Any]:
        agent = self.engine.agents[step.agent_name]
        agent_output = await self.engine._run_agent_step(
            agent=agent,
            step=step,
            workflow_state=workflow_state,
            workflow_run=workflow_run,
            payload=payload,
            current_user=current_user,
            initial_context=self.engine._load_stage_context(workflow_state, step.name),
        )
        return {
            "completed_without_agent_output": False,
            "agent_output": agent_output,
            "routing_decision": routing_decision,
        }

    async def handle_noop_stage(
        self,
        *,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
        routing_decision: Optional[RoutingDecision],
    ) -> dict[str, Any]:
        return {"completed_without_agent_output": True, "routing_decision": routing_decision}

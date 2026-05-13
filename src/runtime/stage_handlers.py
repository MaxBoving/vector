from __future__ import annotations

from enum import Enum
from typing import Callable, Dict

from src.workflows.types import WorkflowStepDefinition


class StageFamily(str, Enum):
    ROUTER = "router"
    CONTEXT = "context"
    AGENT = "agent"
    NOOP = "noop"


StageHandler = Callable[..., object]


class StageHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: Dict[StageFamily, StageHandler] = {}

    def register(self, family: StageFamily, handler: StageHandler) -> None:
        self._handlers[family] = handler

    def handler_for(self, family: StageFamily) -> StageHandler:
        handler = self._handlers.get(family)
        if handler is None:
            raise KeyError(f"No stage handler registered for family '{family.value}'.")
        return handler

    def classify(self, step: WorkflowStepDefinition) -> StageFamily:
        if step.agent_name == "router_agent":
            return StageFamily.ROUTER
        if step.metadata.get("context_stage"):
            return StageFamily.CONTEXT
        if step.agent_name:
            return StageFamily.AGENT
        return StageFamily.NOOP

from .schemas import (
    ActionType,
    AgentAction,
    AgentInput,
    AgentMetadata,
    AgentOutput,
    AuditResult,
    RoutingDecision,
    TaskIntent,
    complete_stage_action,
    complete_workflow_action,
    gate_action,
    tool_action,
    write_artifact_action,
)
from .base import BaseAgent

_LAZY_EXPORTS = {
    "BriefingAgent": (".briefing_agent", "BriefingAgent"),
    "ConversationalAgent": (".conversational_agent", "ConversationalAgent"),
    "ExplainerAgent": (".explainer_agent", "ExplainerAgent"),
    "PlannerAgent": (".planner_agent", "PlannerAgent"),
    "ReportAgent": (".report_agent", "ReportAgent"),
}


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _LAZY_EXPORTS[name]
    from importlib import import_module

    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

__all__ = [
    "ActionType",
    "AgentAction",
    "AgentInput",
    "AgentMetadata",
    "AgentOutput",
    "AuditResult",
    "BaseAgent",
    "BriefingAgent",
    "ConversationalAgent",
    "ExplainerAgent",
    "PlannerAgent",
    "ReportAgent",
    "RoutingDecision",
    "TaskIntent",
    "complete_stage_action",
    "complete_workflow_action",
    "gate_action",
    "tool_action",
    "write_artifact_action",
]

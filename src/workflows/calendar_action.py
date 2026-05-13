"""
CALENDAR_ACTION_WORKFLOW — explicit workflow definition for calendar write requests.

Calendar write-action requests are routed to this workflow type so that:
  - They are tracked as first-class workflow runs.
  - Approval semantics (AWAITING_INPUT gate) are encoded in the definition.
  - resume_workflow can restore state from persisted routing_decision without
    re-running classification.

The actual proposal-building and gate-response construction is handled by
maybe_handle_direct_action_request / resolve_direct_action in direct_actions.py.
This definition serves as the registry entry and metadata carrier.
"""
from src.workflows.types import WorkflowDefinition, WorkflowStepDefinition, WorkflowType

CALENDAR_ACTION_WORKFLOW = WorkflowDefinition(
    workflow_type=WorkflowType.CALENDAR_ACTION,
    entry_step="propose",
    approval_required=True,
    steps=[
        WorkflowStepDefinition(
            name="propose",
            agent_name="planner_agent",
            next_steps=["write"],
            approval_required=True,
            approval_gate_type="HUMAN_APPROVAL",
        ),
        WorkflowStepDefinition(
            name="write",
            next_steps=["complete"],
        ),
        WorkflowStepDefinition(
            name="complete",
            next_steps=[],
        ),
    ],
    terminal_steps=["complete"],
    metadata={"action_channel": "calendar"},
)

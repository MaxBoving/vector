from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.workflows.planning_types import RequestPlan
from src.workflows.routing import RouteDecision, RouteFamily, RouteSubintent


class RequestIntent(BaseModel):
    """Canonical output of the request-classification boundary.

    Consolidates route_family, workflow_type, requires_approval, context_profile,
    and time_horizon into a single model.  All downstream orchestration in
    AssistantWorkflowRunner depends on this model only — not on ad-hoc combinations
    of RouteDecision and RequestPlan fields.

    Fields
    ------
    route_family        : WATCH / PLAN / ACT / REPORT
    workflow_type       : resolved primary workflow (e.g. WorkflowType.SCHEDULE_PLANNING)
    compound_workflow_chain : ordered chain for compound plans; empty for single-workflow
    requires_approval   : True only for write actions (send email, create calendar event)
    context_profile     : context sources needed (email, calendar, documents, …)
    time_horizon        : today | tomorrow | this_week | next_week | week_after_next | unspecified
    is_compound         : True when the request decomposes into multiple sub-workflows
    rationale           : one-sentence explanation from the classifier
    request_plan        : preserved for assembler / metadata use; may be None
    subintents          : fine-grained sub-intents for telemetry / routing hints
    """

    route_family: RouteFamily
    workflow_type: str
    compound_workflow_chain: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    context_profile: list[str] = Field(default_factory=list)
    time_horizon: str = "unspecified"
    is_compound: bool = False
    rationale: str = ""
    request_plan: Optional[RequestPlan] = None
    subintents: list[RouteSubintent] = Field(default_factory=list)
    # response_format from IntentClassifier: "conversational"|"report"|"document"|"draft"
    # Empty string means not classified (use workflow defaults).
    response_format: str = ""

    def to_route_decision(self) -> RouteDecision:
        """Adapter for legacy callers that still accept RouteDecision.

        Use sparingly — prefer reading RequestIntent fields directly.
        """
        workflow_chain = (
            self.compound_workflow_chain
            if self.is_compound
            else ([self.workflow_type] if self.workflow_type else [])
        )
        return RouteDecision(
            primary_intent=self.route_family,
            subintents=list(self.subintents),
            workflow_chain=workflow_chain,
            request_plan=self.request_plan,
            requires_write=(self.route_family == RouteFamily.ACT),
            requires_approval=self.requires_approval,
            rationale=self.rationale,
        )

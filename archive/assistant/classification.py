"""
Single classification boundary for request routing.

Exports one function:

  classify_request_intent_async  — async primary classifier used by
                                   AssistantWorkflowRunner._classify_route.

Classification precedence:
  1. RequestInterpretation (canonical) → authoritative semantic interpretation.
  2. If missing, build RequestInterpretation (LLM-native).

No keyword fallback routing path remains in this boundary.
"""
from __future__ import annotations

from typing import Any, Optional

from src.api.schemas import AssistantQueryRequest
from src.assistant.request_interpretation import RequestInterpretation, build_request_interpretation
from src.assistant.types import RequestIntent
from src.workflows.llm_router import LLMRouter  # kept for call-site compatibility only
from src.workflows.planning_types import RequestPlan
from src.workflows.routing import RouteFamily
from src.workflows.types import WorkflowType

_WORKFLOW_KIND_TO_ROUTE: dict[str, RouteFamily] = {
    "analysis": RouteFamily.REPORT,
    "watch": RouteFamily.WATCH,
    "plan": RouteFamily.PLAN,
    "write_proposal": RouteFamily.ACT,
}


def _interpretation_to_intent(interpretation: RequestInterpretation) -> RequestIntent:
    primary = interpretation.candidate_workflows[0].name if interpretation.candidate_workflows else WorkflowType.CONVERSATIONAL
    workflow_chain = [step.intent for step in interpretation.steps if step.intent]
    context_profile: list[str] = []
    for step in interpretation.steps:
        for req in step.requires:
            if req not in context_profile:
                context_profile.append(req)
    requires_approval = any(step.approval_required for step in interpretation.steps)
    first_step_kind = interpretation.steps[0].kind if interpretation.steps else "analysis"
    route_family = _WORKFLOW_KIND_TO_ROUTE.get(first_step_kind, RouteFamily.REPORT)
    return RequestIntent(
        route_family=route_family,
        workflow_type=primary,
        compound_workflow_chain=workflow_chain if interpretation.mode == "compound" else [],
        requires_approval=requires_approval,
        context_profile=context_profile,
        time_horizon=(interpretation.request_plan.time_horizon if interpretation.request_plan else "unspecified"),
        is_compound=interpretation.mode == "compound",
        rationale=interpretation.explanation,
        request_plan=interpretation.request_plan,
        subintents=[],
        response_format=str((interpretation.classified_intent or {}).get("response_format") or ""),
    )


async def classify_request_intent_async(
    payload: AssistantQueryRequest,
    *,
    llm_router: LLMRouter,
    unified_memory: dict[str, Any] | None = None,
    precomputed_request_plan: RequestPlan | None = None,
    precomputed_write_intent: tuple[bool, Optional[str]] | None = None,
    history: list[dict] | None = None,
    interpretation: RequestInterpretation | None = None,
) -> tuple[AssistantQueryRequest, RequestIntent]:
    """Primary async classifier.  Returns (updated_payload, RequestIntent).

    Implements the single-path precedence described in this module's docstring.
    No request reaches both LLMRouter and the keyword classifier as co-equal
    first-class classifiers.
    """
    _ = (llm_router, unified_memory, precomputed_write_intent)
    if interpretation is None:
        interpretation = await build_request_interpretation(
            message=(payload.message or "").strip(),
            history=history or [],
            request_plan=precomputed_request_plan,
            has_attachments=bool(payload.attachments),
        )
    intent = _interpretation_to_intent(interpretation)
    if intent.workflow_type:
        payload = payload.model_copy(update={"workflow_hint": intent.workflow_type})
    return payload, intent

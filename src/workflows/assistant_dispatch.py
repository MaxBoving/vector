from __future__ import annotations

import json
import logging
from typing import Any

from src.agents.schemas import RoutingDecision as AgentRoutingDecision, TaskIntent
from src.workflows.routing import RouteDecision, RouteFamily, RouteSubintent
from src.api.schemas import AssistantMessageResponse, AssistantQueryRequest
from src.core.database import get_assistant_conversation, get_interactions_for_conversation, get_or_create_live_context, get_world_reference_datetime
from src.core.models import SessionInteraction, User
from src.assistant.agent import AgenticAssistant
from src.runtime.engine import RuntimeEngine
from src.workflows.event_payloads import build_planning_context
from src.workflows.request_planner import plan_request
from src.workflows.routing import classify_route

logger = logging.getLogger(__name__)

_BRIEFING_WORKFLOWS = {
    "schedule_planning",
    "meeting_prep",
    "weekly_recap",
    "morning_brief",
    "calendar_briefing",
    "email_ingestion",
    "email_watcher",
}


def _task_intent_for_route(route_decision: RouteDecision, workflow_type: str) -> TaskIntent:
    if workflow_type == "document_explanation" or RouteSubintent.DOCUMENT_EXPLANATION in route_decision.subintents:
        return TaskIntent.DOCUMENT_REVIEW
    if route_decision.primary_intent == RouteFamily.ACT:
        if any(subintent in route_decision.subintents for subintent in (RouteSubintent.DRAFT_EMAIL, RouteSubintent.SEND_EMAIL)):
            return TaskIntent.EXECUTION_DRAFT
        return TaskIntent.EXECUTION_REQUEST
    if route_decision.primary_intent == RouteFamily.PLAN:
        return TaskIntent.EXECUTION_REQUEST
    if route_decision.primary_intent == RouteFamily.REPORT:
        return TaskIntent.STRATEGIC_ANALYSIS
    if route_decision.primary_intent == RouteFamily.WATCH:
        return TaskIntent.FACT_FINDING
    return TaskIntent.FACT_FINDING


def _runtime_routing_decision(workflow_type: str, request_plan, route_decision) -> AgentRoutingDecision:
    intent = _task_intent_for_route(route_decision, workflow_type)
    return AgentRoutingDecision(
        intent=intent,
        specialist_required=workflow_type,
        relevant_state_keys=list(getattr(request_plan, "requested_context_sources", []) or []),
        requires_approval=bool(getattr(route_decision, "requires_approval", False)),
        rationale=str(getattr(route_decision, "rationale", "") or ""),
    )


def _runtime_extra_metadata(
    payload: AssistantQueryRequest,
    request_plan,
    workflow_type: str,
    *,
    ceo_id: str,
) -> dict[str, object]:
    extra_metadata: dict[str, object] = {"request_plan": request_plan.model_dump(mode="json")}
    follow_up_context = getattr(payload, "follow_up_context", None)
    if follow_up_context and getattr(follow_up_context, "source_context", None):
        extra_metadata["follow_up_context"] = {
            "source_interaction_id": getattr(follow_up_context, "source_interaction_id", None),
            "source_response_type": getattr(follow_up_context, "source_response_type", None),
            "source_context": getattr(follow_up_context, "source_context", None),
            "selected_option_label": getattr(follow_up_context, "selected_option_label", None),
            "selected_option_value": getattr(follow_up_context, "selected_option_value", None),
            "selected_option_apply_text": getattr(follow_up_context, "selected_option_apply_text", None),
        }
    if payload.conversation_id:
        live_context = get_or_create_live_context(ceo_id, payload.conversation_id)
        resolved_clarifications = dict(getattr(live_context, "resolved_clarifications", {}) or {})
        if resolved_clarifications:
            extra_metadata["resolved_clarifications"] = resolved_clarifications
        clarification_resolutions = list(getattr(live_context, "clarification_resolutions", []) or [])
        if clarification_resolutions:
            extra_metadata["clarification_resolutions"] = clarification_resolutions[-5:]
    if workflow_type in _BRIEFING_WORKFLOWS:
        extra_metadata["event_payload"] = {
            "planning_context": build_planning_context(
                message=payload.message,
                request_plan=request_plan,
                ranked_threads=[],
                upcoming_events=[],
            )
        }
        extra_metadata["skip_clarification_gate"] = True
    return extra_metadata


def _follow_up_source_workflow_type(ceo_id: str, conversation_id: str, source_interaction_id: int | None) -> str | None:
    if source_interaction_id is None:
        return None

    conversation = get_assistant_conversation(ceo_id, conversation_id)
    if not conversation or source_interaction_id not in (conversation.interaction_ids or []):
        return None

    interactions = get_interactions_for_conversation(ceo_id, [source_interaction_id])
    source_interaction = interactions[-1] if interactions else None
    if not source_interaction or not source_interaction.response:
        return None

    try:
        parsed = json.loads(source_interaction.response)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None

    workflow_type = str(parsed.get("workflow_type") or "").strip()
    return workflow_type or None


async def generate_native_assistant_response(
    payload: AssistantQueryRequest,
    interaction: SessionInteraction,
    current_user: User,
    *,
    runtime: RuntimeEngine,
    agent: AgenticAssistant,
) -> AssistantMessageResponse:
    reference_dt = get_world_reference_datetime(current_user.ceo_id)
    follow_up_context = getattr(payload, "follow_up_context", None)
    planning_message = payload.message
    if follow_up_context and getattr(follow_up_context, "source_context", None):
        planning_message = f"{payload.message}\n\n[Internal context: {follow_up_context.source_context}]"
    request_plan = plan_request(
        planning_message,
        has_attachments=bool(payload.attachments),
        reference_dt=reference_dt,
    )
    routing_decision = classify_route(payload, precomputed_request_plan=request_plan)
    follow_up_workflow_type = None
    if follow_up_context:
        follow_up_workflow_type = _follow_up_source_workflow_type(
            current_user.ceo_id,
            payload.conversation_id,
            getattr(follow_up_context, "source_interaction_id", None),
        )
    workflow_type = str(
        follow_up_workflow_type
        or request_plan.target_workflow
        or request_plan.direct_workflow
        or ""
    ).strip()
    logger.info(
        "assistant.query routed ceo_id=%s conversation_id=%s interaction_id=%s workflow=%s horizon=%s target_date=%s route=%s",
        current_user.ceo_id,
        payload.conversation_id,
        interaction.id,
        workflow_type or "unknown",
        request_plan.time_horizon,
        request_plan.target_date,
        getattr(routing_decision, "primary_intent", None),
    )
    if workflow_type and workflow_type != "conversational":
        definition = runtime._definition_for_type(workflow_type)
        response = await runtime.run(
            definition=definition,
            payload=payload,
            interaction=interaction,
            current_user=current_user,
            routing_decision=_runtime_routing_decision(workflow_type, request_plan, routing_decision),
            extra_metadata=_runtime_extra_metadata(
                payload,
                request_plan,
                workflow_type,
                ceo_id=current_user.ceo_id,
            ),
        )
        logger.info(
            "assistant.query completed workflow=%s interaction_id=%s response_type=%s status=%s",
            workflow_type,
            interaction.id,
            getattr(response, "response_type", None),
            getattr(response, "status", None),
        )
        return response
    response = await agent.handle(
        payload=payload,
        interaction=interaction,
        current_user=current_user,
    )
    logger.info(
        "assistant.query completed workflow=conversational interaction_id=%s response_type=%s status=%s",
        interaction.id,
        getattr(response, "response_type", None),
        getattr(response, "status", None),
    )
    return response

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.types import WorkflowType


@dataclass(frozen=True)
class ActionSemanticSignals:
    visible_message: str
    email_action: bool
    calendar_action: bool
    explicit_execution_request: bool
    direct_capability_question: bool
    integration_setup_question: bool
    external_delivery_requested: bool
    requested_channels: tuple[str, ...]
    requires_analysis_before_action: bool


def classify_action_semantics(
    *,
    message: str,
    workflow_preference: str | None = None,
    task_topic: str | None = None,
    resolved_action_reference: dict[str, Any] | None = None,
    routing_decision: dict[str, Any] | None = None,
    precomputed_request_workflow: str | None = None,
    precomputed_write_intent: tuple[bool, str | None] | None = None,
) -> ActionSemanticSignals:
    visible_message = extract_visible_request_text(message).strip()
    request_workflow = precomputed_request_workflow or workflow_preference

    channels = _infer_requested_channels(
        message=visible_message,
        workflow_preference=request_workflow,
        resolved_action_reference=resolved_action_reference,
        routing_decision=routing_decision,
        precomputed_write_intent=precomputed_write_intent,
    )
    explicit_execution = _infer_explicit_execution_request(
        workflow_preference=request_workflow,
        resolved_action_reference=resolved_action_reference,
        routing_decision=routing_decision,
        precomputed_write_intent=precomputed_write_intent,
        channels=channels,
    )
    direct_capability = _infer_direct_capability_question(
        explicit_execution_request=explicit_execution,
        channels=channels,
    )
    integration_setup = _infer_integration_setup_question(
        resolved_action_reference=resolved_action_reference,
        channels=channels,
    )
    requires_analysis = _infer_requires_analysis_before_action(
        task_topic=task_topic,
        resolved_action_reference=resolved_action_reference,
    )

    return ActionSemanticSignals(
        visible_message=visible_message,
        email_action="email" in channels,
        calendar_action="calendar" in channels,
        explicit_execution_request=explicit_execution,
        direct_capability_question=direct_capability,
        integration_setup_question=integration_setup,
        external_delivery_requested=bool(channels)
        or bool(precomputed_write_intent and precomputed_write_intent[0])
        or bool(resolved_action_reference)
        or bool((routing_decision or {}).get("requires_approval")),
        requested_channels=tuple(channels),
        requires_analysis_before_action=requires_analysis,
    )


def _infer_requested_channels(
    *,
    message: str,
    workflow_preference: str | None,
    resolved_action_reference: dict[str, Any] | None,
    routing_decision: dict[str, Any] | None,
    precomputed_write_intent: tuple[bool, str | None] | None = None,
) -> list[str]:
    channels: list[str] = []
    if isinstance(resolved_action_reference, dict):
        action_type = str(resolved_action_reference.get("action_type") or "").strip()
        if action_type == "send_email":
            channels.append("email")
        if action_type in {"schedule_call", "calendar_create"}:
            channels.append("calendar")

    if precomputed_write_intent is not None:
        is_write, action_type = precomputed_write_intent
        if is_write and action_type == "email":
            channels.append("email")
        if is_write and action_type == "calendar":
            channels.append("calendar")
    if routing_decision and str(routing_decision.get("intent") or "").strip() == "execution_draft":
        channels.append("email")

    if workflow_preference == WorkflowType.EMAIL_INGESTION:
        channels.append("email")
    return list(dict.fromkeys(channels))


def _infer_explicit_execution_request(
    *,
    workflow_preference: str | None,
    resolved_action_reference: dict[str, Any] | None,
    routing_decision: dict[str, Any] | None,
    precomputed_write_intent: tuple[bool, str | None] | None,
    channels: list[str],
) -> bool:
    if resolved_action_reference:
        return True
    if routing_decision and bool(routing_decision.get("requires_approval")):
        return True
    if workflow_preference == WorkflowType.EMAIL_INGESTION:
        return True
    if precomputed_write_intent is not None:
        return bool(precomputed_write_intent[0])
    return bool(channels)


def _infer_direct_capability_question(
    *,
    explicit_execution_request: bool,
    channels: list[str],
) -> bool:
    return explicit_execution_request and bool(channels)


def _infer_integration_setup_question(*, resolved_action_reference: dict[str, Any] | None, channels: list[str]) -> bool:
    if resolved_action_reference:
        return bool(channels)
    return False


def _infer_requires_analysis_before_action(
    *,
    task_topic: str | None,
    resolved_action_reference: dict[str, Any] | None,
) -> bool:
    if resolved_action_reference:
        return False
    return task_topic == "customer_escalation"

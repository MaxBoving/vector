from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.routing import _classify_write_intent
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
    precomputed_request_workflow: str | None = None,
    precomputed_write_intent: tuple[bool, str | None] | None = None,
) -> ActionSemanticSignals:
    visible_message = extract_visible_request_text(message).strip()
    lowered = visible_message.lower()
    request_workflow = precomputed_request_workflow or workflow_preference

    channels = _infer_requested_channels(
        lowered=lowered,
        workflow_preference=request_workflow,
        resolved_action_reference=resolved_action_reference,
        precomputed_write_intent=precomputed_write_intent,
    )
    explicit_execution = _infer_explicit_execution_request(
        lowered=lowered,
        workflow_preference=request_workflow,
        resolved_action_reference=resolved_action_reference,
        channels=channels,
    )
    direct_capability = _infer_direct_capability_question(
        lowered=lowered,
        explicit_execution_request=explicit_execution,
        channels=channels,
    )
    integration_setup = _infer_integration_setup_question(lowered=lowered, channels=channels)
    requires_analysis = _infer_requires_analysis_before_action(
        lowered=lowered,
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
        external_delivery_requested=any(marker in lowered for marker in ("send to ", "share with ", "forward to ", "sign off", "external")),
        requested_channels=tuple(channels),
        requires_analysis_before_action=requires_analysis,
    )


def _infer_requested_channels(
    *,
    lowered: str,
    workflow_preference: str | None,
    resolved_action_reference: dict[str, Any] | None,
    precomputed_write_intent: tuple[bool, str | None] | None = None,
) -> list[str]:
    channels: list[str] = []
    planning_context_only = workflow_preference == WorkflowType.SCHEDULE_PLANNING and not any(
        marker in lowered
        for marker in (
            "send",
            "draft",
            "write",
            "reply",
            "book",
            "set up",
            "coordinate",
            "call",
            "meeting",
            "follow-up call",
        )
    )
    if isinstance(resolved_action_reference, dict):
        action_type = str(resolved_action_reference.get("action_type") or "").strip()
        if action_type == "send_email":
            channels.append("email")
        if action_type in {"schedule_call", "calendar_create"}:
            channels.append("calendar")

    if precomputed_write_intent is not None:
        is_write, action_type = precomputed_write_intent
    else:
        try:
            is_write, action_type = _classify_write_intent(lowered)
        except Exception:
            is_write, action_type = False, None
    if workflow_preference == WorkflowType.SCHEDULE_PLANNING and not any(
        marker in lowered for marker in ("call", "meeting", "calendar", "book", "follow-up call")
    ):
        is_write, action_type = False, None
    if is_write and action_type == "email":
        channels.append("email")
    if is_write and action_type == "calendar":
        channels.append("calendar")

    if workflow_preference == WorkflowType.EMAIL_INGESTION:
        channels.append("email")
    if not planning_context_only and any(marker in lowered for marker in ("email", "mail", "cc ", "copy ", "send it", "send this", "send that", "send the email")):
        channels.append("email")
    if not planning_context_only and any(marker in lowered for marker in ("calendar", "call", "meeting", "book")):
        channels.append("calendar")
    if not planning_context_only and "schedule" in lowered and any(marker in lowered for marker in ("call", "meeting", "calendar", "book", "follow-up call")):
        channels.append("calendar")
    return list(dict.fromkeys(channels))


def _infer_explicit_execution_request(
    *,
    lowered: str,
    workflow_preference: str | None,
    resolved_action_reference: dict[str, Any] | None,
    channels: list[str],
) -> bool:
    if resolved_action_reference:
        return True
    if workflow_preference == WorkflowType.EMAIL_INGESTION:
        return True
    if workflow_preference == WorkflowType.SCHEDULE_PLANNING and not any(marker in lowered for marker in ("call", "meeting", "calendar", "book")):
        return False
    return bool(channels) and any(
        marker in lowered
        for marker in (
            "send",
            "schedule",
            "book",
            "set up",
            "coordinate",
            "copy ",
            "cc ",
            "delegate",
            "immediately",
            "right now",
            "within the hour",
        )
    )


def _infer_direct_capability_question(
    *,
    lowered: str,
    explicit_execution_request: bool,
    channels: list[str],
) -> bool:
    meta_markers = (
        "can you actually",
        "whether you can",
        "if you cannot execute",
        "if you can't",
        "say so immediately",
        "tell me explicitly whether",
        "tell me if you can",
        "do i need to do this manually",
        "do i need to send it manually",
        "do i need to do that manually",
        "do i need to schedule it myself",
        "straight yes or no",
        "execution capabilities",
    )
    if any(marker in lowered for marker in meta_markers):
        return True
    return explicit_execution_request and bool(channels) and any(
        marker in lowered for marker in ("can you", "manually", "yourself", "from this environment")
    )


def _infer_integration_setup_question(*, lowered: str, channels: list[str]) -> bool:
    setup_markers = (
        "which integrations",
        "what integrations",
        "outlook integration",
        "google integration",
        "set up to make this work",
        "make this work properly",
        "connect outlook",
        "connect google",
        "set up properly",
    )
    if any(marker in lowered for marker in setup_markers):
        return True
    provider_mention = any(marker in lowered for marker in ("outlook", "google", "gmail", "calendar"))
    return bool(channels) and provider_mention and any(marker in lowered for marker in ("connect", "integration", "set up"))


def _infer_requires_analysis_before_action(
    *,
    lowered: str,
    task_topic: str | None,
    resolved_action_reference: dict[str, Any] | None,
) -> bool:
    if resolved_action_reference:
        return False
    analysis_markers = (
        "identify",
        "which customer",
        "highest-risk",
        "highest risk",
        "analyze",
        "analyse",
        "assess",
        "rank",
        "prioritize",
        "renewal dates",
        "support tickets",
        "usage patterns",
        "customer data",
    )
    customer_markers = (
        "customer",
        "customers",
        "account",
        "renewal",
        "support ticket",
        "usage",
        "apex",
        "redwood",
    )
    action_markers = ("draft", "email", "send", "copy", "cc", "schedule", "set up", "call")
    if not all([
        any(marker in lowered for marker in analysis_markers),
        any(marker in lowered for marker in customer_markers),
        any(marker in lowered for marker in action_markers),
    ]):
        return False
    return task_topic == "customer_escalation" or True

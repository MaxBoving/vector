from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.action_semantics import ActionSemanticSignals, classify_action_semantics
from src.workflows.planning_types import RequestPlan
from src.workflows.request_planner import plan_request
from src.workflows.routing import _classify_write_intent
from src.workflows.types import WorkflowType


@dataclass(frozen=True)
class RunnerSemanticSignals:
    visible_message: str
    request_plan_direct_workflow: str | None
    explicit_execution_request: bool
    direct_capability_question: bool
    integration_setup_question: bool
    requested_channels: tuple[str, ...]
    report_followup: bool
    live_context_followup: bool


@dataclass(frozen=True)
class SharedTurnSemanticBundle:
    visible_message: str
    request_plan: RequestPlan | None
    write_intent: tuple[bool, str | None]
    runner_signals: RunnerSemanticSignals
    action_signals: ActionSemanticSignals


def build_turn_semantic_bundle(
    *,
    message: str,
    live_context: dict[str, Any] | None = None,
    workflow_preference: str | None = None,
    task_topic: str | None = None,
    resolved_action_reference: dict[str, Any] | None = None,
    unified_memory: dict[str, Any] | None = None,
    precomputed_request_plan: RequestPlan | None = None,
) -> SharedTurnSemanticBundle:
    visible_message = extract_visible_request_text(message).strip()
    request_plan = precomputed_request_plan
    if request_plan is None and visible_message:
        request_plan = plan_request(visible_message, has_attachments=False, unified_memory=unified_memory)
    lowered = visible_message.lower()
    try:
        write_intent = _classify_write_intent(lowered)
    except Exception:
        write_intent = (False, None)

    request_workflow = str(request_plan.direct_workflow or request_plan.target_workflow or "").strip() if request_plan else None
    runner_signals = classify_runner_semantics(
        message=message,
        live_context=live_context,
        resolved_action_reference=resolved_action_reference,
        precomputed_request_plan=request_plan,
        precomputed_write_intent=write_intent,
    )
    action_signals = classify_action_semantics(
        message=message,
        workflow_preference=workflow_preference,
        task_topic=task_topic,
        resolved_action_reference=resolved_action_reference,
        precomputed_request_workflow=request_workflow or workflow_preference,
        precomputed_write_intent=write_intent,
    )
    return SharedTurnSemanticBundle(
        visible_message=visible_message,
        request_plan=request_plan,
        write_intent=write_intent,
        runner_signals=runner_signals,
        action_signals=action_signals,
    )


def classify_runner_semantics(
    *,
    message: str,
    live_context: dict[str, Any] | None = None,
    resolved_action_reference: dict[str, Any] | None = None,
    precomputed_request_plan: RequestPlan | None = None,
    precomputed_write_intent: tuple[bool, str | None] | None = None,
) -> RunnerSemanticSignals:
    visible_message = extract_visible_request_text(message).strip()
    lowered = visible_message.lower()
    request_plan = precomputed_request_plan
    if request_plan is None and visible_message:
        request_plan = plan_request(visible_message, has_attachments=False)
    request_workflow = str(request_plan.direct_workflow or request_plan.target_workflow or "").strip() if request_plan else None

    channels = _infer_requested_channels(
        lowered=lowered,
        request_workflow=request_workflow,
        resolved_action_reference=resolved_action_reference,
        precomputed_write_intent=precomputed_write_intent,
    )
    explicit_execution_request = _infer_explicit_execution_request(
        lowered=lowered,
        request_workflow=request_workflow,
        resolved_action_reference=resolved_action_reference,
        channels=channels,
    )
    direct_capability_question = _infer_direct_capability_question(
        lowered=lowered,
        explicit_execution_request=explicit_execution_request,
        channels=channels,
    )
    integration_setup_question = _infer_integration_setup_question(
        lowered=lowered,
        channels=channels,
    )
    report_followup = _infer_report_followup(
        lowered=lowered,
        request_workflow=request_workflow,
        live_context=live_context or {},
        explicit_execution_request=explicit_execution_request,
        direct_capability_question=direct_capability_question,
    )
    live_context_followup = _infer_live_context_followup(
        lowered=lowered,
        live_context=live_context or {},
        report_followup=report_followup,
    )

    return RunnerSemanticSignals(
        visible_message=visible_message,
        request_plan_direct_workflow=request_workflow,
        explicit_execution_request=explicit_execution_request,
        direct_capability_question=direct_capability_question,
        integration_setup_question=integration_setup_question,
        requested_channels=tuple(channels),
        report_followup=report_followup,
        live_context_followup=live_context_followup,
    )


def _infer_requested_channels(
    *,
    lowered: str,
    request_workflow: str | None,
    resolved_action_reference: dict[str, Any] | None,
    precomputed_write_intent: tuple[bool, str | None] | None = None,
) -> list[str]:
    channels: list[str] = []
    planning_context_only = request_workflow == WorkflowType.SCHEDULE_PLANNING and not any(
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
    if request_workflow == WorkflowType.SCHEDULE_PLANNING and not any(
        marker in lowered for marker in ("call", "meeting", "calendar", "book", "follow-up call")
    ):
        is_write, action_type = False, None
    if is_write and action_type == "email":
        channels.append("email")
    if is_write and action_type == "calendar":
        channels.append("calendar")

    if request_workflow == WorkflowType.EMAIL_INGESTION:
        channels.append("email")
    if request_workflow == WorkflowType.SCHEDULE_PLANNING and any(
        marker in lowered for marker in ("call", "meeting", "calendar", "book")
    ):
        channels.append("calendar")

    if not planning_context_only and any(marker in lowered for marker in ("email", "mail", "cc ", "copy ")):
        channels.append("email")
    if not planning_context_only and any(marker in lowered for marker in ("send it", "send this", "send that", "send the email")):
        channels.append("email")
    if not planning_context_only and any(marker in lowered for marker in ("calendar", "call", "meeting", "book")):
        channels.append("calendar")

    # Preserve "schedule" as a calendar signal only when paired with an execution frame.
    if not planning_context_only and "schedule" in lowered and any(marker in lowered for marker in ("call", "meeting", "calendar", "book", "follow-up call")):
        channels.append("calendar")

    return list(dict.fromkeys(channels))


def _infer_explicit_execution_request(
    *,
    lowered: str,
    request_workflow: str | None,
    resolved_action_reference: dict[str, Any] | None,
    channels: list[str],
) -> bool:
    if resolved_action_reference:
        return True
    if request_workflow == WorkflowType.EMAIL_INGESTION:
        return True
    if request_workflow == WorkflowType.SCHEDULE_PLANNING and not any(
        marker in lowered for marker in ("call", "meeting", "calendar", "book")
    ):
        return False

    action_like = any(
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
    return bool(channels) and action_like


def _infer_direct_capability_question(
    *,
    lowered: str,
    explicit_execution_request: bool,
    channels: list[str],
) -> bool:
    if not lowered:
        return False
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


def _infer_integration_setup_question(
    *,
    lowered: str,
    channels: list[str],
) -> bool:
    if not lowered:
        return False
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


def _has_parent_context(live_context: dict[str, Any]) -> bool:
    return any(
        [
            live_context.get("current_schedule"),
            live_context.get("open_decisions"),
            live_context.get("open_commitments"),
            live_context.get("last_agent_contributions"),
        ]
    )


def _infer_report_followup(
    *,
    lowered: str,
    request_workflow: str | None,
    live_context: dict[str, Any],
    explicit_execution_request: bool,
    direct_capability_question: bool,
) -> bool:
    if not _has_parent_context(live_context):
        return False
    if explicit_execution_request or direct_capability_question:
        return False

    if request_workflow == WorkflowType.REPORT_GENERATION:
        return True

    artifact_markers = (
        "pptx",
        "powerpoint",
        "slides",
        "deck",
        "docx",
        "memo",
        "workbook",
        "xlsx",
        "excel",
        "chart",
        "forecast",
        "presentation",
    )
    if any(marker in lowered for marker in artifact_markers) and re.search(r"\b(that|this|it|above)\b", lowered):
        return True

    contextual_conversion_markers = (
        "make a memo",
        "make me a memo",
        "turn into a memo",
        "turn into memo",
        "make a deck",
        "make me a deck",
        "make a presentation",
        "turn into a deck",
        "turn into slides",
        "make a workbook",
        "turn into a workbook",
        "make a chart",
        "refine this",
        "refine that",
        "expand this",
        "expand that",
        "polish this",
        "polish that",
    )
    if any(marker in lowered for marker in contextual_conversion_markers):
        return True

    if len(lowered.split()) <= 12 and any(marker in lowered for marker in ("board prep", "open decision", "follow-up", "follow up")):
        return True
    return False


def _infer_live_context_followup(
    *,
    lowered: str,
    live_context: dict[str, Any],
    report_followup: bool,
) -> bool:
    if not lowered or not _has_parent_context(live_context):
        return False
    if report_followup:
        return True

    explicit_markers = (
        "refine that",
        "refine this",
        "turn that into",
        "turn this into",
        "make that into",
        "make this into",
        "from this",
        "from that",
        "use that",
        "use this",
        "based on that",
        "based on this",
        "the above",
        "from the schedule",
        "from schedule",
        "from the plan",
        "from plan",
        "use the schedule",
        "use the plan",
        "turn the schedule into",
        "turn the plan into",
    )
    if any(marker in lowered for marker in explicit_markers):
        return True
    return len(lowered.split()) <= 10 and bool(re.search(r"\b(that|this|it|above)\b", lowered))

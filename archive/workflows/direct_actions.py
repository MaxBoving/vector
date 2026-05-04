from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_DIRECT_ACTION_MODEL = (
    os.getenv("DIRECT_ACTION_LLM_MODEL")
    or os.getenv("ANTHROPIC_SIMPLE_MODEL")
    or os.getenv("ANTHROPIC_MODEL")
    or "claude-3-haiku-20240307"
)

from sqlmodel import Session

from src.api.schemas import AnswerPayload, AnswerSection, AssistantMessageResponse, AssistantQueryRequest, DecisionOption, DecisionPresentation, DraftPresentation, MessagePresentation, TrustMetadata
from src.core.database import engine, get_or_create_live_context, update_live_context
from src.core.models import SessionInteraction, User
from src.integrations.providers import (
    ProviderIntegrationError,
    create_calendar_write,
    create_email_draft_write,
    get_integration_statuses,
    send_email_write,
)
from src.workflows.approval_envelope import build_approval_metadata, build_approval_metadata_from_record, normalize_gate_metadata
from src.workflows.approval_records import build_approval_record, build_pending_interaction_context, build_resolved_interaction_context
from src.workflows.action_references import merge_pending_actions
from src.workflows.action_semantics import classify_action_semantics
from src.workflows.interaction_persistence import persist_interaction_state, serialize_interaction_response
from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.types import WorkflowType


async def maybe_handle_direct_action_request(
    *,
    payload: AssistantQueryRequest,
    interaction: SessionInteraction,
    current_user: User,
    history: List[dict] | None = None,
    unified_memory: Dict[str, Any] | None = None,
    action_signals: Any | None = None,
    typed_requirements: dict[str, Any] | None = None,
) -> AssistantMessageResponse | None:
    working_memory = (unified_memory or {}).get("working_memory") or {}
    resolved_action_reference = working_memory.get("resolved_action_reference") or {}
    write_action_requested = bool(working_memory.get("write_action_requested"))
    workflow_preference = str(working_memory.get("workflow_preference") or "")
    mode = str(working_memory.get("mode") or "")
    must_not_do = {str(item) for item in (working_memory.get("must_not_do") or []) if str(item)}
    deliverable = working_memory.get("deliverable") or {}
    deliverable_kind = str(deliverable.get("kind") or "")
    if "calendar_briefing" in must_not_do or "email_ingestion" in must_not_do:
        return None
    # Keep report-side follow-up turns on the document workflow. A live schedule in
    # memory is common during CEO conversations and must not by itself trigger a
    # calendar action path when the CEO is revising a board packet or other report.
    if workflow_preference == WorkflowType.REPORT_GENERATION and mode in {"revision", "continuation", "correction"}:
        return None
    if deliverable_kind in {"artifact_revision", "resolution_language", "execution_bundle"}:
        return None
    if deliverable_kind == "email" and not bool(working_memory.get("write_action_requested")):
        return None
    if _requires_analysis_before_action(
        message=payload.message,
        working_memory=working_memory if isinstance(working_memory, dict) else {},
        resolved_action_reference=resolved_action_reference if isinstance(resolved_action_reference, dict) else None,
    ):
        return None
    if action_signals is None:
        action_signals = classify_action_semantics(
            message=payload.message,
            workflow_preference=workflow_preference,
            task_topic=str(working_memory.get("task_topic") or ""),
            resolved_action_reference=resolved_action_reference if isinstance(resolved_action_reference, dict) else None,
        )
    explicit_execution_requested = action_signals.explicit_execution_request

    schedule_proposal = await _build_calendar_proposal(
        payload.message,
        history=history,
        unified_memory=unified_memory,
        action_signals=action_signals,
        typed_requirements=typed_requirements,
    )
    if schedule_proposal is not None:
        if explicit_execution_requested and not _has_connected_write_provider(current_user.ceo_id, channel="calendar"):
            response = _build_execution_unavailable_response(
                payload=payload,
                interaction=interaction,
                workflow_type=WorkflowType.CALENDAR_BRIEFING,
                channel="calendar",
                reason="No writable calendar provider is connected in this environment.",
                proposal=schedule_proposal,
            )
            _persist_direct_response(interaction.id, response, status="COMPLETED", gate_type=None, context=None)
            return response
        if schedule_proposal.get("missing_fields"):
            response = _build_calendar_clarification_response(payload, interaction, schedule_proposal)
            _persist_direct_response(interaction.id, response, status="COMPLETED", gate_type=None, context=None)
            return response
        response = _build_pending_proposal_response(
            payload=payload,
            interaction=interaction,
            workflow_type=WorkflowType.CALENDAR_BRIEFING,
            title="Approve Calendar Event",
            summary=f"Ready to create '{schedule_proposal['title']}' for {schedule_proposal['display_day']} at {schedule_proposal['display_time']}.",
            proposal_type="calendar_create",
            proposal=schedule_proposal,
            reason="Approve this calendar event before it is written to your connected calendar.",
        )
        _persist_direct_response(
            interaction.id,
            response,
            status="AWAITING_INPUT",
            gate_type="HUMAN_APPROVAL",
            context=build_pending_interaction_context(
                gate=response.metadata.get("gate"),
                extra={"direct_action": {"proposal_type": "calendar_create", "proposal": schedule_proposal}},
            ),
        )
        return response

    email_proposal = await _build_email_proposal(
        payload.message,
        history=history,
        unified_memory=unified_memory,
        action_signals=action_signals,
        typed_requirements=typed_requirements,
    )
    if email_proposal is not None:
        if not _has_connected_write_provider(current_user.ceo_id, channel="email"):
            response = _build_execution_unavailable_response(
                payload=payload,
                interaction=interaction,
                workflow_type=WorkflowType.EMAIL_INGESTION,
                channel="email",
                reason="No writable email provider is connected in this environment.",
                proposal=email_proposal,
            )
            _persist_direct_response(interaction.id, response, status="COMPLETED", gate_type=None, context=None)
            return response
        if email_proposal.get("missing_fields"):
            response = _build_email_clarification_response(payload, interaction, email_proposal)
            _persist_direct_response(interaction.id, response, status="COMPLETED", gate_type=None, context=None)
            return response
        response = _build_pending_proposal_response(
            payload=payload,
            interaction=interaction,
            workflow_type=WorkflowType.EMAIL_INGESTION,
            title="Email Draft Ready",
            summary=f"The email draft to {email_proposal['to']} is ready below. Send it or discard it.",
            proposal_type="email_draft",
            proposal=email_proposal,
            reason="Review the draft below, then send it or discard it.",
        )
        _persist_direct_response(
            interaction.id,
            response,
            status="AWAITING_INPUT",
            gate_type="HUMAN_APPROVAL",
            context=build_pending_interaction_context(
                gate=response.metadata.get("gate"),
                extra={"direct_action": {
                    "proposal_type": "email_draft",
                    "proposal": email_proposal,
                    "action_id": resolved_action_reference.get("action_id") if isinstance(resolved_action_reference, dict) else None,
                }},
            ),
        )
        return response

    return None


def resolve_direct_action(
    *,
    interaction_id: int,
    current_user: User,
    decision: str,
    mode: Optional[str] = None,
    note: Optional[str] = None,
) -> Optional[AssistantMessageResponse]:
    with Session(engine) as session:
        interaction = session.get(SessionInteraction, interaction_id)
        if not interaction or interaction.ceo_id != current_user.ceo_id:
            return None
        context = _parse_context(interaction.missing_data_context)
    direct_action = context.get("direct_action") if isinstance(context, dict) else None
    if not isinstance(direct_action, dict):
        return None

    proposal_type = direct_action.get("proposal_type")
    proposal = direct_action.get("proposal", {})
    action_id = direct_action.get("action_id")
    gate = context.get("gate") if isinstance(context, dict) else None
    approval_record = build_approval_record(
        stage="direct_action",
        gate=gate if isinstance(gate, dict) else None,
        decision=decision.lower(),
        note=note,
        actor=current_user.username,
        mode=mode,
    )
    if decision.lower() == "reject":
        response = _build_direct_rejection_response(interaction, proposal_type, note, approval_record)
        _persist_direct_response(
            interaction.id,
            response,
            status="FAILED",
            gate_type=None,
            context=build_resolved_interaction_context(approval=approval_record),
        )
        _update_pending_action_status(
            interaction=interaction,
            current_user=current_user,
            action_id=action_id,
            status="failed",
        )
        return response

    if decision.lower() != "approve":
        raise RuntimeError(f"Unsupported approval decision: {decision}")

    try:
        if proposal_type == "calendar_create":
            result = create_calendar_write(current_user.ceo_id, proposal)
            response = _build_direct_success_response(
                interaction=interaction,
                workflow_type=WorkflowType.CALENDAR_BRIEFING,
                title="Calendar Event Created",
                summary=f"Created '{result.get('title', proposal.get('title', 'Scheduled meeting'))}' on your connected calendar.",
                details=[
                    f"When: {proposal.get('display_day')} at {proposal.get('display_time')}",
                    f"Provider: {result.get('provider', 'calendar')}",
                ],
                link=result.get("html_link"),
                approval_record=approval_record,
            )
        elif proposal_type == "email_draft":
            approval_mode = (mode or "draft").lower()
            if approval_mode == "send":
                result = send_email_write(current_user.ceo_id, proposal)
                response = _build_direct_success_response(
                    interaction=interaction,
                    workflow_type=WorkflowType.EMAIL_INGESTION,
                    title="Email Sent",
                    summary=f"Sent the email to {result.get('to', proposal.get('to'))} with subject '{result.get('subject', proposal.get('subject'))}'.",
                    details=[
                        f"Provider: {result.get('provider', 'mail')}",
                        "The message was sent after explicit approval.",
                    ],
                    link=None,
                    draft_payload=proposal,
                    approval_record=approval_record,
                )
            else:
                result = create_email_draft_write(current_user.ceo_id, proposal)
                response = _build_direct_success_response(
                    interaction=interaction,
                    workflow_type=WorkflowType.EMAIL_INGESTION,
                    title="Email Draft Created",
                    summary=f"Created a draft to {result.get('to', proposal.get('to'))} with subject '{result.get('subject', proposal.get('subject'))}'.",
                    details=[
                        f"Provider: {result.get('provider', 'mail')}",
                        "The draft is available in your connected mailbox for review before sending.",
                    ],
                    link=None,
                    draft_payload=proposal,
                    approval_record=approval_record,
                )
        else:
            raise RuntimeError(f"Unsupported direct action proposal: {proposal_type}")
    except ProviderIntegrationError as exc:
        response = _build_execution_unavailable_response(
            payload=AssistantQueryRequest(message=interaction.query or "", conversation_id=_conversation_id_for_interaction(interaction)),
            interaction=interaction,
            workflow_type=WorkflowType.CALENDAR_BRIEFING if proposal_type == "calendar_create" else WorkflowType.EMAIL_INGESTION,
            channel="calendar" if proposal_type == "calendar_create" else "email",
            reason=str(exc),
            proposal=proposal,
            approved=True,
        )
        _persist_direct_response(
            interaction.id,
            response,
            status="FAILED",
            gate_type=None,
            context=build_resolved_interaction_context(approval=approval_record),
        )
        _update_pending_action_status(
            interaction=interaction,
            current_user=current_user,
            action_id=action_id,
            status="failed",
        )
        return response

    _persist_direct_response(
        interaction.id,
        response,
        status="COMPLETED",
        gate_type=None,
        context=build_resolved_interaction_context(approval=approval_record),
    )
    _update_pending_action_status(
        interaction=interaction,
        current_user=current_user,
        action_id=action_id,
        status="executed",
    )
    return response


def _is_explicit_execution_request(
    *,
    message: str,
    resolved_action_reference: dict[str, Any] | None = None,
) -> bool:
    return classify_action_semantics(
        message=message,
        resolved_action_reference=resolved_action_reference,
    ).explicit_execution_request


def _has_connected_write_provider(ceo_id: str, *, channel: str) -> bool:
    services = {"email": {"gmail", "outlook_mail"}, "calendar": {"google_calendar", "outlook_calendar"}}.get(channel, set())
    if not services:
        return False
    try:
        statuses = get_integration_statuses(ceo_id)
    except Exception:
        return False
    return any(
        isinstance(item, dict)
        and item.get("service") in services
        and bool(item.get("connected"))
        for item in statuses
    )


def _build_execution_unavailable_response(
    *,
    payload: AssistantQueryRequest,
    interaction: SessionInteraction,
    workflow_type: str,
    channel: str,
    reason: str,
    proposal: dict[str, Any],
    approved: bool = False,
) -> AssistantMessageResponse:
    action_label = "send the email" if channel == "email" else "book the calendar event"
    question_options = []
    if channel == "email":
        question_options = [
            {
                "question": "Connect an email provider to send this directly.",
                "offer_type": "action_offer",
                "options": [
                    {
                        "label": "Google Workspace",
                        "value": "connect_google_workspace",
                        "apply_text": "Connect Google Workspace so you can send this directly.",
                        "description": "Connect Gmail to send from your workspace email.",
                    },
                    {
                        "label": "Microsoft Outlook",
                        "value": "connect_outlook_workspace",
                        "apply_text": "Connect Microsoft Outlook so you can send this directly.",
                        "description": "Connect Outlook to send from your work mailbox.",
                    },
                ],
            }
        ]
    sections = [
        AnswerSection(
            label="What I Can Do Here",
            items=[
                "I can prepare the exact content and handoff details for immediate manual execution.",
            ],
        ),
        AnswerSection(
            label="Execution Limit",
            items=[
                reason,
                f"I cannot {action_label} from this environment.",
            ],
        ),
        AnswerSection(
            label="Ready Now",
            items=_proposal_lines("email_draft" if channel == "email" else "calendar_create", proposal),
        ),
    ]
    return AssistantMessageResponse(
        conversation_id=payload.conversation_id,
        message_id=f"msg_{interaction.id}",
        workflow_type=workflow_type,
        response_type="report",
        status="completed",
        answer=AnswerPayload(
            title="Execution Not Available Here",
            summary=(
                f"I prepared the {channel} content, but I cannot execute the {channel} write action from this environment."
            ),
            sections=sections,
        ),
        trust=TrustMetadata(
            confidence="high",
            confidence_score=0.94,
            assumptions=[],
            open_questions=[],
            data_quality="high",
            calculation_used=False,
            missing_context=[reason],
            question_options=question_options,
        ),
        sources=[],
        artifacts=[],
        presentation=MessagePresentation(
            mode="draft" if channel == "email" else "decision",
            variant="execution_unavailable",
            preamble=(
                "Connect an email provider to send this directly."
                if channel == "email"
                else None
            ),
            draft=DraftPresentation(
                channel=channel,
                status="ready_for_manual_send" if channel == "email" else "ready_for_manual_booking",
                to=proposal.get("to") or None,
                cc=list(proposal.get("cc") or []),
                subject=proposal.get("subject") or None,
                body=proposal.get("body") or None,
                call_to_action=(
                    "Connect Google Workspace or Microsoft Outlook to send this directly."
                    if channel == "email"
                    else "Book this in your calendar manually now."
                ),
            ) if channel == "email" else None,
            decision=DecisionPresentation(
                decision_summary=f"The {channel} content is ready, but direct execution is unavailable here.",
                impact_if_rejected="The write action will remain manual until a writable provider is connected.",
            ) if channel != "email" else None,
        ),
        metadata={
            "interaction_id": interaction.id,
            "query": payload.message,
            "timestamp": interaction.timestamp,
            "execution_unavailable": {
                "channel": channel,
                "reason": reason,
                "approved": approved,
            },
        },
    )


def _build_pending_proposal_response(
    *,
    payload: AssistantQueryRequest,
    interaction: SessionInteraction,
    workflow_type: str,
    title: str,
    summary: str,
    proposal_type: str,
    proposal: dict[str, Any],
    reason: str,
) -> AssistantMessageResponse:
    sections = [
        AnswerSection(label="Proposed Action", items=_proposal_lines(proposal_type, proposal)),
    ]
    if proposal_type != "email_draft":
        sections.append(AnswerSection(label="Approval Required", items=_approval_lines(proposal_type)))
    return AssistantMessageResponse(
        conversation_id=payload.conversation_id,
        message_id=f"msg_{interaction.id}",
        workflow_type=workflow_type,
        response_type="report",
        status="pending",
        answer=AnswerPayload(title=title, summary=summary, sections=sections),
        trust=TrustMetadata(
            confidence="high",
            confidence_score=0.95,
            assumptions=["This action will not execute until you approve it."],
            open_questions=["Approve or reject this proposed write action."],
            data_quality="high",
            calculation_used=False,
            missing_context=[],
        ),
        sources=[],
        artifacts=[],
        presentation=MessagePresentation(
            mode="draft" if proposal_type == "email_draft" else "decision",
            variant="email" if proposal_type == "email_draft" else "approval",
            draft=DraftPresentation(
                channel="email",
                status="ready_to_send",
                to=proposal.get("to") or None,
                cc=list(proposal.get("cc") or []),
                subject=proposal.get("subject") or None,
                body=proposal.get("body") or None,
                call_to_action="Send this now or discard it.",
            ) if proposal_type == "email_draft" else None,
            decision=None if proposal_type == "email_draft" else DecisionPresentation(
                decision_summary=summary,
                recommended_option=_gate_options(proposal_type)[0]["label"] if _gate_options(proposal_type) else None,
                impact_if_approved=_proposal_lines(proposal_type, proposal)[0] if _proposal_lines(proposal_type, proposal) else None,
                impact_if_rejected="The write action will not be executed.",
                options=[
                    DecisionOption(
                        label=option.get("label") or "Resolve",
                        decision=option.get("decision"),
                        mode=option.get("mode"),
                    )
                    for option in _gate_options(proposal_type)
                ],
            ),
        ),
        metadata={
            "interaction_id": interaction.id,
            "query": payload.message,
            "timestamp": interaction.timestamp,
            "gate": normalize_gate_metadata({
                "gate_type": "HUMAN_APPROVAL",
                "reason": reason,
                "options": _gate_options(proposal_type),
                "context": {"proposal_type": proposal_type, "proposal": proposal},
            }),
            "approval": build_approval_metadata(
                status="pending",
                stage="direct_action",
                gate={
                    "gate_type": "HUMAN_APPROVAL",
                    "reason": reason,
                    "options": _gate_options(proposal_type),
                    "context": {"proposal_type": proposal_type, "proposal": proposal},
                },
            ),
            "action_intent": proposal_type,
        },
    )


def _build_calendar_clarification_response(
    payload: AssistantQueryRequest,
    interaction: SessionInteraction,
    proposal: dict[str, Any],
) -> AssistantMessageResponse:
    return AssistantMessageResponse(
        conversation_id=payload.conversation_id,
        message_id=f"msg_{interaction.id}",
        workflow_type=WorkflowType.CALENDAR_BRIEFING,
        response_type="report",
        status="completed",
        answer=AnswerPayload(
            title="Calendar Scheduling",
            summary=f"I can line up {proposal.get('display_day', 'that meeting')} at {proposal.get('display_time', 'the requested time')}, but I still need {', '.join(proposal.get('missing_fields', []))}.",
            sections=[
                AnswerSection(label="What I Need", items=proposal.get("missing_fields", [])),
                AnswerSection(label="Suggested Reply", items=["For example: 'Call it Finance weekly check-in'"]),
            ],
        ),
        trust=TrustMetadata(
            confidence="high",
            confidence_score=0.92,
            assumptions=["Calendar write automation is waiting on the missing meeting details."],
            open_questions=["What would you like me to call the meeting?"],
            data_quality="high",
            calculation_used=False,
            missing_context=proposal.get("missing_fields", []),
        ),
        sources=[],
        artifacts=[],
        presentation=MessagePresentation(
            mode="decision",
            variant="clarification",
            decision=DecisionPresentation(
                decision_summary=f"I still need {', '.join(proposal.get('missing_fields', []))} before I can prepare this calendar action.",
                impact_if_rejected="The calendar action cannot be created until the missing details are provided.",
            ),
        ),
        metadata={"interaction_id": interaction.id, "query": payload.message, "timestamp": interaction.timestamp},
    )


def _build_email_clarification_response(
    payload: AssistantQueryRequest,
    interaction: SessionInteraction,
    proposal: dict[str, Any],
) -> AssistantMessageResponse:
    return AssistantMessageResponse(
        conversation_id=payload.conversation_id,
        message_id=f"msg_{interaction.id}",
        workflow_type=WorkflowType.EMAIL_INGESTION,
        response_type="report",
        status="completed",
        answer=AnswerPayload(
            title="Email Drafting",
            summary=f"I can prepare the draft, but I still need {', '.join(proposal.get('missing_fields', []))}.",
            sections=[
                AnswerSection(label="What I Need", items=proposal.get("missing_fields", [])),
                AnswerSection(label="Suggested Reply", items=["For example: \"Send an email to jane@company.com subject 'Board follow-up' saying we should meet next week.\""]),
            ],
        ),
        trust=TrustMetadata(
            confidence="high",
            confidence_score=0.92,
            assumptions=["Draft creation is waiting on the missing email details."],
            open_questions=["Who is the recipient, what is the subject, and what should the message say?"],
            data_quality="high",
            calculation_used=False,
            missing_context=proposal.get("missing_fields", []),
        ),
        sources=[],
        artifacts=[],
        presentation=MessagePresentation(
            mode="draft",
            variant="clarification",
            draft=DraftPresentation(
                channel="email",
                status="needs_input",
                to=proposal.get("to") or None,
                subject=proposal.get("subject") or None,
                body=proposal.get("body") or None,
                call_to_action="Provide the missing email details to prepare the draft.",
            ),
        ),
        metadata={"interaction_id": interaction.id, "query": payload.message, "timestamp": interaction.timestamp},
    )


def _build_direct_success_response(
    *,
    interaction: SessionInteraction,
    workflow_type: str,
    title: str,
    summary: str,
    details: list[str],
    link: Optional[str],
    draft_payload: Optional[dict[str, Any]] = None,
    approval_record: Optional[dict[str, Any]] = None,
) -> AssistantMessageResponse:
    sections = [AnswerSection(label="Details", items=details)]
    if link:
        sections.append(AnswerSection(label="Open Link", items=[link]))
    return AssistantMessageResponse(
        conversation_id=_conversation_id_for_interaction(interaction),
        message_id=f"msg_{interaction.id}",
        workflow_type=workflow_type,
        response_type="report",
        status="completed",
        answer=AnswerPayload(title=title, summary=summary, sections=sections),
        trust=TrustMetadata(
            confidence="high",
            confidence_score=0.96,
            assumptions=[],
            open_questions=[],
            data_quality="high",
            calculation_used=False,
            missing_context=[],
        ),
        sources=[],
        artifacts=[],
        presentation=MessagePresentation(
            mode="draft" if workflow_type == WorkflowType.EMAIL_INGESTION else "artifact",
            variant="email" if workflow_type == WorkflowType.EMAIL_INGESTION else "calendar",
            draft=DraftPresentation(
                channel="email",
                status="sent" if "Sent" in title else "drafted",
                to=(draft_payload or {}).get("to") or _extract_email_detail(details, "To") or _extract_to_from_summary(summary),
                subject=(draft_payload or {}).get("subject") or _extract_subject_from_summary(summary),
                body=(draft_payload or {}).get("body"),
                call_to_action="Review the draft in your mailbox before sending." if "Draft" in title else None,
            ) if workflow_type == WorkflowType.EMAIL_INGESTION else None,
        ),
        metadata={
            "interaction_id": interaction.id,
            "query": interaction.query,
            "timestamp": interaction.timestamp,
            "gate": None,
            "approval": build_approval_metadata_from_record(
                stage="direct_action",
                record={
                    **(approval_record or {}),
                    "decision": (approval_record or {}).get("decision") or "approve",
                    "mode": (approval_record or {}).get("mode") or ("send" if "Sent" in title else "draft" if workflow_type == WorkflowType.EMAIL_INGESTION else None),
                },
            ) if approval_record else build_approval_metadata(
                status="approved",
                stage="direct_action",
                decision="approve",
                mode="send" if "Sent" in title else "draft" if workflow_type == WorkflowType.EMAIL_INGESTION else None,
            ),
        },
    )


def _build_direct_rejection_response(
    interaction: SessionInteraction,
    proposal_type: str,
    note: Optional[str],
    approval_record: Optional[dict[str, Any]] = None,
) -> AssistantMessageResponse:
    is_email_draft = proposal_type == "email_draft"
    return AssistantMessageResponse(
        conversation_id=_conversation_id_for_interaction(interaction),
        message_id=f"msg_{interaction.id}",
        workflow_type=WorkflowType.CALENDAR_BRIEFING if proposal_type == "calendar_create" else WorkflowType.EMAIL_INGESTION,
        response_type="report",
        status="failed",
        answer=AnswerPayload(
            title="Draft Discarded" if is_email_draft else "Approval Declined",
            summary="The draft was discarded. No email was sent." if is_email_draft else "The proposed write action was not approved.",
            sections=[],
        ),
        trust=TrustMetadata(
            confidence="low",
            confidence_score=0.2,
            assumptions=[],
            open_questions=["Do you want to revise the request and try again?"],
            data_quality="high",
            calculation_used=False,
            missing_context=["Approval declined"],
        ),
        sources=[],
        artifacts=[],
        presentation=MessagePresentation(
            mode="draft" if is_email_draft else "decision",
            variant="discarded" if is_email_draft else "rejected",
            draft=DraftPresentation(
                channel="email",
                status="discarded",
                call_to_action=None,
            ) if is_email_draft else None,
            decision=None if is_email_draft else DecisionPresentation(
                decision_summary="The proposed write action was not approved.",
                impact_if_rejected="No write action was taken.",
            ),
        ),
        metadata={
            "interaction_id": interaction.id,
            "query": interaction.query,
            "timestamp": interaction.timestamp,
            "gate": None,
            "approval": build_approval_metadata_from_record(
                stage="direct_action",
                record={
                    **(approval_record or {}),
                    "decision": (approval_record or {}).get("decision") or "reject",
                    "note": (approval_record or {}).get("note") or note,
                },
            ) if approval_record else build_approval_metadata(
                status="rejected",
                stage="direct_action",
                decision="reject",
                note=note,
            ),
            "approval_note": note,
        },
    )


def _persist_direct_response(
    interaction_id: int | None,
    response: AssistantMessageResponse,
    *,
    status: str,
    gate_type: Optional[str],
    context: Optional[dict[str, Any]],
) -> None:
    persist_interaction_state(
        interaction_id,
        status=status,
        current_stage="direct_action",
        response=serialize_interaction_response(response),
        gate_type=gate_type,
        context=context,
    )


def _parse_context(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _update_pending_action_status(
    *,
    interaction: SessionInteraction,
    current_user: User,
    action_id: Optional[str],
    status: str,
) -> None:
    if not action_id:
        return
    conversation_id = _conversation_id_for_interaction(interaction)
    live_context = get_or_create_live_context(current_user.ceo_id, conversation_id).model_dump(mode="json")
    merged = merge_pending_actions(
        existing=live_context.get("pending_actions") if isinstance(live_context, dict) else [],
        updates=[{"action_id": action_id, "status": status}],
    )
    update_live_context(
        conversation_id,
        ceo_id=current_user.ceo_id,
        pending_actions=merged,
    )


async def _build_calendar_proposal(
    message: str,
    *,
    history: List[dict] | None = None,
    unified_memory: Dict[str, Any] | None = None,
    action_signals: Any | None = None,
    typed_requirements: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    if not _typed_requirements_allow_channel(typed_requirements, channel="calendar"):
        return None
    visible_message = extract_visible_request_text(message)
    lowered = visible_message.lower()
    working_memory = (unified_memory or {}).get("working_memory") or {}
    session_memory = (unified_memory or {}).get("session_memory") or {}
    explicit_calendar_intent = bool(action_signals.calendar_action) if action_signals is not None else (
        any(k in lowered for k in ("schedule", "add", "create", "book", "put"))
        and any(k in lowered for k in ("calendar", "meeting", "event"))
    )
    memory_signals_calendar = (
        str(working_memory.get("workflow_preference") or "") == WorkflowType.SCHEDULE_PLANNING
        and any(
            k in lowered
            for k in (
                "calendar",
                "meeting",
                "book it",
                "put it on the books",
                "line it up",
                "call it",
            )
        )
    )
    if not (explicit_calendar_intent or memory_signals_calendar):
        return None
    parsed = _parse_schedule_request(visible_message)
    if not parsed.get("title"):
        parsed["title"] = (
            session_memory.get("previous_response_title")
            or ((working_memory.get("deliverable") or {}).get("artifact_type") if isinstance(working_memory.get("deliverable"), dict) else "")
            or ""
        )
    # If key fields are missing and we have conversation history, try LLM resolution
    if history and (not parsed.get("title") or not parsed.get("starts_at")):
        enriched = await _llm_resolve_calendar_fields(message, parsed, history)
        if enriched:
            parsed.update({k: v for k, v in enriched.items() if v and not parsed.get(k)})
    missing_fields = []
    if not parsed.get("title"):
        missing_fields.append("meeting title")
    if not parsed.get("starts_at"):
        missing_fields.append("meeting time")
    parsed["missing_fields"] = missing_fields
    return parsed


async def _build_email_proposal(
    message: str,
    *,
    history: List[dict] | None = None,
    unified_memory: Dict[str, Any] | None = None,
    action_signals: Any | None = None,
    typed_requirements: dict[str, Any] | None = None,
) -> Optional[dict[str, Any]]:
    if not _typed_requirements_allow_channel(typed_requirements, channel="email"):
        return None
    visible_message = extract_visible_request_text(message)
    lowered = visible_message.lower()
    working_memory = (unified_memory or {}).get("working_memory") or {}
    session_memory = (unified_memory or {}).get("session_memory") or {}
    resolved_action_reference = working_memory.get("resolved_action_reference") or {}
    if isinstance(resolved_action_reference, dict) and resolved_action_reference.get("action_type") == "send_email":
        proposal = dict(resolved_action_reference.get("proposal") or {})
        proposal.setdefault("to", "")
        proposal.setdefault("subject", "")
        proposal.setdefault("body", "")
        proposal.setdefault("cc", [])
        proposal["missing_fields"] = [
            field
            for field, value in (
                ("recipient email", proposal.get("to")),
                ("email subject", proposal.get("subject")),
                ("email body", proposal.get("body")),
            )
            if not value
        ]
        return proposal
    is_delegation = "delegate" in lowered
    is_explicit_email = bool(action_signals.email_action) if action_signals is not None else (("send" in lowered or "draft" in lowered) and ("email" in lowered or "mail" in lowered))
    if str((working_memory.get("deliverable") or {}).get("kind") or "") == "email" and bool(working_memory.get("write_action_requested")):
        is_explicit_email = True
    if (
        not is_explicit_email
        and any(
            isinstance(action, dict) and action.get("action_type") == "send_email"
            for action in (session_memory.get("pending_actions") or [])
        )
        and any(token in lowered for token in ("send it", "send that email", "send the email"))
    ):
        is_explicit_email = True
    if not is_explicit_email and not is_delegation:
        return None
    to_match = re.search(r"\bto\s+([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b", visible_message, re.IGNORECASE)
    subject_match = re.search(r"\bsubject\s+['\"]([^'\"]+)['\"]", visible_message, re.IGNORECASE)
    body_match = re.search(r"\b(?:saying|body)\s+['\"]([^'\"]+)['\"]", visible_message, re.IGNORECASE)
    proposal = {
        "to": to_match.group(1) if to_match else "",
        "subject": subject_match.group(1) if subject_match else "",
        "body": body_match.group(1) if body_match else "",
        "cc": [],
    }
    # If key fields are missing, try LLM resolution.
    # Use history when available; the message itself may carry [Context: ...] with enough signal.
    has_context = "[Context:" in message
    if (history or has_context) and (not proposal["to"] or not proposal["subject"] or not proposal["body"]):
        enriched = await _llm_resolve_email_fields(message, proposal, history or [], is_delegation=is_delegation)
        if enriched:
            proposal.update({k: v for k, v in enriched.items() if v and not proposal.get(k)})
    missing_fields = []
    if not proposal["to"]:
        missing_fields.append("recipient email")
    if not proposal["subject"]:
        missing_fields.append("email subject")
    if not proposal["body"]:
        missing_fields.append("email body")
    proposal["missing_fields"] = missing_fields
    return proposal


def _requires_analysis_before_action(
    *,
    message: str,
    working_memory: dict[str, Any],
    resolved_action_reference: dict[str, Any] | None,
) -> bool:
    """Keep mixed analysis→execution requests on report generation until a concrete draft exists."""
    return classify_action_semantics(
        message=message,
        workflow_preference=str(working_memory.get("workflow_preference") or ""),
        task_topic=str(working_memory.get("task_topic") or ""),
        resolved_action_reference=resolved_action_reference,
    ).requires_analysis_before_action


def _typed_requirements_allow_channel(
    typed_requirements: dict[str, Any] | None,
    *,
    channel: str,
) -> bool:
    if not isinstance(typed_requirements, dict):
        return True
    if not bool(typed_requirements.get("contains_write_step")):
        return False
    write_channels = {
        str(item)
        for item in (typed_requirements.get("write_channels") or [])
        if str(item)
    }
    if not write_channels:
        return True
    return channel in write_channels


async def _llm_resolve_calendar_fields(
    message: str,
    partial: dict[str, Any],
    history: List[dict],
) -> dict[str, Any] | None:
    """Haiku call to resolve calendar fields from conversation context."""
    try:
        from src.core.llm import LLMClient  # local import to avoid circular deps
        llm = LLMClient(model=_DIRECT_ACTION_MODEL)
        if not llm.anthropic_async and not llm.openai_async:
            return None
        history_text = "\n".join(
            f"[{t.get('role', 'user')}]: {str(t.get('content', ''))[:300]}"
            for t in history[-3:]
        )
        system = (
            "You are a calendar event parser for an executive assistant. "
            "Given conversation history and a current message, extract calendar event details. "
            "Return ONLY JSON with these fields: "
            "{\"title\": str, \"day\": str, \"display_day\": str, "
            "\"starts_at\": \"ISO8601 or empty\", \"ends_at\": \"ISO8601 or empty\", "
            "\"display_time\": str, \"attendees\": [str]}. "
            "Use empty string for fields you cannot determine. Do NOT invent details."
        )
        prompt = (
            f"Conversation so far:\n{history_text}\n\n"
            f"Current request: {message}\n\n"
            f"Already extracted: {json.dumps({k: v for k, v in partial.items() if v})}\n\n"
            "Resolve any references (e.g. 'same meeting', 'that person') from the conversation "
            "and fill remaining fields."
        )
        raw = await llm.complete_async(prompt, system)
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            return json.loads(match.group(0))
    except Exception as exc:
        print(f"[DirectActions] Calendar LLM enrichment failed: {exc}")
    return None


async def _llm_resolve_email_fields(
    message: str,
    partial: dict[str, Any],
    history: List[dict],
    is_delegation: bool = False,
) -> dict[str, Any] | None:
    """Haiku call to resolve email fields from conversation context."""
    try:
        from src.core.llm import LLMClient  # local import to avoid circular deps
        llm = LLMClient(model=_DIRECT_ACTION_MODEL)
        if not llm.anthropic_async and not llm.openai_async:
            return None
        history_text = "\n".join(
            f"[{t.get('role', 'user')}]: {str(t.get('content', ''))[:300]}"
            for t in history[-3:]
        )
        if is_delegation:
            system = (
                "You are an email drafter for an executive assistant. "
                "The CEO wants to DELEGATE a task by email — compose the delegation message. "
                "Return ONLY JSON: {\"to\": \"email address or empty if unknown\", \"subject\": str, \"body\": str, \"cc\": [str]}. "
                "Draft a concise, professional delegation email body using the task context. "
                "Leave 'to' empty if the recipient is not specified — do NOT invent email addresses. "
                "Subject should be 'Delegating: <task topic>'."
            )
        else:
            system = (
                "You are an email drafter for an executive assistant. "
                "Given conversation history and a current message, extract email composition details. "
                "Return ONLY JSON: {\"to\": \"email address\", \"subject\": str, \"body\": str, \"cc\": [str]}. "
                "Use empty string for unknown fields. Do NOT invent recipient email addresses."
            )
        prompt = (
            f"Conversation so far:\n{history_text}\n\n"
            f"Current request: {message}\n\n"
            f"Already extracted: {json.dumps({k: v for k, v in partial.items() if v})}\n\n"
            "Resolve any references (e.g. 'same person', 'that follow-up') from the conversation "
            "and fill remaining fields."
        )
        raw = await llm.complete_async(prompt, system)
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            return json.loads(match.group(0))
    except Exception as exc:
        print(f"[DirectActions] Email LLM enrichment failed: {exc}")
    return None


def _parse_schedule_request(message: str) -> dict[str, Any]:
    visible_message = extract_visible_request_text(message)
    lowered = visible_message.lower()
    day = ""
    for marker in ("today", "tomorrow", "tommroow", "next week", "monday", "tuesday", "wednesday", "thursday", "friday"):
        if marker in lowered:
            day = "tomorrow" if marker == "tommroow" else marker
            break
    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", lowered)
    starts_at = ""
    ends_at = ""
    display_time = ""
    if day and time_match:
        starts_at, ends_at, display_time = _resolve_schedule_window(day, time_match.group(1), time_match.group(2), time_match.group(3))
    title_match = re.search(r"(?:called|titled|about)\s+['\"]([^'\"]+)['\"]", visible_message, re.IGNORECASE)
    return {
        "day": day,
        "display_day": day or "the requested day",
        "display_time": display_time or "the requested time",
        "title": title_match.group(1) if title_match else "",
        "starts_at": starts_at,
        "ends_at": ends_at,
        "timezone": "UTC",
        "attendees": [],
    }


def _resolve_schedule_window(day: str, hour: str, minute: Optional[str], meridiem: str) -> tuple[str, str, str]:
    now = datetime.utcnow()
    target = now.replace(second=0, microsecond=0)
    if day == "tomorrow":
        target = target + timedelta(days=1)
    elif day in {"monday", "tuesday", "wednesday", "thursday", "friday"}:
        weekday_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4}
        days_ahead = (weekday_map[day] - target.weekday()) % 7
        days_ahead = 7 if days_ahead == 0 else days_ahead
        target = target + timedelta(days=days_ahead)
    hour_value = int(hour) % 12
    if meridiem.lower() == "pm":
        hour_value += 12
    minute_value = int(minute or "0")
    target = target.replace(hour=hour_value, minute=minute_value)
    end = target + timedelta(minutes=30)
    display_time = target.strftime("%-I:%M %p") if minute else target.strftime("%-I %p")
    return target.isoformat(), end.isoformat(), display_time.lower()


def _proposal_lines(proposal_type: str, proposal: dict[str, Any]) -> list[str]:
    if proposal_type == "calendar_create":
        return [
            f"Title: {proposal.get('title', 'Scheduled meeting')}",
            f"When: {proposal.get('display_day')} at {proposal.get('display_time')}",
        ]
    if proposal_type == "email_draft":
        return [
            f"To: {proposal.get('to')}",
            f"Subject: {proposal.get('subject')}",
        ]
    return []


def _approval_lines(proposal_type: str) -> list[str]:
    if proposal_type == "email_draft":
        return [
            "Approve as draft to create a draft in your connected mailbox.",
            "Send now to send the message immediately after approval.",
        ]
    return ["Approve to execute this write action on your connected account."]


def _extract_email_detail(details: list[str], prefix: str) -> str | None:
    for detail in details:
        if detail.lower().startswith(f"{prefix.lower()}:"):
            return detail.split(":", 1)[1].strip() or None
    return None


def _extract_to_from_summary(summary: str) -> str | None:
    match = re.search(r"\bto\s+([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\b", summary, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_subject_from_summary(summary: str) -> str | None:
    match = re.search(r"\bsubject\s+'([^']+)'", summary, re.IGNORECASE)
    return match.group(1) if match else None


def _gate_options(proposal_type: str) -> list[dict[str, Any]]:
    if proposal_type == "email_draft":
        return [
            {"label": "Send", "decision": "approve", "mode": "send"},
            {"label": "Discard", "decision": "reject"},
        ]
    return [
        {"label": "Approve", "decision": "approve"},
        {"label": "Decline", "decision": "reject"},
    ]


def _conversation_id_for_interaction(interaction: SessionInteraction) -> str:
    try:
        parsed = json.loads(interaction.response or "")
        if isinstance(parsed, dict) and parsed.get("conversation_id"):
            return str(parsed["conversation_id"])
    except Exception:
        pass
    return f"conv:{interaction.ceo_id}:primary"

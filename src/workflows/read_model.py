import json
import logging
import re
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from src.api.schemas import (
    AnswerPayload,
    AnswerSection,
    AssistantMessageResponse,
    ConversationResponse,
    MessagePresentation,
    SourceRef,
    TrustMetadata,
)
from src.core.database import engine, get_or_create_live_context, get_or_create_situational_profile
from src.core.models import SessionInteraction, User, WorkflowRun
from src.tools.artifact_tools import hydrate_stage_artifact_refs, hydrate_stage_artifacts, read_stage_artifact_metadata
from src.workflows.approval_envelope import (
    build_approval_metadata,
    build_approval_metadata_from_record,
    normalize_gate_metadata,
)
from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.request_planner import plan_request
from src.workflows.semantic_followups import SemanticContext
from src.workflows.workflow_contracts import (
    default_presentation_payload,
    workflow_response_type,
    workflow_title,
    workflow_type_from_presentation,
)


_HIDDEN_ARTIFACT_STAGES = {
    "planning",
    "synthesizer",
    "canvas_preview",
    "report_docx_preview",
    "report_pptx_preview",
    "analysis_spec",
}

CANONICAL_ENVELOPE_VERSION = 2
logger = logging.getLogger(__name__)


def get_default_conversation_id(ceo_id: str) -> str:
    return f"conv:{ceo_id}:primary"


def _strip_artifact_frontmatter(content: str) -> str:
    if not content:
        return ""
    return re.sub(r"^---\n.*?\n---\n+", "", content, flags=re.DOTALL).strip()


# Deprecated schedule aliases — map to the canonical type at read time.
# These values may be present in old persisted records but must never be
# produced by new writes.
_SCHEDULE_ALIASES: dict[str, str] = {
    "day_schedule_planning": "schedule_planning",
    "week_schedule_planning": "schedule_planning",
}


def _normalize_workflow_type(workflow_type: str) -> str:
    """Map deprecated workflow type aliases to their canonical equivalent."""
    return _SCHEDULE_ALIASES.get(workflow_type, workflow_type)


def _workflow_type_from_persisted_sources(
    interaction: SessionInteraction,
    workflow_run: Optional[WorkflowRun],
) -> str | None:
    workflow_type = getattr(workflow_run, "workflow_type", None) if workflow_run else None
    if workflow_type and not (_workflow_run_has_attachments(workflow_run) and workflow_type == "conversational"):
        return _normalize_workflow_type(workflow_type)

    if workflow_run:
        state_workflow_type = ((getattr(workflow_run, "state_data", None) or {}).get("workflow_type") or "").strip()
        if state_workflow_type:
            return _normalize_workflow_type(state_workflow_type)

    if workflow_run:
        response_data = getattr(workflow_run, "response_data", None)
        if isinstance(response_data, dict):
            response_workflow_type = _workflow_type_from_response_payload(response_data)
            if response_workflow_type:
                return response_workflow_type

    if not interaction.response:
        return None
    try:
        parsed = json.loads(interaction.response)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    if isinstance(parsed, dict):
        response_workflow_type = _workflow_type_from_response_payload(parsed)
        if response_workflow_type:
            return response_workflow_type
    return None


def _workflow_type_from_response_payload(payload: dict[str, Any]) -> str | None:
    response_workflow_type = str(payload.get("workflow_type") or "").strip()
    if response_workflow_type:
        return _normalize_workflow_type(response_workflow_type)

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata_workflow_type = str(metadata.get("workflow_type") or "").strip()
        if metadata_workflow_type:
            return _normalize_workflow_type(metadata_workflow_type)

    presentation = payload.get("presentation")
    if isinstance(presentation, dict):
        mode = str(presentation.get("mode") or "").strip()
        variant = str(presentation.get("variant") or "").strip()
        inferred = workflow_type_from_presentation(mode, variant)
        if inferred:
            return _normalize_workflow_type(inferred)
        inferred_mode = {
            "calendar": "calendar_briefing",
            "schedule": "schedule_planning",
        }.get(mode)
        if inferred_mode:
            return _normalize_workflow_type(inferred_mode)
    return None


def _workflow_run_metadata(workflow_run: Optional[WorkflowRun]) -> dict[str, Any]:
    if not workflow_run:
        return {}
    metadata = ((getattr(workflow_run, "state_data", None) or {}).get("metadata") or {})
    return metadata if isinstance(metadata, dict) else {}


def _workflow_run_has_attachments(workflow_run: Optional[WorkflowRun]) -> bool:
    metadata = _workflow_run_metadata(workflow_run)
    attachments = metadata.get("attachments")
    return bool(attachments)


def _semantic_context_from_response(interaction: SessionInteraction) -> SemanticContext | None:
    if not interaction.response:
        return None
    try:
        parsed = json.loads(interaction.response)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    trust = parsed.get("trust") if isinstance(parsed, dict) else {}
    raw_context = trust.get("semantic_context") if isinstance(trust, dict) else None
    if not isinstance(raw_context, dict):
        return None
    try:
        return SemanticContext.model_validate(raw_context)
    except Exception:
        return None


def _expects_canonical_envelope(workflow_run: Optional[WorkflowRun]) -> bool:
    metadata = _workflow_run_metadata(workflow_run)
    version = metadata.get("envelope_version")
    try:
        return int(version) >= CANONICAL_ENVELOPE_VERSION
    except (TypeError, ValueError):
        return False



def _infer_workflow_type(
    interaction: SessionInteraction,
    artifacts: Dict[str, str],
    workflow_run: Optional[WorkflowRun],
) -> str:
    persisted_workflow_type = _workflow_type_from_persisted_sources(interaction, workflow_run)
    if persisted_workflow_type:
        return persisted_workflow_type

    if _workflow_run_has_attachments(workflow_run):
        return "document_explanation"

    query = extract_visible_request_text(interaction.query).lower()
    semantic_plan = plan_request(query, has_attachments=_workflow_run_has_attachments(workflow_run))
    semantic_workflow_type = str(semantic_plan.direct_workflow or "").strip()
    if semantic_workflow_type and semantic_workflow_type != "report_generation":
        logger.info(
            "read_model.workflow inferred from planner interaction_id=%s workflow=%s",
            interaction.id,
            semantic_workflow_type,
        )
        return semantic_workflow_type

    logger.info(
        "read_model.workflow defaulted interaction_id=%s workflow=report_generation",
        interaction.id,
    )
    return "report_generation"


def _build_answer_payload(interaction: SessionInteraction, artifacts: Dict[str, str], workflow_type: str) -> AnswerPayload:
    cleaned_artifacts = {key: _strip_artifact_frontmatter(value) for key, value in artifacts.items()}
    primary_text = (
        cleaned_artifacts.get("synthesizer")
        or cleaned_artifacts.get("report_docx_preview")
        or cleaned_artifacts.get("report_pptx_preview")
        or cleaned_artifacts.get("canvas_preview")
        or interaction.response
        or ""
    )
    title = workflow_title(workflow_type)
    summary = primary_text.strip() or ("Working on your request." if interaction.status == "PENDING" else "No answer available yet.")

    sections: List[AnswerSection] = []
    if summary:
        sections.append(AnswerSection(label="Executive Summary", content=summary))

    if not sections:
        sections.append(AnswerSection(label="Status", content=summary))

    return AnswerPayload(title=title, summary=summary, sections=sections)


def _build_trust_metadata(interaction: SessionInteraction, artifacts: Dict[str, str]) -> TrustMetadata:
    has_answer_artifact = bool(artifacts.get("synthesizer"))
    confidence = "medium"
    confidence_score = 0.72
    data_quality = "medium"

    if interaction.status == "FAILED":
        confidence = "low"
        confidence_score = 0.2
        data_quality = "low"
    elif interaction.status == "PENDING":
        confidence = "low"
        confidence_score = 0.35
    elif has_answer_artifact:
        confidence = "high"
        confidence_score = 0.86
        data_quality = "high"

    missing_context: List[str] = []
    gate_metadata = _gate_metadata_from_interaction(interaction)
    if gate_metadata and gate_metadata.get("gate_type"):
        missing_context.append(str(gate_metadata["gate_type"]))

    assumptions = []
    open_questions = []
    assumptions.append("The answer is based on currently available company context.")

    if interaction.status != "COMPLETED":
        open_questions.append("The analysis is still in progress.")

    if gate_metadata:
        reason = gate_metadata.get("reason")
        if reason:
            open_questions.append(str(reason))
        elif gate_metadata.get("gate_type"):
            open_questions.append(f"Additional input may be required due to {str(gate_metadata['gate_type']).lower()}.")

    return TrustMetadata(
        confidence=confidence,
        confidence_score=confidence_score,
        assumptions=assumptions,
        open_questions=open_questions,
        data_quality=data_quality,
        calculation_used=False,
        missing_context=missing_context,
        semantic_context=_semantic_context_from_response(interaction),
    )


def _build_source_refs(artifacts: Dict[str, str]) -> List[SourceRef]:
    sources = [SourceRef(source_id="company_state", title="Company State", type="state")]
    for stage, content in artifacts.items():
        if not content:
            continue
        sources.append(
            SourceRef(
                source_id=f"artifact:{stage}",
                title=f"{stage.replace('_', ' ').title()} Artifact",
                type="artifact",
                snippet=_strip_artifact_frontmatter(content)[:240],
            )
        )
    return sources


def _visible_artifact_stages(artifacts: Dict[str, str]) -> List[str]:
    return [
        stage
        for stage in artifacts
        if stage not in _HIDDEN_ARTIFACT_STAGES and not stage.endswith("_preview")
    ]


def _artifact_noun(stage: str) -> str:
    return {
        "report_docx": "memo",
        "report_pptx": "deck",
        "analysis_xlsx": "workbook",
        "executive_canvas": "one-pager",
    }.get(stage, "artifact")


def _build_artifact_preamble(
    *,
    answer_title: str,
    artifact_types: List[str],
) -> str:
    title = (answer_title or "").strip()
    generic_titles = {
        workflow_title("report_generation"),
        workflow_title("document_explanation"),
        workflow_title("email_ingestion"),
        workflow_title("email_watcher"),
        workflow_title("calendar_briefing"),
        workflow_title("morning_brief"),
        workflow_title("schedule_planning"),
        workflow_title("meeting_prep"),
        workflow_title("weekly_recap"),
    }
    scoped_title = title if title and title not in generic_titles else ""

    if len(artifact_types) == 1:
        noun = _artifact_noun(artifact_types[0])
        if scoped_title:
            return f"I prepared the {noun} for {scoped_title}. It is ready below."
        return f"I prepared the {noun}. It is ready below."

    nouns = [_artifact_noun(stage) for stage in artifact_types[:3]]
    if len(nouns) == 2:
        joined = f"{nouns[0]} and {nouns[1]}"
    else:
        joined = ", ".join(nouns[:-1]) + f", and {nouns[-1]}"
    if scoped_title:
        return f"I prepared the {joined} for {scoped_title}. They are ready below."
    return f"I prepared the {joined}. They are ready below."


def _apply_artifact_first_presentation(
    response: AssistantMessageResponse,
    *,
    interaction: SessionInteraction,
    artifacts: Dict[str, str],
) -> None:
    if response.presentation is not None:
        return
    artifact_types = [artifact.artifact_type for artifact in response.artifacts or []]
    if not artifact_types:
        artifact_types = _visible_artifact_stages(artifacts)
    if not artifact_types:
        return

    presentation = response.presentation or MessagePresentation()
    if not presentation.preamble:
        presentation.preamble = _build_artifact_preamble(
            answer_title=response.answer.title,
            artifact_types=artifact_types,
        )
    presentation.mode = "artifact"
    presentation.summary = None
    presentation.priorities = []
    presentation.recommended_actions = []
    presentation.risks = []
    presentation.details = []
    response.presentation = presentation


def _artifact_presentation_metadata(interaction_id: int, ceo_id: str, stage: str) -> Dict[str, Any]:
    raw = read_stage_artifact_metadata(interaction_id, ceo_id, stage)
    metadata: Dict[str, Any] = {}
    for key in ("theme_id", "template_id", "presentation_version"):
        value = raw.get(key)
        if value:
            metadata[key] = value
    return metadata


def _build_artifact_refs(
    interaction_id: int,
    ceo_id: str,
    artifacts: Dict[str, str],
    artifact_refs: Dict[str, str] | None = None,
) -> List[Dict[str, Any]]:
    available_stages = set(_visible_artifact_stages(artifacts))
    all_stage_refs = artifact_refs or {}
    for stage in all_stage_refs.keys():
        if stage not in _HIDDEN_ARTIFACT_STAGES and not stage.endswith("_preview"):
            available_stages.add(stage)

    preview_fallbacks = {
        "report_docx_preview": "report_docx",
        "report_pptx_preview": "report_pptx",
        "canvas_preview": "executive_canvas",
    }
    for preview_stage, base_stage in preview_fallbacks.items():
        if base_stage in available_stages:
            continue
        if preview_stage in artifacts or preview_stage in all_stage_refs:
            available_stages.add(preview_stage)
    return [
        {
            "artifact_type": stage,
            "artifact_id": f"interaction:{interaction_id}:{stage}",
            "label": stage.replace("_", " ").title(),
            "metadata": _artifact_presentation_metadata(interaction_id, ceo_id, stage),
        }
        for stage in sorted(available_stages, key=lambda item: item)
    ]


def _load_native_assistant_message(
    interaction: SessionInteraction,
    workflow_run: Optional[WorkflowRun] = None,
) -> Optional[AssistantMessageResponse]:
    persisted_run = workflow_run or _load_workflow_run(interaction.id)
    if persisted_run and persisted_run.response_data:
        try:
            response = AssistantMessageResponse(**persisted_run.response_data)
            _merge_read_model_metadata(response, interaction, persisted_run)
            return response
        except (TypeError, ValueError):
            pass

    if _expects_canonical_envelope(persisted_run):
        return None

    if not interaction.response:
        return None
    try:
        parsed = json.loads(interaction.response)
        if isinstance(parsed, dict) and "answer" in parsed and "trust" in parsed:
            response = AssistantMessageResponse(**parsed)
            _merge_read_model_metadata(response, interaction, persisted_run)
            return response
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def _build_canonical_recovery_response(
    *,
    interaction: SessionInteraction,
    current_user: User,
    conversation_id: Optional[str],
    artifacts: Dict[str, str],
    artifact_refs: Dict[str, str],
    persisted_run: WorkflowRun,
) -> AssistantMessageResponse:
    workflow_type = (
        _workflow_type_from_persisted_sources(interaction, persisted_run)
        or "report_generation"
    )
    response_type = workflow_response_type(workflow_type)
    status = "completed" if interaction.status == "COMPLETED" else "failed" if interaction.status == "FAILED" else "pending"
    summary = "The canonical assistant envelope for this run is unavailable."
    presentation_payload = default_presentation_payload(workflow_type, summary=summary)
    presentation = MessagePresentation(**presentation_payload) if presentation_payload else None

    response = AssistantMessageResponse(
        conversation_id=conversation_id or get_default_conversation_id(current_user.ceo_id),
        message_id=f"msg_{interaction.id}",
        workflow_type=workflow_type,
        response_type=response_type,
        status=status,
        answer=AnswerPayload(
            title=workflow_title(workflow_type),
            summary=summary,
            sections=[AnswerSection(label="Status", content=summary)],
        ),
        trust=TrustMetadata(
            confidence="low",
            confidence_score=0.2,
            assumptions=[],
            open_questions=["The persisted assistant response could not be reconstructed from the canonical workflow run."],
            data_quality="low",
            calculation_used=False,
            missing_context=["canonical_envelope_missing"],
        ),
        sources=_build_source_refs(artifacts),
        artifacts=_build_artifact_refs(interaction.id or 0, interaction.ceo_id, artifacts, artifact_refs),
        presentation=presentation,
        metadata={
            **_read_model_metadata(interaction, persisted_run),
            "read_model_status": "canonical_envelope_missing",
        },
    )
    _apply_artifact_first_presentation(response, interaction=interaction, artifacts=artifacts)
    return response


def _load_workflow_run(interaction_id: Optional[int]) -> Optional[WorkflowRun]:
    if interaction_id is None:
        return None
    with Session(engine) as session:
        statement = (
            select(WorkflowRun)
            .where(WorkflowRun.interaction_id == interaction_id)
            .order_by(WorkflowRun.id.desc())
        )
        return session.exec(statement).first()


def _planner_metadata_from_run(workflow_run: Optional[WorkflowRun]) -> Dict[str, object]:
    if not workflow_run:
        return {}
    metadata = ((workflow_run.state_data or {}).get("metadata") or {})
    planner_execution = metadata.get("planner_execution")
    if not isinstance(planner_execution, dict):
        return {}
    return {
        "planner_execution": {
            "execution_mode": planner_execution.get("execution_mode"),
            "planning_horizon": planner_execution.get("planning_horizon"),
            "executed_plan_steps": planner_execution.get("executed_plan_steps", []),
            "evidence_summary": planner_execution.get("evidence_summary", {}),
            "sparse_guidance": planner_execution.get("sparse_guidance"),
            "planning_window": planner_execution.get("planning_window"),
        }
    }


def _parse_missing_data_context(raw_context: Any) -> dict[str, Any]:
    if not raw_context:
        return {}
    if isinstance(raw_context, dict):
        return raw_context
    try:
        parsed = json.loads(raw_context)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _gate_metadata_from_interaction(interaction: SessionInteraction) -> dict[str, Any] | None:
    context = _parse_missing_data_context(interaction.missing_data_context)
    if interaction.gate_type:
        return normalize_gate_metadata(
            {
                "gate_type": interaction.gate_type,
                "reason": context.get("reason"),
                "options": context.get("options"),
                "context": context,
            }
        )
    return normalize_gate_metadata(context.get("gate"))


def _approval_metadata_from_sources(
    interaction: SessionInteraction,
    workflow_run: Optional[WorkflowRun],
    gate_metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    stage_name = interaction.current_stage
    if workflow_run:
        state_metadata = ((workflow_run.state_data or {}).get("metadata") or {})
        approvals = state_metadata.get("approvals") or {}
        approval_record = approvals.get(stage_name or "")
        if isinstance(approval_record, dict):
            return build_approval_metadata_from_record(stage=stage_name, record=approval_record)

    interaction_context = _parse_missing_data_context(interaction.missing_data_context)
    interaction_approval = interaction_context.get("approval")
    if isinstance(interaction_approval, dict):
        return build_approval_metadata_from_record(
            stage=interaction_approval.get("stage") or interaction.current_stage,
            record=interaction_approval,
        )

    normalized_status = (interaction.status or "").upper()
    if normalized_status == "AWAITING_INPUT" and gate_metadata:
        return build_approval_metadata(status="pending", stage=stage_name, gate=gate_metadata)
    return None


def _compact_live_context_summary(live_context: Any) -> dict[str, Any]:
    schedule = live_context.current_schedule if isinstance(live_context.current_schedule, dict) else {}
    blocks = schedule.get("blocks") or [] if isinstance(schedule, dict) else []
    meetings = schedule.get("meetings") or [] if isinstance(schedule, dict) else []
    deadlines = schedule.get("deadlines") or [] if isinstance(schedule, dict) else []
    resolved_clarifications = (
        dict(live_context.resolved_clarifications or {})
        if hasattr(live_context, "resolved_clarifications")
        else {}
    )
    clarification_resolutions = (
        list(live_context.clarification_resolutions or [])
        if hasattr(live_context, "clarification_resolutions")
        else []
    )
    return {
        "turn_count": live_context.turn_count,
        "open_decisions": list(live_context.open_decisions or [])[:5],
        "open_commitments": list(live_context.open_commitments or [])[:5],
        "entities_in_play": list((live_context.entities_in_play or {}).keys())[:5],
        "resolved_clarifications": resolved_clarifications,
        "clarification_resolutions": clarification_resolutions[-5:],
        "current_schedule": {
            "turn": schedule.get("turn") if isinstance(schedule, dict) else None,
            "block_titles": [
                str(block.get("title") or "").strip()
                for block in blocks[:5]
                if isinstance(block, dict) and str(block.get("title") or "").strip()
            ],
            "meeting_titles": [
                str(meeting.get("title") or "").strip()
                for meeting in meetings[:5]
                if isinstance(meeting, dict) and str(meeting.get("title") or "").strip()
            ],
            "deadline_count": len(deadlines) if isinstance(deadlines, list) else 0,
        },
        "updated_at": getattr(live_context, "updated_at", None),
    }


def _compact_situational_profile_summary(situational: Any) -> dict[str, Any]:
    recurring_topics = situational.recurring_topics or []
    open_threads = situational.open_threads or []
    return {
        "operating_mode": situational.operating_mode,
        "active_pressures": list(situational.active_pressures or [])[:5],
        "recurring_topics": [
            topic.get("topic")
            for topic in recurring_topics[:5]
            if isinstance(topic, dict) and topic.get("topic")
        ],
        "open_threads": [
            thread.get("thread")
            for thread in open_threads[:5]
            if isinstance(thread, dict) and thread.get("thread")
        ],
        "relationship_obligations": list(situational.relationship_obligations or [])[:5],
        "updated_at": getattr(situational, "updated_at", None),
    }


def _read_model_metadata(
    interaction: SessionInteraction,
    workflow_run: Optional[WorkflowRun],
) -> Dict[str, object]:
    gate_metadata = _gate_metadata_from_interaction(interaction)
    approval_metadata = _approval_metadata_from_sources(interaction, workflow_run, gate_metadata)
    metadata: Dict[str, object] = {
        "interaction_id": interaction.id,
        "current_stage": interaction.current_stage,
        "query": extract_visible_request_text(interaction.query),
        "timestamp": interaction.timestamp,
        "gate": gate_metadata,
        "approval": approval_metadata,
    }
    conversation_id = None
    if interaction.response:
        try:
            parsed = json.loads(interaction.response)
            if isinstance(parsed, dict):
                conversation_id = parsed.get("conversation_id")
        except (json.JSONDecodeError, TypeError, ValueError):
            conversation_id = None
    if conversation_id:
        live_context = get_or_create_live_context(interaction.ceo_id, conversation_id)
        live_context_summary = _compact_live_context_summary(live_context)
        metadata["live_context"] = live_context_summary
        metadata["resolved_clarifications"] = dict(live_context_summary.get("resolved_clarifications") or {})
        metadata["clarification_resolutions"] = list(live_context_summary.get("clarification_resolutions") or [])
    situational = get_or_create_situational_profile(interaction.ceo_id)
    metadata["situational_profile"] = _compact_situational_profile_summary(situational)
    metadata.update(_planner_metadata_from_run(workflow_run))
    return metadata


def _merge_read_model_metadata(
    response: AssistantMessageResponse,
    interaction: SessionInteraction,
    workflow_run: Optional[WorkflowRun],
) -> None:
    read_model_metadata = _read_model_metadata(interaction, workflow_run)
    existing_metadata = dict(response.metadata or {})
    for key, value in read_model_metadata.items():
        if key == "planner_execution":
            if value and not existing_metadata.get(key):
                existing_metadata[key] = value
            continue
        if existing_metadata.get(key) is None and value is not None:
            existing_metadata[key] = value
    response.metadata = existing_metadata


def _build_legacy_message_response(
    *,
    interaction: SessionInteraction,
    current_user: User,
    conversation_id: Optional[str],
    artifacts: Dict[str, str],
    artifact_refs: Dict[str, str],
    persisted_run: Optional[WorkflowRun],
) -> AssistantMessageResponse:
    workflow_type = _infer_workflow_type(interaction, artifacts, persisted_run)
    response_type = workflow_response_type(workflow_type)
    status = "completed" if interaction.status == "COMPLETED" else "failed" if interaction.status == "FAILED" else "pending"
    synthesizer_text = (
        _strip_artifact_frontmatter(artifacts.get("synthesizer") or "")
        or _strip_artifact_frontmatter(artifacts.get("report_docx_preview") or "")
        or _strip_artifact_frontmatter(artifacts.get("report_pptx_preview") or "")
        or _strip_artifact_frontmatter(artifacts.get("canvas_preview") or "")
        or (interaction.response or "")
    )
    presentation_payload = default_presentation_payload(workflow_type, summary=synthesizer_text.strip() or None)
    presentation = MessagePresentation(**presentation_payload) if presentation_payload else None

    response = AssistantMessageResponse(
        conversation_id=conversation_id or get_default_conversation_id(current_user.ceo_id),
        message_id=f"msg_{interaction.id}",
        workflow_type=workflow_type,
        response_type=response_type,
        status=status,
        answer=_build_answer_payload(interaction, artifacts, workflow_type),
        trust=_build_trust_metadata(interaction, artifacts),
        sources=_build_source_refs(artifacts),
        artifacts=_build_artifact_refs(interaction.id or 0, interaction.ceo_id, artifacts, artifact_refs),
        presentation=presentation,
        metadata=_read_model_metadata(interaction, persisted_run),
    )
    _apply_artifact_first_presentation(response, interaction=interaction, artifacts=artifacts)
    return response


def build_assistant_message_response(
    interaction: SessionInteraction,
    current_user: User,
    conversation_id: Optional[str] = None,
) -> AssistantMessageResponse:
    artifacts = hydrate_stage_artifacts(interaction.id, interaction.ceo_id) if interaction.id is not None else {}
    artifact_refs = hydrate_stage_artifact_refs(interaction.id, interaction.ceo_id) if interaction.id is not None else {}
    persisted_run = _load_workflow_run(interaction.id)
    native_message = _load_native_assistant_message(interaction, persisted_run)
    if native_message:
        if conversation_id and native_message.conversation_id != conversation_id:
            native_message.conversation_id = conversation_id
        _merge_read_model_metadata(native_message, interaction, persisted_run)
        _apply_artifact_first_presentation(native_message, interaction=interaction, artifacts=artifacts)
        logger.info(
            "read_model.build native interaction_id=%s workflow=%s response_type=%s",
            interaction.id,
            native_message.workflow_type,
            native_message.response_type,
        )
        return native_message

    if persisted_run and _expects_canonical_envelope(persisted_run):
        response = _build_canonical_recovery_response(
            interaction=interaction,
            current_user=current_user,
            conversation_id=conversation_id,
            artifacts=artifacts,
            artifact_refs=artifact_refs,
            persisted_run=persisted_run,
        )
        logger.info(
            "read_model.build canonical interaction_id=%s workflow=%s response_type=%s",
            interaction.id,
            response.workflow_type,
            response.response_type,
        )
        return response

    response = _build_legacy_message_response(
        interaction=interaction,
        current_user=current_user,
        conversation_id=conversation_id,
        artifacts=artifacts,
        artifact_refs=artifact_refs,
        persisted_run=persisted_run,
    )
    logger.info(
        "read_model.build legacy interaction_id=%s workflow=%s response_type=%s",
        interaction.id,
        response.workflow_type,
        response.response_type,
    )
    return response


def build_conversation_response(
    conversation_id: str,
    interactions: List[SessionInteraction],
    current_user: User,
) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=conversation_id or get_default_conversation_id(current_user.ceo_id),
        messages=[
            build_assistant_message_response(
                interaction,
                current_user=current_user,
                conversation_id=conversation_id,
            )
            for interaction in interactions
        ],
    )

from __future__ import annotations

import asyncio
from datetime import datetime
import time
from typing import Any, Optional

from sqlmodel import Session, select

from src.agents import (
    AgentInput,
    AgentOutput,
    BriefingAgent,
    ConversationalAgent,
    ExplainerAgent,
    PlannerAgent,
    ReportAgent,
    RoutingDecision,
)
from src.api.schemas import (
    AnswerPayload,
    AnswerSection,
    ArtifactRef,
    AssistantMessageResponse,
    AssistantQueryRequest,
    DecisionOption,
    DecisionPresentation,
    DraftPresentation,
    FinanceMetricChip,
    FinancePresentation,
    FinanceVisualPresentation,
    CalendarEventPresentation,
    CalendarPresentation,
    CanvasHeroMetric,
    CanvasPresentation,
    CanvasSectionPresentation,
    MessagePresentation,
    PresentationSection,
    SourceRef,
    TrustMetadata,
    WeeklyPlanBlock,
    WeeklyPlanMeeting,
    WeeklyPlanPresentation,
    WeeklyPlanWindow,
)
from src.core.database import engine
from src.core.models import SessionInteraction, User, WorkflowRun
from src.core.persona import AssistantPersona
from src.presentation.presentation_validator import normalize_and_validate_presentation_spec
from src.tools.base import ToolContext
from src.tools.artifact_tools import get_stage_artifact_path
from src.tools.registry import ToolRegistry, build_default_tool_registry
from src.workflows.assistant_common import artifact_ref_for_stage, build_assistant_workflow_state
from src.workflows.approval_envelope import (
    build_approval_metadata,
    build_approval_metadata_from_record,
    normalize_gate_metadata,
)
from src.workflows.approval_records import build_approval_record, build_pending_interaction_context, build_resolved_interaction_context
from src.workflows.context_loading import (
    build_context_stage_actions,
    collect_workflow_context_from_stage_outputs,
    finalize_context_stage,
    get_context_stage_definition,
)
from src.workflows.interaction_persistence import persist_interaction_state, serialize_interaction_response
from src.workflows.clarification_policy import ClarificationDecision
from src.workflows.question_ranking import rank_question_options
from src.workflows.types import WorkflowDefinition, WorkflowStepDefinition
from src.workflows.workflow_contracts import default_presentation_payload

from .artifact_actions import GeneratedArtifactAction, WriteArtifactAction
from .events import AgentEvent, ToolEvent, WorkflowEvent, WorkflowEventType
from .handlers import RuntimeStageHandlers
from .stage_handlers import StageFamily, StageHandlerRegistry
from .state import GateState, StageStatus, WorkflowState, WorkflowStatus

CANONICAL_ENVELOPE_VERSION = 2


class RuntimeEngine:
    def __init__(
        self,
        tools: ToolRegistry | None = None,
        stage_handlers: StageHandlerRegistry | None = None,
    ) -> None:
        self.tools = tools or build_default_tool_registry()
        self.agents = {
            "planner_agent": PlannerAgent(),
            "report_agent": ReportAgent(self.tools),
            "explainer_agent": ExplainerAgent(self.tools),
            "briefing_agent": BriefingAgent(self.tools),
            "conversational_agent": ConversationalAgent(),
        }
        self.runtime_stage_handlers = RuntimeStageHandlers(self)
        if stage_handlers is None:
            from .bootstrap import build_default_stage_handler_registry

            stage_handlers = build_default_stage_handler_registry(handlers=self.runtime_stage_handlers)
        self.stage_handlers = stage_handlers

    def _artifact_noun(self, artifact_type: str) -> str:
        return {
            "report_docx": "memo",
            "report_pptx": "deck",
            "analysis_xlsx": "workbook",
            "executive_canvas": "one-pager",
        }.get(artifact_type, "artifact")

    def _build_artifact_preamble(self, response: AssistantMessageResponse) -> str | None:
        artifact_types = [artifact.artifact_type for artifact in response.artifacts if artifact.status == "generated"]
        if not artifact_types:
            return None

        nouns = [self._artifact_noun(artifact_type) for artifact_type in artifact_types[:3]]
        title = (response.answer.title or "").strip()
        if len(nouns) == 1:
            return f"I prepared the {nouns[0]} for {title}. It is ready below." if title else f"I prepared the {nouns[0]}. It is ready below."
        if len(nouns) == 2:
            joined = f"{nouns[0]} and {nouns[1]}"
        else:
            joined = ", ".join(nouns[:-1]) + f", and {nouns[-1]}"
        return f"I prepared the {joined} for {title}. They are ready below." if title else f"I prepared the {joined}. They are ready below."

    def _reconcile_response_artifacts(
        self,
        *,
        response: AssistantMessageResponse,
        interaction_id: int,
        ceo_id: str,
    ) -> None:
        reconciled: list[ArtifactRef] = []
        seen_types: set[str] = set()
        for artifact in response.artifacts:
            if artifact.artifact_type in seen_types:
                continue
            path = get_stage_artifact_path(interaction_id, ceo_id, artifact.artifact_type)
            exists = bool(path and path.exists())
            if not exists and artifact.status != "failed":
                continue
            if exists and artifact.status == "planned":
                artifact.status = "generated"
                artifact.artifact_id = f"interaction:{interaction_id}:{artifact.artifact_type}"
            reconciled.append(artifact)
            seen_types.add(artifact.artifact_type)
        response.artifacts = reconciled

    def _append_artifact_warning(
        self,
        *,
        response: AssistantMessageResponse,
        artifact_stage: str,
        tool_name: str,
        error: str,
    ) -> None:
        warnings = list(response.metadata.get("artifact_warnings") or [])
        warnings.append(
            {
                "artifact_type": artifact_stage,
                "tool_name": tool_name,
                "error": error,
            }
        )
        response.metadata["artifact_warnings"] = warnings

    def _record_failed_artifact(
        self,
        *,
        response: AssistantMessageResponse,
        interaction_id: int,
        artifact_action: GeneratedArtifactAction,
        error: str,
    ) -> None:
        response.artifacts = [
            artifact
            for artifact in response.artifacts
            if artifact.artifact_type != artifact_action.artifact_stage
        ]
        response.artifacts.append(
            ArtifactRef(
                artifact_type=artifact_action.artifact_stage,
                artifact_id=f"interaction:{interaction_id}:{artifact_action.artifact_stage}",
                label=artifact_action.label or artifact_action.artifact_stage,
                format=artifact_action.format,
                status="failed",
                blocking_reason=error,
                metadata={"error": error},
            )
        )

    def _artifact_output_missing(self, path: Any) -> bool:
        try:
            return not path or not path.exists() or path.stat().st_size <= 0
        except OSError:
            return True

    def _finalize_artifact_response_presentation(self, response: AssistantMessageResponse) -> None:
        generated_artifacts = [artifact for artifact in response.artifacts if artifact.status == "generated"]
        if not generated_artifacts:
            return

        presentation = response.presentation or MessagePresentation()
        presentation.mode = "artifact"
        presentation.preamble = presentation.preamble or self._build_artifact_preamble(response)
        presentation.summary = None
        presentation.priorities = []
        presentation.recommended_actions = []
        presentation.risks = []
        presentation.details = []
        response.presentation = presentation

    def _default_presentation_payload(
        self,
        *,
        definition: WorkflowDefinition,
        summary: str | None = None,
    ) -> dict[str, Any] | None:
        clean_summary = str(summary or "").strip() or None
        return default_presentation_payload(definition.workflow_type, summary=clean_summary)

    def _merge_default_presentation(
        self,
        *,
        definition: WorkflowDefinition,
        presentation: dict[str, Any] | None,
        summary: str | None = None,
    ) -> dict[str, Any] | None:
        defaults = self._default_presentation_payload(definition=definition, summary=summary)
        if not defaults:
            return presentation
        if not presentation:
            return defaults

        merged = dict(presentation)
        for key, value in defaults.items():
            if merged.get(key) is None:
                merged[key] = value
        return merged

    async def run(
        self,
        definition: WorkflowDefinition,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        current_user: User,
        routing_decision: Optional[RoutingDecision] = None,
        extra_metadata: Optional[dict[str, Any]] = None,
    ) -> AssistantMessageResponse:
        workflow_state = build_assistant_workflow_state(
            interaction=interaction,
            current_user=current_user,
            workflow_type=definition.workflow_type,
            stage_names=[step.name for step in definition.steps],
        )
        if routing_decision:
            workflow_state.routing_decision = routing_decision.model_dump(mode="json")
        workflow_state.metadata.update(
            {
                "query": payload.message,
                "conversation_id": payload.conversation_id,
                "project_id": payload.project_id,
                "attachments": [attachment.model_dump() for attachment in payload.attachments],
                "options": payload.options.model_dump(),
            }
        )
        if extra_metadata:
            workflow_state.metadata.update(extra_metadata)
        workflow_state.metadata.setdefault("envelope_version", CANONICAL_ENVELOPE_VERSION)
        workflow_state.metadata.setdefault("semantic_source", "assistant_service")

        workflow_run = self._load_or_create_workflow_run(workflow_state)
        return await self._run_definition(
            definition=definition,
            payload=payload,
            interaction=interaction,
            current_user=current_user,
            workflow_state=workflow_state,
            workflow_run=workflow_run,
            routing_decision=routing_decision,
            record_start_event=True,
            start_step_name=definition.entry_step,
        )

    async def resume_workflow(
        self,
        *,
        interaction_id: int,
        current_user: User,
        decision: str,
        note: Optional[str] = None,
    ) -> AssistantMessageResponse:
        workflow_run, interaction = self._load_resumable_run(interaction_id=interaction_id, ceo_id=current_user.ceo_id)
        workflow_state = WorkflowState(**(workflow_run.state_data or {}))
        definition = self._definition_for_type(workflow_run.workflow_type)
        payload = self._payload_from_workflow_state(workflow_state)
        if not workflow_state.routing_decision:
            raise RuntimeError(
                "Cannot resume workflow: routing_decision was not persisted with the original run. "
                "Re-classification is not permitted on the resume path."
            )
        routing_decision = RoutingDecision(**workflow_state.routing_decision)

        if workflow_state.status != WorkflowStatus.AWAITING_INPUT or not workflow_state.gate:
            raise RuntimeError("Workflow is not waiting for approval input.")

        resolution = decision.lower()
        if resolution not in {"approve", "reject"}:
            raise RuntimeError(f"Unsupported approval decision: {decision}")

        self._resolve_gate(
            workflow_state=workflow_state,
            workflow_run=workflow_run,
            stage_name=workflow_state.current_stage or definition.entry_step,
            decision=resolution,
            note=note,
            actor=current_user.username,
        )

        if resolution == "reject":
            return self._build_rejected_gate_response(
                definition=definition,
                payload=payload,
                interaction=interaction,
                workflow_state=workflow_state,
                workflow_run=workflow_run,
                routing_decision=routing_decision,
                note=note,
            )

        return await self._run_definition(
            definition=definition,
            payload=payload,
            interaction=interaction,
            current_user=current_user,
            workflow_state=workflow_state,
            workflow_run=workflow_run,
            routing_decision=routing_decision,
            record_start_event=False,
            start_step_name=workflow_state.current_stage or definition.entry_step,
        )

    async def _run_definition(
        self,
        *,
        definition: WorkflowDefinition,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        current_user: User,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        routing_decision: Optional[RoutingDecision],
        record_start_event: bool,
        start_step_name: str,
    ) -> AssistantMessageResponse:
        if record_start_event:
            self._record_workflow_event(
                workflow_state,
                workflow_run,
                WorkflowEventType.WORKFLOW_STARTED,
                payload={"workflow_type": definition.workflow_type},
            )
        else:
            workflow_state.status = WorkflowStatus.RUNNING
            workflow_state.updated_at = datetime.now().isoformat()
            self._record_workflow_event(
                workflow_state,
                workflow_run,
                WorkflowEventType.WORKFLOW_RESUMED,
                stage=start_step_name,
                payload={"workflow_type": definition.workflow_type},
            )

        self._sync_interaction(interaction.id, status="PENDING", current_stage=start_step_name)

        response: Optional[AssistantMessageResponse] = None
        start_index = self._step_index(definition, start_step_name)

        try:
            for step in definition.steps[start_index:]:
                if step.name in definition.terminal_steps:
                    continue
                if self._stage_is_already_completed(workflow_state, step.name):
                    continue

                workflow_state.current_stage = step.name
                workflow_state.updated_at = datetime.now().isoformat()
                self._mark_stage_status(workflow_state, step.name, StageStatus.RUNNING)
                self._record_workflow_event(
                    workflow_state,
                    workflow_run,
                    WorkflowEventType.STAGE_STARTED,
                    stage=step.name,
                )
                self._sync_interaction(interaction.id, current_stage=step.name, status="PENDING")

                self._enforce_approval_if_required(step, workflow_state, workflow_run)

                stage_result = await self._execute_stage_family(
                    step=step,
                    workflow_state=workflow_state,
                    workflow_run=workflow_run,
                    payload=payload,
                    current_user=current_user,
                    routing_decision=routing_decision,
                )
                routing_decision = stage_result.get("routing_decision", routing_decision)
                agent_output = stage_result.get("agent_output")
                if stage_result.get("completed_without_agent_output"):
                    self._mark_stage_status(workflow_state, step.name, StageStatus.COMPLETED)
                    self._record_workflow_event(
                        workflow_state,
                        workflow_run,
                        WorkflowEventType.STAGE_COMPLETED,
                        stage=step.name,
                    )
                    continue

                if workflow_state.status == WorkflowStatus.AWAITING_INPUT:
                    response = self._build_pending_gate_response(
                        definition=definition,
                        payload=payload,
                        interaction=interaction,
                        agent_output=agent_output,
                        workflow_state=workflow_state,
                        routing_decision=routing_decision,
                    )
                    self._reconcile_response_artifacts(
                        response=response,
                        interaction_id=interaction.id or 0,
                        ceo_id=current_user.ceo_id,
                    )
                    self._finalize_artifact_response_presentation(response)
                    response.metadata["workflow_run_id"] = workflow_run.id
                    response.metadata["workflow_id"] = workflow_state.workflow_id
                    self._persist_workflow_run(workflow_run, workflow_state, response=response)
                    self._sync_interaction(
                        interaction.id,
                        status=workflow_state.status.value,
                        current_stage=workflow_state.current_stage,
                        response=serialize_interaction_response(response),
                        intent=routing_decision.intent.value if routing_decision else None,
                        gate_type=workflow_state.gate.gate_type if workflow_state.gate else None,
                        context=build_pending_interaction_context(
                            gate=workflow_state.gate.model_dump() if workflow_state.gate else None
                        ),
                        persist_gate=True,
                    )
                    return response

                if agent_output and agent_output.metadata.get("needs_clarification"):
                    response = self._build_clarification_response(
                        definition=definition,
                        payload=payload,
                        interaction=interaction,
                        agent_output=agent_output,
                        workflow_state=workflow_state,
                        routing_decision=routing_decision,
                    )
                    response.metadata["workflow_run_id"] = workflow_run.id
                    response.metadata["workflow_id"] = workflow_state.workflow_id
                    self._persist_workflow_run(workflow_run, workflow_state, response=response)
                    self._sync_interaction(
                        interaction.id,
                        status="COMPLETED",
                        current_stage=workflow_state.current_stage,
                        response=serialize_interaction_response(response),
                        intent=routing_decision.intent.value if routing_decision else None,
                        gate_type=None,
                        context=build_resolved_interaction_context(approval=None),
                        persist_gate=True,
                    )
                    return response

                self._apply_agent_metadata_updates(workflow_state, agent_output)
                workflow_state.stage_outputs[step.name] = agent_output.structured_output
                workflow_state.final_response = agent_output.content
                response = self._build_response(
                    definition=definition,
                    payload=payload,
                    interaction=interaction,
                    agent_output=agent_output,
                    workflow_state=workflow_state,
                    routing_decision=routing_decision,
                )
                self._execute_actions(
                    agent_output=agent_output,
                    workflow_state=workflow_state,
                    workflow_run=workflow_run,
                    current_user=current_user,
                    response=response,
                )
                self._reconcile_response_artifacts(
                    response=response,
                    interaction_id=interaction.id or 0,
                    ceo_id=current_user.ceo_id,
                )
                self._finalize_artifact_response_presentation(response)
                self._mark_stage_status(workflow_state, step.name, StageStatus.COMPLETED)
                self._record_workflow_event(
                    workflow_state,
                    workflow_run,
                    WorkflowEventType.STAGE_COMPLETED,
                    stage=step.name,
                )

            if response is None:
                raise RuntimeError(f"No response produced for workflow {definition.workflow_type}")

            workflow_state.status = WorkflowStatus.COMPLETED
            workflow_state.current_stage = "complete"
            workflow_state.updated_at = datetime.now().isoformat()
            self._mark_stage_status(workflow_state, "complete", StageStatus.COMPLETED)
            response.metadata["workflow_run_id"] = workflow_run.id
            response.metadata["workflow_id"] = workflow_state.workflow_id

            self._record_workflow_event(
                workflow_state,
                workflow_run,
                WorkflowEventType.WORKFLOW_COMPLETED,
                stage="complete",
            )
            self._persist_workflow_run(workflow_run, workflow_state, response=response)
            self._sync_interaction(
                interaction.id,
                status="COMPLETED",
                current_stage="complete",
                response=serialize_interaction_response(response),
                intent=routing_decision.intent.value if routing_decision else None,
                gate_type=None,
                context=build_resolved_interaction_context(
                    approval=workflow_state.metadata.get("approvals", {}).get(workflow_state.metadata.get("approval_stage") or "")
                ),
                persist_gate=True,
            )
            return response
        except Exception as exc:
            if workflow_state.status != WorkflowStatus.AWAITING_INPUT:
                failure_response = self._handle_failure_transition(
                    definition=definition,
                    workflow_state=workflow_state,
                    workflow_run=workflow_run,
                    payload=payload,
                    interaction=interaction,
                    routing_decision=routing_decision,
                    error=str(exc),
                )
                if failure_response is not None:
                    return failure_response
                workflow_state.status = WorkflowStatus.FAILED
            workflow_state.updated_at = datetime.now().isoformat()
            self._record_workflow_event(
                workflow_state,
                workflow_run,
                WorkflowEventType.WORKFLOW_FAILED if workflow_state.status == WorkflowStatus.FAILED else WorkflowEventType.GATE_TRIGGERED,
                stage=workflow_state.current_stage,
                payload={"error": str(exc)},
            )
            self._persist_workflow_run(workflow_run, workflow_state, error=str(exc))
            self._sync_interaction(
                interaction.id,
                status=workflow_state.status.value,
                current_stage=workflow_state.current_stage,
                response=str(exc),
                intent=routing_decision.intent.value if routing_decision else None,
                gate_type=workflow_state.gate.gate_type if workflow_state.gate else None,
                context=build_pending_interaction_context(
                    gate=workflow_state.gate.model_dump() if workflow_state.gate else None
                ) if workflow_state.gate else build_resolved_interaction_context(
                    approval=workflow_state.metadata.get("approvals", {}).get(workflow_state.metadata.get("approval_stage") or "")
                ),
                persist_gate=True,
            )
            raise

    def _build_response(
        self,
        definition: WorkflowDefinition,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        agent_output: AgentOutput,
        workflow_state: WorkflowState,
        routing_decision: Optional[RoutingDecision],
    ) -> AssistantMessageResponse:
        response_type = definition.metadata.get("response_type", "report")
        structured = agent_output.structured_output
        presentation_spec, presentation_quality = self._normalized_presentation_contract(agent_output)
        normalized_trust = self._normalize_trust_payload(structured.get("trust", {}))
        normalized_sources = self._normalize_sources(structured.get("sources", []))
        normalized_presentation = self._normalize_presentation_payload(
            structured.get("presentation") or agent_output.metadata.get("presentation")
        )
        normalized_presentation = self._merge_default_presentation(
            definition=definition,
            presentation=normalized_presentation,
            summary=structured.get("answer", {}).get("summary") or agent_output.summary,
        )
        if workflow_state.gate:
            normalized_presentation = {
                **(normalized_presentation or {}),
                "mode": "decision",
                "variant": "approval",
                "decision": self._decision_payload_for_gate(
                    answer=structured.get("answer", {}),
                    gate=workflow_state.gate.model_dump(),
                    summary=agent_output.summary,
                ),
            }
        planned_artifacts = self._planned_artifacts(
            interaction_id=interaction.id or 0,
            artifact_plan=agent_output.metadata.get("artifact_plan", []),
        )
        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=definition.workflow_type,
            response_type=response_type,
            status="completed",
            answer=AnswerPayload(**structured.get("answer", {})),
            trust=TrustMetadata(**normalized_trust),
            sources=[s for source in normalized_sources if (s := self._safe_source_ref(source)) is not None],
            artifacts=planned_artifacts,
            presentation=MessagePresentation(**normalized_presentation) if normalized_presentation else None,
            metadata={
                **self._base_response_metadata(
                    interaction=interaction,
                    workflow_state=workflow_state,
                    query=payload.message,
                ),
                "interaction_id": interaction.id,
                "agent": agent_output.agent_name,
                "workflow": definition.workflow_type,
                "routing_decision": routing_decision.model_dump() if routing_decision else None,
                "context_stages": definition.metadata.get("context_stages"),
                "finance_template": agent_output.metadata.get("finance_template"),
                "finance_digest": agent_output.metadata.get("finance_digest"),
                "primary_visual": agent_output.metadata.get("primary_visual"),
                "output_modality": agent_output.metadata.get("output_modality"),
                "finance_validation": agent_output.metadata.get("finance_validation"),
                "presentation_spec": presentation_spec,
                "presentation_quality": presentation_quality,
                "planner_execution": workflow_state.metadata.get("planner_execution"),
                "timing": self._response_timing_summary(workflow_state),
            },
        )

    def _build_pending_gate_response(
        self,
        definition: WorkflowDefinition,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        agent_output: AgentOutput,
        workflow_state: WorkflowState,
        routing_decision: Optional[RoutingDecision],
    ) -> AssistantMessageResponse:
        response_type = definition.metadata.get("response_type", "report")
        structured = agent_output.structured_output
        presentation_spec, presentation_quality = self._normalized_presentation_contract(agent_output)
        normalized_trust = self._normalize_trust_payload(structured.get("trust", {}))
        normalized_sources = self._normalize_sources(structured.get("sources", []))
        normalized_presentation = self._normalize_presentation_payload(
            structured.get("presentation") or agent_output.metadata.get("presentation")
        )
        normalized_presentation = self._merge_default_presentation(
            definition=definition,
            presentation=normalized_presentation,
            summary=structured.get("answer", {}).get("summary") or agent_output.summary,
        )
        planned_artifacts = self._planned_artifacts(
            interaction_id=interaction.id or 0,
            artifact_plan=agent_output.metadata.get("artifact_plan", []),
        )
        answer = (
            AnswerPayload(**structured.get("answer", {}))
            if structured.get("answer")
            else AnswerPayload(
                title="Awaiting Approval",
                summary=agent_output.summary or "This request requires human approval before completion.",
                sections=[],
            )
        )
        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=definition.workflow_type,
            response_type=response_type,
            status="pending",
            answer=answer,
            trust=TrustMetadata(**normalized_trust) if structured.get("trust") else TrustMetadata(),
            sources=[s for source in normalized_sources if (s := self._safe_source_ref(source)) is not None],
            artifacts=planned_artifacts,
            presentation=MessagePresentation(**normalized_presentation) if normalized_presentation else None,
            metadata={
                **self._base_response_metadata(
                    interaction=interaction,
                    workflow_state=workflow_state,
                    query=payload.message,
                    gate=workflow_state.gate.model_dump() if workflow_state.gate else None,
                    approval=build_approval_metadata(
                        status="pending",
                        stage=workflow_state.current_stage,
                        gate=workflow_state.gate.model_dump() if workflow_state.gate else None,
                    ),
                ),
                "interaction_id": interaction.id,
                "agent": agent_output.agent_name,
                "workflow": definition.workflow_type,
                "routing_decision": routing_decision.model_dump() if routing_decision else None,
                "finance_template": agent_output.metadata.get("finance_template"),
                "finance_digest": agent_output.metadata.get("finance_digest"),
                "primary_visual": agent_output.metadata.get("primary_visual"),
                "output_modality": agent_output.metadata.get("output_modality"),
                "finance_validation": agent_output.metadata.get("finance_validation"),
                "presentation_spec": presentation_spec,
                "presentation_quality": presentation_quality,
                "planner_execution": workflow_state.metadata.get("planner_execution"),
                "timing": self._response_timing_summary(workflow_state),
            },
        )

    def _normalized_presentation_contract(self, agent_output: AgentOutput) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        raw_spec = agent_output.metadata.get("presentation_spec")
        if not isinstance(raw_spec, dict):
            return None, agent_output.metadata.get("presentation_quality")
        try:
            spec, quality = normalize_and_validate_presentation_spec(raw_spec)
            return spec.model_dump(mode="json"), quality.model_dump(mode="json")
        except Exception:
            return raw_spec, agent_output.metadata.get("presentation_quality")

    def _build_clarification_response(
        self,
        *,
        definition: WorkflowDefinition,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        agent_output: AgentOutput,
        workflow_state: WorkflowState,
        routing_decision: Optional[RoutingDecision],
    ) -> AssistantMessageResponse:
        structured = agent_output.structured_output or {}
        preamble = (
            (structured.get("presentation") or {}).get("preamble")
            or "Before I can give you a useful answer on this, I need a bit more context."
        )
        clarification_options = [
            option for option in (structured.get("clarification_options") or agent_output.metadata.get("clarification_options") or [])
            if isinstance(option, dict)
        ][:3]
        gate = normalize_gate_metadata(
            {
                "gate_type": "CLARIFICATION_REQUIRED",
                "reason": preamble,
                "options": clarification_options,
                "context": {"original_query": agent_output.metadata.get("original_query", payload.message)},
            }
        )
        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=definition.workflow_type,
            response_type="clarification",
            status="completed",
            answer=AnswerPayload(
                title="",
                summary="",
                sections=[
                    AnswerSection(
                        label="Pick One",
                        items=[str(option.get("label")) for option in clarification_options if option.get("label")],
                    )
                ] if clarification_options else [],
            ),
            trust=TrustMetadata(confidence="low", confidence_score=0.0),
            sources=[],
            artifacts=[],
            presentation=MessagePresentation(
                mode="clarification",
                preamble=preamble,
                decision=DecisionPresentation(
                    decision_summary="Choose the interpretation that best matches what you want.",
                    recommended_option=clarification_options[0].get("label") if clarification_options else None,
                    impact_if_rejected="I may continue with weaker or misframed context.",
                    options=[
                        DecisionOption(
                            label=str(option.get("label") or "Choose"),
                            description=str(option.get("description")) if option.get("description") else None,
                        )
                        for option in clarification_options
                        if option.get("label")
                    ],
                ) if clarification_options else None,
            ),
            metadata={
                **self._base_response_metadata(
                    interaction=interaction,
                    workflow_state=workflow_state,
                    query=payload.message,
                ),
                "interaction_id": interaction.id,
                "original_query": agent_output.metadata.get("original_query", payload.message),
                "needs_clarification": True,
                "clarification_options": clarification_options,
                "gate": gate,
            },
        )

    def build_runner_clarification_response(
        self,
        *,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        workflow_type: str,
        clarification: ClarificationDecision,
        original_query: str,
    ) -> AssistantMessageResponse:
        clarification_options = [dict(option) for option in (clarification.options or []) if isinstance(option, dict)][:2]
        preamble = clarification.reason or "Before I continue, I need one detail from you."
        gate = normalize_gate_metadata(
            {
                "gate_type": "CLARIFICATION_REQUIRED",
                "reason": preamble,
                "options": clarification_options,
                "context": {
                    "original_query": original_query,
                    "question_kind": clarification.question_kind,
                    "blocking_gaps": clarification.blocking_gaps,
                },
            }
        )
        response = AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=workflow_type,
            response_type="clarification",
            status="completed",
            answer=AnswerPayload(
                title="One quick detail",
                summary=preamble,
                sections=[
                    AnswerSection(
                        label="Pick One",
                        items=[str(option.get("label")) for option in clarification_options if option.get("label")],
                    )
                ] if clarification_options else [],
            ),
            trust=TrustMetadata(
                confidence="low",
                confidence_score=0.0,
                missing_context=list(clarification.blocking_gaps),
                open_questions=[clarification.question] if clarification.question else [],
            ),
            sources=[],
            artifacts=[],
            presentation=MessagePresentation(
                mode="clarification",
                preamble=preamble + (
                    ("\n" + "\n".join(f"— {str(option.get('apply_text') or '').strip()}" for option in clarification_options if option.get("apply_text")))
                    if clarification_options else ""
                ),
                decision=DecisionPresentation(
                    decision_summary="Choose the interpretation that best matches what you want.",
                    recommended_option=clarification_options[0].get("label") if clarification_options else None,
                    impact_if_rejected="I may continue with weaker or misframed context.",
                    options=[
                        DecisionOption(
                            label=str(option.get("label") or "Choose"),
                            description=str(option.get("description")) if option.get("description") else None,
                        )
                        for option in clarification_options
                        if option.get("label")
                    ],
                ) if clarification_options else None,
            ),
            metadata={
                "interaction_id": interaction.id,
                "current_stage": "runner_clarification",
                "query": payload.message,
                "timestamp": interaction.timestamp,
                "gate": gate,
                "approval": None,
                "original_query": original_query,
                "needs_clarification": True,
                "clarification_options": clarification_options,
                "question_kind": clarification.question_kind,
                "blocking_gaps": clarification.blocking_gaps,
            },
        )
        self._sync_interaction(
            interaction.id,
            status="COMPLETED",
            current_stage="runner_clarification",
            response=serialize_interaction_response(response),
            gate_type="CLARIFICATION_REQUIRED",
            context={"gate": gate},
            persist_gate=True,
        )
        return response

    def _build_rejected_gate_response(
        self,
        *,
        definition: WorkflowDefinition,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        routing_decision: Optional[RoutingDecision],
        note: Optional[str],
    ) -> AssistantMessageResponse:
        response = AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=definition.workflow_type,
            response_type=definition.metadata.get("response_type", "report"),
            status="failed",
            answer=AnswerPayload(
                title="Approval Declined",
                summary="This request was not approved for completion.",
                sections=[],
            ),
            trust=TrustMetadata(
                confidence="low",
                confidence_score=0.2,
                assumptions=[],
                open_questions=["Do you want to revise the request and try again?"],
                data_quality="medium",
                calculation_used=False,
                missing_context=["Approval declined"],
            ),
            sources=[],
            artifacts=[],
            presentation=MessagePresentation(
                mode="decision",
                variant="approval",
                decision=DecisionPresentation(
                    decision_summary="This request was not approved for completion.",
                    impact_if_rejected="The workflow will remain incomplete until the request is revised or retried.",
                    options=[DecisionOption(label="Revise request", decision="reject")],
                ),
            ),
            metadata={
                **self._base_response_metadata(
                    interaction=interaction,
                    workflow_state=workflow_state,
                    query=payload.message,
                    gate=workflow_state.gate.model_dump() if workflow_state.gate else None,
                    approval=build_approval_metadata(
                        status="rejected",
                        stage=workflow_state.current_stage,
                        gate=workflow_state.gate.model_dump() if workflow_state.gate else None,
                        decision="reject",
                        note=note,
                    ),
                ),
                "interaction_id": interaction.id,
                "routing_decision": routing_decision.model_dump() if routing_decision else None,
                "approval_decision": "reject",
                "approval_note": note,
                "workflow_run_id": workflow_run.id,
                "workflow_id": workflow_state.workflow_id,
                "planner_execution": workflow_state.metadata.get("planner_execution"),
                "timing": self._response_timing_summary(workflow_state),
            },
        )
        self._persist_workflow_run(workflow_run, workflow_state, response=response, error="Approval declined")
        self._sync_interaction(
            interaction.id,
            status="FAILED",
            current_stage=workflow_state.current_stage,
            response=serialize_interaction_response(response),
            intent=routing_decision.intent.value if routing_decision else None,
        )
        return response

    async def _run_agent_step(
        self,
        *,
        agent: Any,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
        initial_context: Optional[dict[str, Any]] = None,
    ) -> AgentOutput:
        attempt = 0
        context: dict[str, Any] = dict(initial_context or {})
        last_error: Optional[str] = None

        while attempt <= step.retry_limit:
            started_at = time.monotonic()
            persona_data = context.get("persona") or {}
            persona_block = AssistantPersona(**persona_data).to_system_prompt_block() if persona_data else ""
            agent_input = AgentInput(
                workflow_state=workflow_state,
                stage=step.name,
                task_input=payload.message,
                context=context,
                system_prompt=persona_block or None,
                metadata={
                    "attachments": [attachment.model_dump() for attachment in payload.attachments],
                    "options": payload.options.model_dump(),
                    "conversation_id": payload.conversation_id,
                    "project_id": payload.project_id,
                    **step.metadata,
                },
            )

            self._record_agent_event(
                workflow_state,
                workflow_run,
                agent_name=agent.metadata.name,
                stage=step.name,
                status="invoked",
                payload={"attempt": attempt + 1},
            )

            agent_output = await agent.run(agent_input)
            duration_ms = round((time.monotonic() - started_at) * 1000, 2)
            self._record_agent_event(
                workflow_state,
                workflow_run,
                agent_name=agent.metadata.name,
                stage=step.name,
                status="completed" if agent_output.success else "failed",
                payload={
                    "summary": agent_output.summary,
                    "error": agent_output.error,
                    "attempt": attempt + 1,
                    "duration_ms": duration_ms,
                },
            )

            if not agent_output.success:
                last_error = agent_output.error or f"{agent.metadata.name} failed"
                if attempt >= step.retry_limit:
                    raise RuntimeError(last_error)
                await self._sleep_for_retry(step, workflow_state, step.name, attempt)
                attempt += 1
                continue

            gate_request = self._extract_gate_action(agent_output)
            if gate_request is not None:
                self._apply_gate_action(gate_request, workflow_state, workflow_run, step.name)
                return agent_output

            if self._has_tool_requests(agent_output):
                context_update = self._execute_tool_requests(
                    agent_output=agent_output,
                    workflow_state=workflow_state,
                    workflow_run=workflow_run,
                    current_user=current_user,
                )
                context.update(context_update)
                workflow_state.stage_outputs.setdefault(step.name, {})
                workflow_state.stage_outputs[step.name]["tool_context"] = context
                continue

            return agent_output

        raise RuntimeError(last_error or f"{agent.metadata.name} failed")

    def _execute_actions(
        self,
        agent_output: AgentOutput,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        current_user: User,
        response: AssistantMessageResponse,
    ) -> None:
        tool_context = ToolContext(
            workflow_id=workflow_state.workflow_id,
            interaction_id=workflow_state.interaction_id,
            ceo_id=current_user.ceo_id,
            company_name=current_user.company_name,
            stage=workflow_state.current_stage,
            metadata={
                "project_id": workflow_state.metadata.get("project_id"),
                "conversation_id": workflow_state.metadata.get("conversation_id"),
            },
        )
        for action in agent_output.actions:
            write_artifact_action = WriteArtifactAction.from_agent_action(action)
            if write_artifact_action is not None:
                result, tool_event = self.tools.invoke_with_event(
                    "write_artifact",
                    tool_context,
                    **write_artifact_action.write_kwargs(
                        interaction_id=workflow_state.interaction_id,
                        ceo_id=current_user.ceo_id,
                    ),
                )
                self._record_tool_event(workflow_state, workflow_run, tool_event)
                if result.success:
                    workflow_state.artifacts[write_artifact_action.artifact_stage] = (
                        f"interaction:{workflow_state.interaction_id}:{write_artifact_action.artifact_stage}"
                    )
                    if write_artifact_action.should_expose_in_response():
                        response.artifacts.append(
                            artifact_ref_for_stage(
                                workflow_state.interaction_id or 0,
                                write_artifact_action.artifact_stage,
                                **write_artifact_action.response_artifact_ref_kwargs(),
                            )
                        )
            elif action.action_type.value == "call_tool" and action.target in {"create_canvas", "create_docx_memo", "create_pptx_deck", "create_workbook"}:
                artifact_action = GeneratedArtifactAction.from_agent_action(action)
                if artifact_action is None:
                    continue
                output_path = get_stage_artifact_path(
                    workflow_state.interaction_id or 0,
                    current_user.ceo_id,
                    artifact_action.artifact_stage,
                )
                if not output_path:
                    continue
                try:
                    result, tool_event = self.tools.invoke_with_event(
                        artifact_action.tool_name,
                        tool_context,
                        output_path=str(output_path),
                        **artifact_action.tool_args,
                    )
                    self._record_tool_event(workflow_state, workflow_run, tool_event)
                except Exception as exc:
                    error = f"{artifact_action.tool_name} failed: {exc}"
                    self._append_artifact_warning(
                        response=response,
                        artifact_stage=artifact_action.artifact_stage,
                        tool_name=artifact_action.tool_name,
                        error=error,
                    )
                    self._record_failed_artifact(
                        response=response,
                        interaction_id=workflow_state.interaction_id or 0,
                        artifact_action=artifact_action,
                        error=error,
                    )
                    continue
                if not result.success or self._artifact_output_missing(output_path):
                    error = result.error or f"{artifact_action.tool_name} did not produce a usable file."
                    self._append_artifact_warning(
                        response=response,
                        artifact_stage=artifact_action.artifact_stage,
                        tool_name=artifact_action.tool_name,
                        error=error,
                    )
                    self._record_failed_artifact(
                        response=response,
                        interaction_id=workflow_state.interaction_id or 0,
                        artifact_action=artifact_action,
                        error=error,
                    )
                    continue

                workflow_state.artifacts[artifact_action.artifact_stage] = str(output_path)
                response.artifacts = [
                    artifact
                    for artifact in response.artifacts
                    if artifact.artifact_type != artifact_action.artifact_stage
                ]
                response.artifacts.append(
                    artifact_ref_for_stage(
                        workflow_state.interaction_id or 0,
                        artifact_action.artifact_stage,
                        **artifact_action.response_artifact_ref_kwargs(),
                    )
                )

                preview_write_kwargs = artifact_action.preview_write_kwargs(
                    interaction_id=workflow_state.interaction_id,
                    ceo_id=current_user.ceo_id,
                    agent_name=agent_output.agent_name,
                    result_metadata=result.metadata,
                )
                if preview_write_kwargs:
                    try:
                        preview_result, preview_event = self.tools.invoke_with_event(
                            "write_artifact",
                            tool_context,
                            **preview_write_kwargs,
                        )
                        self._record_tool_event(workflow_state, workflow_run, preview_event)
                        if preview_result.success:
                            preview_stage = str(preview_write_kwargs["stage"])
                            workflow_state.artifacts[preview_stage] = f"interaction:{workflow_state.interaction_id}:{preview_stage}"
                        else:
                            self._append_artifact_warning(
                                response=response,
                                artifact_stage=str(preview_write_kwargs["stage"]),
                                tool_name="write_artifact",
                                error=preview_result.error or "Preview artifact generation failed.",
                            )
                    except Exception as exc:
                        self._append_artifact_warning(
                            response=response,
                            artifact_stage=str(preview_write_kwargs["stage"]),
                            tool_name="write_artifact",
                            error=f"Preview artifact generation failed: {exc}",
                        )

    def _execute_tool_requests(
        self,
        *,
        agent_output: AgentOutput,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        current_user: User,
    ) -> dict[str, Any]:
        context_update: dict[str, Any] = {}
        tool_context = ToolContext(
            workflow_id=workflow_state.workflow_id,
            interaction_id=workflow_state.interaction_id,
            ceo_id=current_user.ceo_id,
            company_name=current_user.company_name,
            stage=workflow_state.current_stage,
            metadata={
                "project_id": workflow_state.metadata.get("project_id"),
                "conversation_id": workflow_state.metadata.get("conversation_id"),
            },
        )

        for action in agent_output.actions:
            if action.action_type.value != "call_tool" or not action.target:
                continue
            result_key = action.args.get("result_key") or action.target
            tool_kwargs = {key: value for key, value in action.args.items() if key != "result_key"}
            if action.target not in self.tools.list_tools():
                raise RuntimeError(f"Tool not registered: {action.target}")
            result, tool_event = self.tools.invoke_with_event(action.target, tool_context, **tool_kwargs)
            self._record_tool_event(workflow_state, workflow_run, tool_event)
            context_update[result_key] = self._normalize_tool_result(result)
            if not result.success and result.error:
                context_update[f"{result_key}_error"] = result.error
        return context_update

    def _execute_context_stage(
        self,
        *,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
    ) -> None:
        context_definition = get_context_stage_definition(workflow_state.workflow_type, step.name)
        aggregate_context = collect_workflow_context_from_stage_outputs(
            workflow_state.workflow_type,
            workflow_state.stage_outputs,
        )
        if workflow_state.metadata.get("event_payload"):
            aggregate_context.setdefault("event_payload", workflow_state.metadata["event_payload"])
        if workflow_state.metadata.get("planner_execution"):
            aggregate_context.setdefault("planner_execution", workflow_state.metadata["planner_execution"])
        if workflow_state.metadata.get("intent_state"):
            aggregate_context.setdefault("intent_state", workflow_state.metadata["intent_state"])
        if workflow_state.metadata.get("unified_memory"):
            aggregate_context.setdefault("unified_memory", workflow_state.metadata["unified_memory"])
        aggregate_context["task_input"] = payload.message
        workflow_state.stage_outputs.setdefault(step.name, {})
        if context_definition is not None:
            workflow_state.stage_outputs[step.name]["definition"] = context_definition.model_dump()

        actions = build_context_stage_actions(
            workflow_state.workflow_type,
            step.name,
            payload.message,
            {
                "project_id": workflow_state.metadata.get("project_id"),
                "project_context": aggregate_context.get("project_context"),
                "request_plan": workflow_state.metadata.get("request_plan"),
            },
        )
        if actions:
            stage_agent_output = AgentOutput(
                agent_name="runtime_context_loader",
                stage=step.name,
                success=True,
                actions=actions,
            )
            context_update = self._execute_tool_requests(
                agent_output=stage_agent_output,
                workflow_state=workflow_state,
                workflow_run=workflow_run,
                current_user=current_user,
            )
            workflow_state.stage_outputs[step.name]["tool_context"] = context_update
            aggregate_context.update(context_update)

        finalized = finalize_context_stage(
            workflow_type=workflow_state.workflow_type,
            stage_name=step.name,
            aggregate_context=aggregate_context,
            attachments=[attachment.model_dump() for attachment in payload.attachments],
        )
        if finalized:
            workflow_state.stage_outputs[step.name].update(finalized)

        workflow_state.metadata.setdefault("executed_context_stages", [])
        if step.name not in workflow_state.metadata["executed_context_stages"]:
            workflow_state.metadata["executed_context_stages"].append(step.name)

    def _has_tool_requests(self, agent_output: AgentOutput) -> bool:
        return any(
            action.action_type.value == "call_tool" and action.args.get("result_key")
            for action in agent_output.actions
        )

    def _extract_gate_action(self, agent_output: AgentOutput) -> Optional[Any]:
        for action in agent_output.actions:
            if action.action_type.value == "emit_gate":
                return action
        return None

    def _load_or_create_workflow_run(self, workflow_state: WorkflowState) -> WorkflowRun:
        with Session(engine) as session:
            statement = select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_state.workflow_id)
            workflow_run = session.exec(statement).first()
            if not workflow_run:
                workflow_run = WorkflowRun(
                    workflow_id=workflow_state.workflow_id,
                    interaction_id=workflow_state.interaction_id,
                    ceo_id=workflow_state.ceo_id,
                    workflow_type=workflow_state.workflow_type,
                    status=workflow_state.status.value,
                    current_stage=workflow_state.current_stage,
                    state_data=workflow_state.model_dump(),
                    event_log=[],
                )
                session.add(workflow_run)
                session.commit()
                session.refresh(workflow_run)
                return workflow_run

            workflow_run.status = workflow_state.status.value
            workflow_run.current_stage = workflow_state.current_stage
            workflow_run.updated_at = datetime.now().isoformat()
            workflow_run.state_data = workflow_state.model_dump()
            session.add(workflow_run)
            session.commit()
            session.refresh(workflow_run)
            return workflow_run

    def _load_resumable_run(self, *, interaction_id: int, ceo_id: str) -> tuple[WorkflowRun, SessionInteraction]:
        with Session(engine) as session:
            interaction = session.get(SessionInteraction, interaction_id)
            if not interaction or interaction.ceo_id != ceo_id:
                raise RuntimeError("Interaction not found.")
            statement = select(WorkflowRun).where(
                WorkflowRun.interaction_id == interaction_id,
                WorkflowRun.ceo_id == ceo_id,
            )
            workflow_run = session.exec(statement).first()
            if not workflow_run:
                raise RuntimeError("Workflow run not found.")
            session.expunge(interaction)
            session.expunge(workflow_run)
            return workflow_run, interaction

    def _persist_workflow_run(
        self,
        workflow_run: WorkflowRun,
        workflow_state: WorkflowState,
        response: Optional[AssistantMessageResponse] = None,
        error: Optional[str] = None,
    ) -> None:
        with Session(engine) as session:
            stored_run = session.get(WorkflowRun, workflow_run.id)
            if not stored_run:
                return
            stored_run.status = workflow_state.status.value
            stored_run.current_stage = workflow_state.current_stage
            stored_run.updated_at = datetime.now().isoformat()
            stored_run.completed_at = datetime.now().isoformat() if workflow_state.status in (
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
            ) else None
            stored_run.state_data = workflow_state.model_dump()
            stored_run.error = error
            if response is not None:
                stored_run.response_data = response.model_dump()
            session.add(stored_run)
            session.commit()
            session.refresh(stored_run)
            workflow_run.id = stored_run.id
            workflow_run.event_log = stored_run.event_log

    def _sync_interaction(
        self,
        interaction_id: Optional[int],
        *,
        status: Optional[str] = None,
        current_stage: Optional[str] = None,
        response: Optional[str] = None,
        intent: Optional[str] = None,
        gate_type: Any = None,
        context: Any = None,
        persist_gate: bool = False,
    ) -> None:
        kwargs: dict[str, Any] = {
            "status": status,
            "current_stage": current_stage,
            "response": response,
            "intent": intent,
        }
        if persist_gate:
            kwargs["gate_type"] = gate_type
            kwargs["context"] = context
        persist_interaction_state(interaction_id, **kwargs)

    def _base_response_metadata(
        self,
        *,
        interaction: SessionInteraction,
        workflow_state: WorkflowState,
        query: str,
        gate: dict[str, Any] | None = None,
        approval: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stage_name = workflow_state.current_stage or ""
        if approval is None:
            approval = build_approval_metadata_from_record(
                stage=stage_name or None,
                record=workflow_state.metadata.get("approvals", {}).get(stage_name),
            )
        return {
            "interaction_id": interaction.id,
            "current_stage": workflow_state.current_stage,
            "query": query,
            "timestamp": interaction.timestamp,
            "gate": normalize_gate_metadata(gate),
            "approval": approval,
            "envelope_version": workflow_state.metadata.get("envelope_version", CANONICAL_ENVELOPE_VERSION),
            "semantic_source": workflow_state.metadata.get("semantic_source", "assistant_service"),
        }

    def _record_workflow_event(
        self,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        event_type: WorkflowEventType,
        *,
        stage: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        event = WorkflowEvent(
            event_type=event_type,
            workflow_id=workflow_state.workflow_id,
            interaction_id=workflow_state.interaction_id,
            stage=stage,
            payload=payload or {},
        )
        self._append_run_event(workflow_run, event.model_dump())

    def _record_agent_event(
        self,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        *,
        agent_name: str,
        stage: str,
        status: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        event = AgentEvent(
            workflow_id=workflow_state.workflow_id,
            agent_name=agent_name,
            stage=stage,
            status=status,
            payload=payload or {},
        )
        workflow_state.agent_events.append(event.model_dump())
        self._append_run_event(workflow_run, event.model_dump())

    def _record_tool_event(
        self,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        event: ToolEvent,
    ) -> None:
        workflow_state.tool_events.append(event.model_dump())
        self._append_run_event(workflow_run, event.model_dump())

    def _response_timing_summary(self, workflow_state: WorkflowState) -> dict[str, Any]:
        stage_durations_ms: dict[str, float] = {}
        for stage in workflow_state.stages:
            if not stage.started_at or not stage.completed_at:
                continue
            try:
                started_at = datetime.fromisoformat(stage.started_at)
                completed_at = datetime.fromisoformat(stage.completed_at)
            except ValueError:
                continue
            stage_durations_ms[stage.name] = round((completed_at - started_at).total_seconds() * 1000, 2)

        tool_events = workflow_state.tool_events or []
        tool_breakdown = []
        for event in tool_events:
            payload = event.get("payload", {}) or {}
            metadata = payload.get("metadata", {}) or {}
            duration_ms = payload.get("duration_ms") or metadata.get("duration_ms")
            tool_breakdown.append(
                {
                    "tool_name": event.get("tool_name"),
                    "stage": event.get("stage"),
                    "status": event.get("status"),
                    "duration_ms": duration_ms,
                    "model": metadata.get("model"),
                    "tokens_used": metadata.get("tokens_used"),
                    "prompt_char_count": metadata.get("prompt_char_count"),
                    "response_model": metadata.get("response_model"),
                }
            )

        return {
            "stage_durations_ms": stage_durations_ms,
            "tool_breakdown": tool_breakdown,
            "total_tool_duration_ms": round(
                sum(
                    float(item.get("duration_ms") or 0)
                    for item in tool_breakdown
                    if item.get("duration_ms") is not None
                ),
                2,
            ),
        }

    def _append_run_event(self, workflow_run: WorkflowRun, payload: dict[str, Any]) -> None:
        with Session(engine) as session:
            stored_run = session.get(WorkflowRun, workflow_run.id)
            if not stored_run:
                return
            current_log = list(stored_run.event_log or [])
            current_log.append(payload)
            stored_run.event_log = current_log
            stored_run.updated_at = datetime.now().isoformat()
            session.add(stored_run)
            session.commit()
            session.refresh(stored_run)
            workflow_run.event_log = stored_run.event_log

    def _mark_stage_status(self, workflow_state: WorkflowState, stage_name: str, status: StageStatus) -> None:
        for stage in workflow_state.stages:
            if stage.name != stage_name:
                continue
            stage.status = status
            if status == StageStatus.RUNNING and not stage.started_at:
                stage.started_at = datetime.now().isoformat()
            if status == StageStatus.COMPLETED:
                if not stage.started_at:
                    stage.started_at = datetime.now().isoformat()
                stage.completed_at = datetime.now().isoformat()
            return

    async def _sleep_for_retry(
        self,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        stage_name: str,
        attempt: int,
    ) -> None:
        backoff = max(step.retry_backoff_seconds, 0)
        workflow_state.retries[stage_name] = attempt + 1
        if backoff:
            await asyncio.sleep(backoff)

    def _enforce_approval_if_required(
        self,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
    ) -> None:
        if not step.approval_required:
            return
        approvals = workflow_state.metadata.get("approvals", {})
        stage_approval = approvals.get(step.name, {})
        if stage_approval.get("decision") == "approve":
            return

        workflow_state.status = WorkflowStatus.AWAITING_INPUT
        workflow_state.gate = GateState(
            gate_type=step.approval_gate_type or "APPROVAL_REQUIRED",
            reason=f"Approval required before executing stage {step.name}.",
            options=[{"label": "Approve", "value": "APPROVE"}, {"label": "Reject", "value": "REJECT"}],
            context={"stage": step.name},
        )
        self._record_workflow_event(
            workflow_state,
            workflow_run,
            WorkflowEventType.GATE_TRIGGERED,
            stage=step.name,
            payload={"gate_type": step.approval_gate_type or "APPROVAL_REQUIRED"},
        )
        raise RuntimeError(f"Approval required for stage: {step.name}")

    def _apply_gate_action(
        self,
        action: Any,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        stage_name: str,
    ) -> None:
        workflow_state.status = WorkflowStatus.AWAITING_INPUT
        workflow_state.gate = GateState(
            gate_type=action.target or "HUMAN_APPROVAL",
            reason=action.args.get("reason"),
            options=[
                {"label": "Approve", "value": "APPROVE"},
                {"label": "Reject", "value": "REJECT"},
            ],
            context=action.args,
        )
        self._mark_stage_status(workflow_state, stage_name, StageStatus.BLOCKED)
        self._record_workflow_event(
            workflow_state,
            workflow_run,
            WorkflowEventType.GATE_TRIGGERED,
            stage=stage_name,
            payload=action.args,
        )

    def _resolve_gate(
        self,
        *,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        stage_name: str,
        decision: str,
        note: Optional[str],
        actor: str,
    ) -> None:
        resolved_at = datetime.now().isoformat()
        gate_payload = workflow_state.gate.model_dump() if workflow_state.gate else None
        if workflow_state.gate:
            workflow_state.gate.resolution = decision.upper()
            workflow_state.gate.resolved_at = resolved_at
        approvals = dict(workflow_state.metadata.get("approvals", {}))
        approvals[stage_name] = build_approval_record(
            stage=stage_name,
            gate=gate_payload,
            decision=decision,
            note=note,
            actor=actor,
            resolved_at=resolved_at,
        )
        workflow_state.metadata["approvals"] = approvals
        workflow_state.metadata["approval_status"] = decision
        workflow_state.metadata["approval_stage"] = stage_name
        self._record_workflow_event(
            workflow_state,
            workflow_run,
            WorkflowEventType.GATE_RESOLVED,
            stage=stage_name,
            payload={"decision": decision, "note": note, "actor": actor},
        )
        if decision == "approve":
            workflow_state.status = WorkflowStatus.RUNNING
            workflow_state.gate = None
            self._mark_stage_status(workflow_state, stage_name, StageStatus.RUNNING)
        else:
            workflow_state.status = WorkflowStatus.FAILED
            self._mark_stage_status(workflow_state, stage_name, StageStatus.FAILED)

    def _normalize_tool_result(self, result: Any) -> Any:
        if "completion" in result.data:
            return result.data["completion"]
        for key in ("results", "state", "preferences", "history", "interaction", "artifacts", "object"):
            if key in result.data:
                return result.data[key]
        return result.data

    def _is_user_visible_assumption(self, assumption: Any) -> bool:
        text = str(assumption or "").strip()
        if not text:
            return False
        lowered = text.lower()
        internal_markers = (
            "workflow",
            "planner",
            "planning workflow",
            "compound evidence",
            "evidence path",
            "runtime",
            "router",
            "agent",
            "artifact pipeline",
            "context stage",
            "stage output",
            "orchestration",
            "tool call",
            "classification",
            "semantic parse",
        )
        return not any(marker in lowered for marker in internal_markers)

    def _normalize_trust_payload(self, raw_trust: dict[str, Any] | None) -> dict[str, Any]:
        trust = dict(raw_trust or {})

        confidence_raw = str(trust.get("confidence", "medium")).strip().lower()
        if confidence_raw.startswith("high"):
            trust["confidence"] = "high"
        elif confidence_raw.startswith("low"):
            trust["confidence"] = "low"
        else:
            trust["confidence"] = "medium"

        data_quality_raw = str(trust.get("data_quality", "medium")).strip().lower()
        if data_quality_raw in {"low", "medium", "high"}:
            trust["data_quality"] = data_quality_raw
        elif "high" in data_quality_raw:
            trust["data_quality"] = "high"
        elif "low" in data_quality_raw:
            trust["data_quality"] = "low"
        else:
            trust["data_quality"] = "medium"

        try:
            trust["confidence_score"] = float(trust.get("confidence_score", 0.5))
        except (TypeError, ValueError):
            trust["confidence_score"] = 0.5

        trust["assumptions"] = [
            str(item).strip()
            for item in (trust.get("assumptions") or [])
            if self._is_user_visible_assumption(item)
        ]
        trust["open_questions"] = trust.get("open_questions") or []
        trust["missing_context"] = trust.get("missing_context") or []
        evidence_state = str(trust.get("evidence_state") or "").strip().lower()
        trust["evidence_state"] = evidence_state if evidence_state in {"strong", "mixed", "sparse"} else None
        trust["evidence_reasons"] = trust.get("evidence_reasons") or []
        safe_to_act = trust.get("safe_to_act")
        trust["safe_to_act"] = safe_to_act if isinstance(safe_to_act, bool) else None
        trust["calculation_used"] = bool(trust.get("calculation_used", False))
        # Normalize question_options: keep only well-formed {question, options[]} entries
        raw_qo = trust.get("question_options") or []
        normalized_qo = []
        seen_questions: set[str] = set()
        for entry in raw_qo:
            if not isinstance(entry, dict):
                continue
            q = str(entry.get("question") or "").strip()
            if not q:
                continue
            q_key = q.lower()
            if q_key in seen_questions:
                continue
            opts = [
                {"label": str(o.get("label") or ""), "value": str(o.get("value") or ""),
                 "apply_text": str(o.get("apply_text") or ""), "description": o.get("description")}
                for o in (entry.get("options") or [])
                if isinstance(o, dict) and o.get("label") and o.get("apply_text")
            ]
            offer_type = entry.get("offer_type") or None
            if offer_type == "clarification" and len(opts) != 2:
                continue
            if offer_type == "action_offer" and not opts:
                continue
            seen_questions.add(q_key)
            normalized_qo.append({"question": q, "options": opts, "offer_type": offer_type})
        trust["question_options"] = rank_question_options(normalized_qo)
        return trust

    def _normalize_sources(self, raw_sources: list[Any] | None) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, raw_source in enumerate(raw_sources or []):
            if not isinstance(raw_source, dict):
                continue
            title = raw_source.get("title") or f"Source {index + 1}"
            source_type = str(raw_source.get("type") or "document").lower()
            if source_type not in {"document", "state", "artifact"}:
                source_type = "document"
            source_id = raw_source.get("source_id") or f"source_{index + 1}"
            snippet = raw_source.get("snippet")
            if not snippet:
                snippet = raw_source.get("content")
            normalized.append(
                {
                    "source_id": source_id,
                    "title": title,
                    "type": source_type,
                    "snippet": snippet,
                    "role": raw_source.get("role"),
                    "relevance_reason": raw_source.get("relevance_reason"),
                    "used_for": (
                        [str(i) for i in raw_source["used_for"] if i]
                        if isinstance(raw_source.get("used_for"), list)
                        else [raw_source["used_for"]] if isinstance(raw_source.get("used_for"), str) and raw_source["used_for"]
                        else []
                    ),
                    "confidence_impact": raw_source.get("confidence_impact"),
                }
            )
        return normalized

    def _safe_source_ref(self, source: dict[str, Any]) -> "SourceRef | None":
        try:
            return SourceRef(**source)
        except Exception:
            return None

    def _normalize_presentation_sections(self, raw_sections: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, raw_section in enumerate(raw_sections or []):
            if not isinstance(raw_section, dict):
                continue
            normalized.append(
                PresentationSection(
                    title=str(raw_section.get("title") or raw_section.get("label") or f"Section {index + 1}"),
                    content=str(raw_section.get("content")) if raw_section.get("content") else None,
                    items=[str(item) for item in (raw_section.get("items") or []) if item],
                ).model_dump(exclude_none=True)
            )
        return normalized

    def _normalize_weekly_plan_payload(self, raw_weekly_plan: Any) -> dict[str, Any] | None:
        if not isinstance(raw_weekly_plan, dict):
            return None

        blocks: list[WeeklyPlanBlock] = []
        for index, raw_block in enumerate(raw_weekly_plan.get("blocks") or []):
            if not isinstance(raw_block, dict):
                continue
            confidence = str(raw_block.get("confidence") or "").strip().lower()
            blocks.append(
                WeeklyPlanBlock(
                    title=str(raw_block.get("title") or f"Block {index + 1}"),
                    kind=str(raw_block.get("kind")) if raw_block.get("kind") else None,
                    starts_at=str(raw_block.get("starts_at")) if raw_block.get("starts_at") else None,
                    ends_at=str(raw_block.get("ends_at")) if raw_block.get("ends_at") else None,
                    day_label=str(raw_block.get("day_label")) if raw_block.get("day_label") else None,
                    time_window=str(raw_block.get("time_window")) if raw_block.get("time_window") else None,
                    reason=str(raw_block.get("reason")) if raw_block.get("reason") else None,
                    source_refs=[str(item) for item in (raw_block.get("source_refs") or []) if item],
                    confidence=confidence if confidence in {"low", "medium", "high"} else None,
                )
            )

        meetings: list[WeeklyPlanMeeting] = []
        for index, raw_meeting in enumerate(raw_weekly_plan.get("meetings") or []):
            if isinstance(raw_meeting, dict):
                meetings.append(
                    WeeklyPlanMeeting(
                        title=str(raw_meeting.get("title") or f"Meeting {index + 1}"),
                        starts_at=str(raw_meeting.get("starts_at")) if raw_meeting.get("starts_at") else None,
                        ends_at=str(raw_meeting.get("ends_at")) if raw_meeting.get("ends_at") else None,
                        attendees=[str(item) for item in (raw_meeting.get("attendees") or []) if item],
                    )
                )
            elif raw_meeting:
                meetings.append(WeeklyPlanMeeting(title=str(raw_meeting)))

        planning_window = raw_weekly_plan.get("planning_window")
        return WeeklyPlanPresentation(
            planning_window=WeeklyPlanWindow(
                horizon=str(planning_window.get("horizon")) if isinstance(planning_window, dict) and planning_window.get("horizon") else None,
                start_date=str(planning_window.get("start_date")) if isinstance(planning_window, dict) and planning_window.get("start_date") else None,
                end_date=str(planning_window.get("end_date")) if isinstance(planning_window, dict) and planning_window.get("end_date") else None,
                timezone=str(planning_window.get("timezone")) if isinstance(planning_window, dict) and planning_window.get("timezone") else None,
                workday_start=str(planning_window.get("workday_start")) if isinstance(planning_window, dict) and planning_window.get("workday_start") else None,
                workday_end=str(planning_window.get("workday_end")) if isinstance(planning_window, dict) and planning_window.get("workday_end") else None,
            ) if isinstance(planning_window, dict) else None,
            blocks=blocks,
            deadlines=[str(item) for item in (raw_weekly_plan.get("deadlines") or []) if item],
            meetings=meetings,
            follow_ups=[str(item) for item in (raw_weekly_plan.get("follow_ups") or []) if item],
        ).model_dump(exclude_none=True)

    def _normalize_decision_payload(self, raw_decision: Any) -> dict[str, Any] | None:
        if not isinstance(raw_decision, dict):
            return None

        options: list[DecisionOption] = []
        for index, raw_option in enumerate(raw_decision.get("options") or []):
            if not isinstance(raw_option, dict):
                continue
            decision = str(raw_option.get("decision") or "").strip().lower()
            mode = str(raw_option.get("mode") or "").strip().lower()
            options.append(
                DecisionOption(
                    label=str(raw_option.get("label") or f"Option {index + 1}"),
                    decision=decision if decision in {"approve", "reject"} else None,
                    mode=mode if mode in {"draft", "send"} else None,
                    description=str(raw_option.get("description")) if raw_option.get("description") else None,
                )
            )

        return DecisionPresentation(
            decision_summary=str(raw_decision.get("decision_summary")) if raw_decision.get("decision_summary") else None,
            recommended_option=str(raw_decision.get("recommended_option")) if raw_decision.get("recommended_option") else None,
            impact_if_approved=str(raw_decision.get("impact_if_approved")) if raw_decision.get("impact_if_approved") else None,
            impact_if_rejected=str(raw_decision.get("impact_if_rejected")) if raw_decision.get("impact_if_rejected") else None,
            required_by=str(raw_decision.get("required_by")) if raw_decision.get("required_by") else None,
            options=options,
        ).model_dump(exclude_none=True)

    def _normalize_draft_payload(self, raw_draft: Any) -> dict[str, Any] | None:
        if not isinstance(raw_draft, dict):
            return None

        return DraftPresentation(
            channel=str(raw_draft.get("channel")) if raw_draft.get("channel") else None,
            status=str(raw_draft.get("status")) if raw_draft.get("status") else None,
            to=str(raw_draft.get("to")) if raw_draft.get("to") else None,
            cc=[str(item) for item in (raw_draft.get("cc") or []) if item],
            subject=str(raw_draft.get("subject")) if raw_draft.get("subject") else None,
            body=str(raw_draft.get("body")) if raw_draft.get("body") else None,
            call_to_action=str(raw_draft.get("call_to_action")) if raw_draft.get("call_to_action") else None,
        ).model_dump(exclude_none=True)

    def _normalize_finance_payload(self, raw_finance: Any) -> dict[str, Any] | None:
        if not isinstance(raw_finance, dict):
            return None

        key_metrics: list[FinanceMetricChip] = []
        for index, raw_metric in enumerate(raw_finance.get("key_metrics") or []):
            if not isinstance(raw_metric, dict):
                continue
            label = str(raw_metric.get("label") or f"Metric {index + 1}")
            value = str(raw_metric.get("value") or "")
            key_metrics.append(FinanceMetricChip(label=label, value=value))

        raw_visual = raw_finance.get("primary_visual")
        visual = None
        if isinstance(raw_visual, dict):
            visual = FinanceVisualPresentation(
                title=str(raw_visual.get("title")) if raw_visual.get("title") else None,
                label=str(raw_visual.get("label")) if raw_visual.get("label") else None,
                description=str(raw_visual.get("description")) if raw_visual.get("description") else None,
            )

        return FinancePresentation(
            template=str(raw_finance.get("template")) if raw_finance.get("template") else None,
            headline=str(raw_finance.get("headline")) if raw_finance.get("headline") else None,
            takeaways=[str(item) for item in (raw_finance.get("takeaways") or []) if item],
            implications=[str(item) for item in (raw_finance.get("implications") or []) if item],
            recommendation=str(raw_finance.get("recommendation")) if raw_finance.get("recommendation") else None,
            next_steps=[str(item) for item in (raw_finance.get("next_steps") or []) if item],
            threshold_events=[str(item) for item in (raw_finance.get("threshold_events") or []) if item],
            key_metrics=key_metrics,
            primary_visual=visual,
        ).model_dump(exclude_none=True)

    def _normalize_calendar_payload(self, raw_calendar: Any) -> dict[str, Any] | None:
        if not isinstance(raw_calendar, dict):
            return None
        events: list[CalendarEventPresentation] = []
        for ev in (raw_calendar.get("events") or []):
            if not isinstance(ev, dict):
                continue
            events.append(CalendarEventPresentation(
                title=str(ev.get("title") or "Meeting"),
                starts_at=str(ev["starts_at"]) if ev.get("starts_at") else None,
                ends_at=str(ev["ends_at"]) if ev.get("ends_at") else None,
                day_label=str(ev["day_label"]) if ev.get("day_label") else None,
                attendees=[str(a) for a in (ev.get("attendees") or []) if a],
                location=str(ev["location"]) if ev.get("location") else None,
                kind=str(ev["kind"]) if ev.get("kind") else None,
            ))
        return CalendarPresentation(
            events=events,
            follow_ups=[str(f) for f in (raw_calendar.get("follow_ups") or []) if f],
        ).model_dump(exclude_none=True)

    def _normalize_canvas_payload(self, raw_canvas: Any) -> dict[str, Any] | None:
        if not isinstance(raw_canvas, dict):
            return None

        raw_hero_metric = raw_canvas.get("hero_metric")
        hero_metric = None
        if isinstance(raw_hero_metric, dict):
            label = str(raw_hero_metric.get("label") or "").strip()
            value = str(raw_hero_metric.get("value") or "").strip()
            if label and value:
                hero_metric = CanvasHeroMetric(
                    label=label,
                    value=value,
                    delta=str(raw_hero_metric.get("delta")).strip() if raw_hero_metric.get("delta") else None,
                )

        sections: list[CanvasSectionPresentation] = []
        for raw_section in (raw_canvas.get("sections") or []):
            if not isinstance(raw_section, dict):
                continue
            label = str(raw_section.get("label") or "").strip()
            if not label:
                continue
            sections.append(
                CanvasSectionPresentation(
                    label=label,
                    bullets=[str(item).strip() for item in (raw_section.get("bullets") or []) if str(item).strip()],
                    content=str(raw_section.get("content")).strip() if raw_section.get("content") else None,
                    highlight=bool(raw_section.get("highlight")),
                )
            )

        return CanvasPresentation(
            title=str(raw_canvas.get("title")).strip() if raw_canvas.get("title") else None,
            subtitle=str(raw_canvas.get("subtitle")).strip() if raw_canvas.get("subtitle") else None,
            hero_metric=hero_metric,
            sections=sections,
            source_credit=str(raw_canvas.get("source_credit")).strip() if raw_canvas.get("source_credit") else None,
            theme_id=str(raw_canvas.get("theme_id")).strip() if raw_canvas.get("theme_id") else None,
        ).model_dump(exclude_none=True)

    def _normalize_presentation_payload(self, raw_presentation: Any) -> dict[str, Any] | None:
        if not isinstance(raw_presentation, dict):
            return None

        weekly_plan = self._normalize_weekly_plan_payload(raw_presentation.get("weekly_plan"))
        decision = self._normalize_decision_payload(raw_presentation.get("decision"))
        draft = self._normalize_draft_payload(raw_presentation.get("draft"))
        finance = self._normalize_finance_payload(raw_presentation.get("finance"))
        calendar = self._normalize_calendar_payload(raw_presentation.get("calendar"))
        canvas = self._normalize_canvas_payload(raw_presentation.get("canvas"))
        mode = str(raw_presentation.get("mode") or "").strip().lower()
        return MessagePresentation(
            mode=mode if mode in {"brief", "report", "schedule", "decision", "draft", "finance", "artifact", "media", "calendar", "canvas"} else None,
            variant=str(raw_presentation.get("variant")) if raw_presentation.get("variant") else None,
            preamble=str(raw_presentation.get("preamble")) if raw_presentation.get("preamble") else None,
            summary=str(raw_presentation.get("summary")) if raw_presentation.get("summary") else None,
            priorities=[PresentationSection(**section) for section in self._normalize_presentation_sections(raw_presentation.get("priorities"))],
            recommended_actions=[
                PresentationSection(**section)
                for section in self._normalize_presentation_sections(raw_presentation.get("recommended_actions"))
            ],
            risks=[PresentationSection(**section) for section in self._normalize_presentation_sections(raw_presentation.get("risks"))],
            details=[PresentationSection(**section) for section in self._normalize_presentation_sections(raw_presentation.get("details"))],
            weekly_plan=WeeklyPlanPresentation(**weekly_plan) if weekly_plan else None,
            decision=DecisionPresentation(**decision) if decision else None,
            draft=DraftPresentation(**draft) if draft else None,
            finance=FinancePresentation(**finance) if finance else None,
            calendar=CalendarPresentation(**calendar) if calendar else None,
            canvas=CanvasPresentation(**canvas) if canvas else None,
        ).model_dump(exclude_none=True)

    def _decision_payload_for_gate(self, *, answer: Any, gate: dict[str, Any], summary: str | None) -> dict[str, Any]:
        sections = answer.get("sections", []) if isinstance(answer, dict) else []
        options = gate.get("options") or []
        decision_summary = summary or gate.get("reason") or "A business decision is required before this action can proceed."
        impact_if_approved = None
        impact_if_rejected = "The request will stop here until it is revised or resubmitted."
        if sections and isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                label = str(section.get("label") or "").lower()
                items = [str(item) for item in (section.get("items") or []) if item]
                if "proposed action" in label and items and not impact_if_approved:
                    impact_if_approved = items[0]
                if "approval required" in label and items and not impact_if_rejected:
                    impact_if_rejected = items[0]
        return {
            "decision_summary": decision_summary,
            "recommended_option": options[0].get("label") if options and isinstance(options[0], dict) else None,
            "impact_if_approved": impact_if_approved,
            "impact_if_rejected": impact_if_rejected,
            "options": [
                {
                    "label": option.get("label") or "Resolve",
                    "decision": option.get("decision"),
                    "mode": option.get("mode"),
                    "description": option.get("description"),
                }
                for option in options
                if isinstance(option, dict)
            ],
        }

    def _planned_artifacts(self, *, interaction_id: int, artifact_plan: Any) -> list[ArtifactRef]:
        if not isinstance(artifact_plan, list):
            return []

        planned: list[ArtifactRef] = []
        for index, raw_artifact in enumerate(artifact_plan):
            if not isinstance(raw_artifact, dict):
                continue
            artifact_type = str(raw_artifact.get("artifact_type") or f"planned_artifact_{index + 1}")
            label = str(raw_artifact.get("label") or artifact_type.replace("_", " ").title())
            planned.append(
                ArtifactRef(
                    artifact_type=artifact_type,
                    artifact_id=f"planned:{interaction_id}:{artifact_type}",
                    label=label,
                    format=raw_artifact.get("format"),
                    status=raw_artifact.get("status", "planned"),
                    purpose=raw_artifact.get("purpose"),
                    ready_when=raw_artifact.get("ready_when"),
                    blocking_reason=raw_artifact.get("blocking_reason"),
                )
            )
        return planned

    def _handle_failure_transition(
        self,
        *,
        definition: WorkflowDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        routing_decision: Optional[RoutingDecision],
        error: str,
    ) -> Optional[AssistantMessageResponse]:
        current_step = next((step for step in definition.steps if step.name == workflow_state.current_stage), None)
        if not current_step or not current_step.failure_step:
            return None

        workflow_state.status = WorkflowStatus.FAILED
        workflow_state.current_stage = current_step.failure_step
        workflow_state.updated_at = datetime.now().isoformat()
        self._mark_stage_status(workflow_state, current_step.name, StageStatus.FAILED)
        self._mark_stage_status(workflow_state, current_step.failure_step, StageStatus.COMPLETED)
        self._record_workflow_event(
            workflow_state,
            workflow_run,
            WorkflowEventType.STAGE_FAILED,
            stage=current_step.name,
            payload={"error": error, "failure_step": current_step.failure_step},
        )

        response_type = definition.metadata.get("response_type", "report")
        failure_response = AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=definition.workflow_type,
            response_type=response_type,
            status="failed",
            answer=AnswerPayload(
                title=definition.metadata.get("failure_title", "Workflow Failed"),
                summary=definition.metadata.get("failure_summary", "The system could not complete this request."),
                sections=[],
            ),
            trust=TrustMetadata(
                confidence="low",
                confidence_score=0.1,
                assumptions=[],
                open_questions=["Do you want to retry this request with more context?"],
                data_quality="low",
                calculation_used=False,
                missing_context=[],
            ),
            sources=[],
            artifacts=[],
            metadata={
                "interaction_id": interaction.id,
                "current_stage": workflow_state.current_stage,
                "query": payload.message,
                "routing_decision": routing_decision.model_dump() if routing_decision else None,
                "error": error,
            },
        )
        self._persist_workflow_run(workflow_run, workflow_state, response=failure_response, error=error)
        self._sync_interaction(
            interaction.id,
            status="FAILED",
            current_stage=workflow_state.current_stage,
            response=serialize_interaction_response(failure_response),
            intent=routing_decision.intent.value if routing_decision else None,
            gate_type=None,
            context=build_resolved_interaction_context(
                approval=workflow_state.metadata.get("approvals", {}).get(workflow_state.metadata.get("approval_stage") or "")
            ),
            persist_gate=True,
        )
        return failure_response

    def _definition_for_type(self, workflow_type: str) -> WorkflowDefinition:
        from src.workflows.calendar_briefing import CALENDAR_BRIEFING_WORKFLOW
        from src.workflows.day_schedule_planning import DAY_SCHEDULE_PLANNING_WORKFLOW
        from src.workflows.document_explanation import DOCUMENT_EXPLANATION_WORKFLOW
        from src.workflows.email_ingestion import EMAIL_INGESTION_WORKFLOW
        from src.workflows.email_watcher import EMAIL_WATCHER_WORKFLOW
        from src.workflows.meeting_prep import MEETING_PREP_WORKFLOW
        from src.workflows.morning_brief import MORNING_BRIEF_WORKFLOW
        from src.workflows.report_generation import REPORT_GENERATION_WORKFLOW
        from src.workflows.schedule_planning import SCHEDULE_PLANNING_WORKFLOW
        from src.workflows.week_schedule_planning import WEEK_SCHEDULE_PLANNING_WORKFLOW
        from src.workflows.weekly_recap import WEEKLY_RECAP_WORKFLOW

        if workflow_type == "document_explanation":
            return DOCUMENT_EXPLANATION_WORKFLOW
        if workflow_type in ("schedule_planning", "day_schedule_planning", "week_schedule_planning"):
            return SCHEDULE_PLANNING_WORKFLOW
        if workflow_type == "meeting_prep":
            return MEETING_PREP_WORKFLOW
        if workflow_type == "email_watcher":
            return EMAIL_WATCHER_WORKFLOW
        if workflow_type == "email_ingestion":
            return EMAIL_INGESTION_WORKFLOW
        if workflow_type == "calendar_briefing":
            return CALENDAR_BRIEFING_WORKFLOW
        if workflow_type == "morning_brief":
            return MORNING_BRIEF_WORKFLOW
        if workflow_type == "weekly_recap":
            return WEEKLY_RECAP_WORKFLOW
        return REPORT_GENERATION_WORKFLOW

    def _payload_from_workflow_state(self, workflow_state: WorkflowState) -> AssistantQueryRequest:
        metadata = workflow_state.metadata or {}
        return AssistantQueryRequest(
            message=metadata.get("query", ""),
            conversation_id=metadata.get("conversation_id", f"conv:{workflow_state.ceo_id}:primary"),
            project_id=metadata.get("project_id"),
            attachments=metadata.get("attachments", []),
            options=metadata.get("options", {}),
        )

    def _step_index(self, definition: WorkflowDefinition, step_name: str) -> int:
        for index, step in enumerate(definition.steps):
            if step.name == step_name:
                return index
        return 0

    def _stage_is_already_completed(self, workflow_state: WorkflowState, stage_name: str) -> bool:
        for stage in workflow_state.stages:
            if stage.name == stage_name:
                return stage.status == StageStatus.COMPLETED
        return False

    def _load_stage_context(self, workflow_state: WorkflowState, stage_name: str) -> dict[str, Any]:
        context = collect_workflow_context_from_stage_outputs(
            workflow_state.workflow_type,
            workflow_state.stage_outputs,
        )
        if workflow_state.metadata.get("intent_state"):
            context.setdefault("intent_state", workflow_state.metadata["intent_state"])
        if workflow_state.metadata.get("unified_memory"):
            context.setdefault("unified_memory", workflow_state.metadata["unified_memory"])
        stage_output = workflow_state.stage_outputs.get(stage_name, {})
        if isinstance(stage_output, dict):
            tool_context = stage_output.get("tool_context", {})
            if isinstance(tool_context, dict):
                context.update(tool_context)
        return context

    def _is_context_stage(self, step: WorkflowStepDefinition) -> bool:
        return bool(step.metadata.get("context_stage"))

    def _apply_agent_metadata_updates(self, workflow_state: WorkflowState, agent_output: AgentOutput) -> None:
        metadata = agent_output.metadata or {}
        if "event_payload" in metadata and isinstance(metadata["event_payload"], dict):
            workflow_state.metadata["event_payload"] = metadata["event_payload"]
        if "planner_execution" in metadata and isinstance(metadata["planner_execution"], dict):
            workflow_state.metadata["planner_execution"] = metadata["planner_execution"]
            workflow_state.metadata.setdefault("executed_plan_stages", [])
            current_stage = workflow_state.current_stage
            if current_stage and current_stage not in workflow_state.metadata["executed_plan_stages"]:
                workflow_state.metadata["executed_plan_stages"].append(current_stage)

    async def _execute_stage_family(
        self,
        *,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
        routing_decision: Optional[RoutingDecision],
    ) -> dict[str, Any]:
        family = self.stage_handlers.classify(step)
        handler = self.stage_handlers.handler_for(family)
        return await handler(
            step=step,
            workflow_state=workflow_state,
            workflow_run=workflow_run,
            payload=payload,
            current_user=current_user,
            routing_decision=routing_decision,
        )

    async def _handle_router_stage(
        self,
        *,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
        routing_decision: Optional[RoutingDecision],
    ) -> dict[str, Any]:
        return await self.runtime_stage_handlers.handle_router_stage(
            step=step,
            workflow_state=workflow_state,
            workflow_run=workflow_run,
            payload=payload,
            current_user=current_user,
            routing_decision=routing_decision,
        )

    async def _handle_context_stage(
        self,
        *,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
        routing_decision: Optional[RoutingDecision],
    ) -> dict[str, Any]:
        return await self.runtime_stage_handlers.handle_context_stage(
            step=step,
            workflow_state=workflow_state,
            workflow_run=workflow_run,
            payload=payload,
            current_user=current_user,
            routing_decision=routing_decision,
        )

    async def _handle_agent_stage(
        self,
        *,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
        routing_decision: Optional[RoutingDecision],
    ) -> dict[str, Any]:
        return await self.runtime_stage_handlers.handle_agent_stage(
            step=step,
            workflow_state=workflow_state,
            workflow_run=workflow_run,
            payload=payload,
            current_user=current_user,
            routing_decision=routing_decision,
        )

    async def _handle_noop_stage(
        self,
        *,
        step: WorkflowStepDefinition,
        workflow_state: WorkflowState,
        workflow_run: WorkflowRun,
        payload: AssistantQueryRequest,
        current_user: User,
        routing_decision: Optional[RoutingDecision],
    ) -> dict[str, Any]:
        return await self.runtime_stage_handlers.handle_noop_stage(
            step=step,
            workflow_state=workflow_state,
            workflow_run=workflow_run,
            payload=payload,
            current_user=current_user,
            routing_decision=routing_decision,
        )

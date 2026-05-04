"""
AssistantService — single entry point for the classify→select→run→persist pipeline.

The service owns all orchestration logic.  Infrastructure (runtime engine, tools,
router agents, assembler) lives in AssistantWorkflowRunner and is injected via
composition.

Usage
-----
    from src.assistant.service import AssistantService
    from src.workflows.runner import AssistantWorkflowRunner

    runner = AssistantWorkflowRunner()
    response = await AssistantService(runner).handle(
        payload=payload, interaction=interaction, current_user=current_user
    )

Or use the convenience factory that creates its own runner with default
infrastructure:

    response = await AssistantService.default().handle(...)
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agents import TaskIntent
from src.api.schemas import AnswerSection, AssistantMessageResponse, AssistantQueryRequest, TrustMetadata
from src.core.database import append_interaction_to_conversation, get_or_create_live_context, get_previous_conversation_interaction
from src.core.models import SessionInteraction, User
from src.integrations.providers import get_integration_statuses
from src.workflows.action_references import resolve_action_reference
from src.workflows.clarification_policy import should_interrupt_for_clarification
from src.workflows.direct_actions import maybe_handle_direct_action_request
from src.workflows.intent_state import IntentState, parse_turn_intent, resolve_intent
from src.workflows.interaction_persistence import persist_interaction_state, serialize_interaction_response
from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.request_planner import plan_request
from src.workflows.routing import RouteFamily
from src.workflows.runner_semantics import SharedTurnSemanticBundle, build_turn_semantic_bundle, classify_runner_semantics
from src.workflows.types import WorkflowType
from src.workflows.watch_context import WATCH_PLAN_WORKFLOWS

from src.assistant.classification import classify_request_intent_async
from src.assistant.artifact_mode import resolve_artifact_mode
from src.assistant.enrichment import enrich_clarification_followup, enrich_live_context_followup
from src.assistant.request_interpretation import (
    RequestInterpretation,
    build_request_interpretation,
    replan_request_interpretation,
)
from src.assistant.memory import (
    build_artifact_context,
    build_and_persist_unified_memory,
    build_conversation_history,
    load_previous_intent_state,
    persist_intent_state,
    persist_pending_actions,
)
from src.assistant.types import RequestIntent
from src.agents.schemas import RoutingDecision

if TYPE_CHECKING:
    from src.workflows.runner import AssistantWorkflowRunner, ResolvedAssistantRequest


@dataclass(frozen=True)
class CorrectionRoutingDecision:
    is_correction: bool
    explicit_execution_request: bool
    channels: tuple[str, ...]
    force_direct_action: bool
    direct_workflow_type: str | None
    pin_offer_execution: bool
    rationale: str

    def as_trace_dict(self) -> dict[str, Any]:
        return {
            "is_correction": self.is_correction,
            "explicit_execution_request": self.explicit_execution_request,
            "channels": list(self.channels),
            "force_direct_action": self.force_direct_action,
            "direct_workflow_type": self.direct_workflow_type,
            "pin_offer_execution": self.pin_offer_execution,
            "rationale": self.rationale,
        }


def decide_correction_route(
    *,
    resolved_intent: IntentState,
    semantic_bundle: SharedTurnSemanticBundle | None,
    action_offer_accepted: bool,
    has_workflow_hint: bool,
) -> CorrectionRoutingDecision:
    is_correction = resolved_intent.mode == "correction"
    channels = tuple(semantic_bundle.runner_signals.requested_channels) if semantic_bundle else tuple()
    explicit_execution_request = bool(semantic_bundle and semantic_bundle.runner_signals.explicit_execution_request)

    if is_correction and explicit_execution_request and channels:
        workflow_type = WorkflowType.EMAIL_INGESTION if "email" in channels else WorkflowType.CALENDAR_BRIEFING
        return CorrectionRoutingDecision(
            is_correction=True,
            explicit_execution_request=True,
            channels=channels,
            force_direct_action=True,
            direct_workflow_type=workflow_type,
            pin_offer_execution=False,
            rationale="correction+explicit_execution+channels",
        )

    pin_offer_execution = is_correction and action_offer_accepted and not has_workflow_hint
    rationale = "correction+accepted_offer" if pin_offer_execution else "none"
    return CorrectionRoutingDecision(
        is_correction=is_correction,
        explicit_execution_request=explicit_execution_request,
        channels=channels,
        force_direct_action=False,
        direct_workflow_type=None,
        pin_offer_execution=pin_offer_execution,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

class AssistantService:
    """
    Owns the classify→select workflow→run→persist pipeline.

    Requires an AssistantWorkflowRunner for infrastructure (runtime engine,
    router agent, assembler, llm_router).  Use AssistantService.default() to
    create one with standard defaults.
    """

    def __init__(self, runner: "AssistantWorkflowRunner") -> None:
        self._runner = runner

    @classmethod
    def default(cls) -> "AssistantService":
        """Create service with default infrastructure."""
        from src.workflows.runner import AssistantWorkflowRunner
        return cls(AssistantWorkflowRunner())

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def handle(
        self,
        *,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        current_user: User,
    ) -> AssistantMessageResponse:
        """Run the full classify→select→run→persist pipeline."""
        runner = self._runner

        # Enrich clarification follow-ups and skip the gate on re-submitted queries.
        payload, skip_clarification_gate = enrich_clarification_followup(
            payload=payload,
            interaction=interaction,
            current_user=current_user,
        )
        payload = enrich_live_context_followup(
            payload=payload,
            current_user=current_user,
        )

        # Build context: history, intent state, live context, artifact context.
        conversation_history = build_conversation_history(
            ceo_id=current_user.ceo_id,
            conversation_id=payload.conversation_id,
            current_interaction_id=interaction.id,
        )
        previous_intent_state = load_previous_intent_state(
            ceo_id=current_user.ceo_id,
            conversation_id=payload.conversation_id,
        )
        live_context = (
            get_or_create_live_context(current_user.ceo_id, payload.conversation_id).model_dump(mode="json")
            if payload.conversation_id
            else {}
        )
        artifact_context = build_artifact_context(
            ceo_id=current_user.ceo_id,
            conversation_id=payload.conversation_id,
            current_interaction_id=interaction.id,
        )

        # Parse + resolve intent state.
        visible_message = extract_visible_request_text(payload.message)
        precomputed_request_plan = plan_request(
            visible_message,
            has_attachments=bool(payload.attachments),
        )
        request_interpretation = await build_request_interpretation(
            message=payload.message,
            history=conversation_history,
            request_plan=precomputed_request_plan,
            has_attachments=bool(payload.attachments),
        )
        semantic_observability = self._build_semantic_observability(request_interpretation)
        parsed_turn_intent = parse_turn_intent(
            message=payload.message,
            previous_state=previous_intent_state,
            artifact_context=artifact_context,
            precomputed_request_plan=precomputed_request_plan,
        )
        resolved_intent = resolve_intent(
            previous_intent_state,
            parsed_turn_intent,
            artifact_context,
            conversation_id=payload.conversation_id,
            last_user_message=visible_message,
        )
        persist_intent_state(
            ceo_id=current_user.ceo_id,
            conversation_id=payload.conversation_id,
            intent_state=resolved_intent,
        )

        # Resolve action reference and build unified memory.
        resolved_action_reference = resolve_action_reference(
            message=payload.message,
            pending_actions=live_context.get("pending_actions") if isinstance(live_context, dict) else [],
        )
        unified_memory = build_and_persist_unified_memory(
            ceo_id=current_user.ceo_id,
            conversation_id=payload.conversation_id,
            resolved_intent=resolved_intent,
            conversation_history=conversation_history,
            artifact_context=artifact_context,
            live_context_override=live_context,
            resolved_action_reference=resolved_action_reference,
        )

        # Legacy intent state no longer mutates semantic route selection.
        payload = self._apply_resolved_intent_to_payload(payload=payload, resolved_intent=resolved_intent)

        # Build semantic bundle (pre-computed signals reused throughout this turn).
        semantic_bundle = build_turn_semantic_bundle(
            message=payload.message,
            live_context=live_context,
            workflow_preference=resolved_intent.workflow_preference,
            task_topic=resolved_intent.task_topic,
            resolved_action_reference=resolved_action_reference if isinstance(resolved_action_reference, dict) else None,
            unified_memory=unified_memory,
            precomputed_request_plan=precomputed_request_plan,
        )

        # Artifact mode detection: C1/C2 offer acceptance + D1 direct request.
        prev_response: dict | None = None
        if payload.conversation_id:
            try:
                prev = get_previous_conversation_interaction(
                    current_user.ceo_id, payload.conversation_id, interaction.id
                )
                if prev and prev.response:
                    prev_response = json.loads(prev.response)
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass

        artifact_mode = resolve_artifact_mode(
            prev_response=prev_response,
            ceo_message=payload.message,
            resolved_deliverable_artifact_type=resolved_intent.deliverable.artifact_type,
        )
        _action_offer_meta: dict = artifact_mode.as_meta_dict()
        _action_offer_meta["intent_state"] = resolved_intent.model_dump(mode="json")
        _action_offer_meta["unified_memory"] = unified_memory
        _action_offer_meta["resolved_clarifications"] = (live_context or {}).get("resolved_clarifications") or {}
        _action_offer_meta["clarification_policy_checked"] = True
        _action_offer_meta["clarification_policy_continue"] = True
        _action_offer_meta["request_interpretation"] = request_interpretation.model_dump(mode="json")
        _action_offer_meta["policy_flags"] = {
            "artifact_pin_to_report_requested": bool(artifact_mode.pin_to_report),
        }
        if artifact_mode.offer_accepted and semantic_bundle.runner_signals.explicit_execution_request:
            _action_offer_meta["mixed_intent_execution_request"] = True
        if resolved_action_reference:
            _action_offer_meta["resolved_action_reference"] = resolved_action_reference

        # Primary classification.
        payload, request_intent, routing_trace, request_interpretation = await self._classify_route(
            payload=payload,
            interaction=interaction,
            current_user=current_user,
            resolved_intent=resolved_intent,
            action_offer_accepted=artifact_mode.offer_accepted,
            history=conversation_history,
            unified_memory=unified_memory,
            semantic_bundle=semantic_bundle,
            request_interpretation=request_interpretation,
        )
        _action_offer_meta["request_interpretation"] = request_interpretation.model_dump(mode="json")
        semantic_observability["request_id"] = request_interpretation.request_id
        semantic_observability["interpreted_primary_workflow"] = self._interpreted_primary_workflow(request_interpretation)
        semantic_observability["replan_happened"] = self._did_replan(request_interpretation)
        semantic_observability["provenance_reason"] = str((request_interpretation.provenance or {}).get("reason") or "")
        routing_trace["semantic_observability"] = dict(semantic_observability)
        self._run_eval_routing_assertions(
            request_intent=request_intent,
            routing_trace=routing_trace,
        )
        # Flow IntentClassifier response_format → artifact_type when no prior
        # detection (C1/D1) already set one. "document" → board_brief activates
        # the report_agent synthesizer's prose document mode.
        if request_intent.response_format == "document" and not _action_offer_meta.get("artifact_type"):
            _action_offer_meta["artifact_type"] = "board_brief"
        try:
            self._enforce_interpretation_authority(
                request_intent=request_intent,
                interpretation=request_interpretation,
            )
        except RuntimeError:
            semantic_observability["enforcement_mismatch"] = True
            routing_trace["semantic_observability"] = dict(semantic_observability)
            raise
        if not skip_clarification_gate:
            clarification_decision = should_interrupt_for_clarification(
                payload=payload,
                conversation_history=conversation_history,
                intent_state=resolved_intent,
                route_decision=request_intent.to_route_decision(),
                artifact_context=artifact_context,
                unified_memory=unified_memory,
                live_context=live_context,
                resolved_action_reference=resolved_action_reference if isinstance(resolved_action_reference, dict) else None,
                action_signals=semantic_bundle.action_signals,
            )
            if clarification_decision.should_interrupt:
                clarification_response = runner.runtime.build_runner_clarification_response(
                    payload=payload,
                    interaction=interaction,
                    workflow_type=request_intent.workflow_type or WorkflowType.REPORT_GENERATION,
                    clarification=clarification_decision,
                    original_query=payload.message,
                )
                if isinstance(clarification_response.metadata, dict):
                    clarification_response.metadata.setdefault("routing_trace", routing_trace)
                    clarification_response.metadata["semantic_observability"] = dict(semantic_observability)
                if payload.conversation_id and interaction.id:
                    append_interaction_to_conversation(
                        current_user.ceo_id, payload.conversation_id, interaction.id
                    )
                return clarification_response

        if request_intent.route_family == RouteFamily.ACT and self._should_allow_direct_action(resolved_intent):
            direct_action_response = await maybe_handle_direct_action_request(
                payload=payload,
                interaction=interaction,
                current_user=current_user,
                history=conversation_history,
                unified_memory=unified_memory,
                action_signals=semantic_bundle.action_signals,
                typed_requirements=self._typed_capability_requirements(request_interpretation),
            )
            if direct_action_response:
                semantic_observability["selected_workflow_implementation"] = direct_action_response.workflow_type
                if isinstance(direct_action_response.metadata, dict):
                    direct_action_response.metadata.setdefault("routing_trace", routing_trace)
                    direct_action_response.metadata["semantic_observability"] = dict(semantic_observability)
                else:
                    direct_action_response.metadata = {
                        "routing_trace": routing_trace,
                        "semantic_observability": dict(semantic_observability),
                    }
                return direct_action_response

        # Workflow resolution and execution.
        resolved = await self._resolve_request(
            payload=payload,
            interaction=interaction,
            current_user=current_user,
            request_intent=request_intent,
        )
        extra_metadata = dict(resolved.extra_metadata or {})
        if skip_clarification_gate:
            extra_metadata["skip_clarification_gate"] = True
        semantic_observability["selected_workflow_implementation"] = resolved.workflow_type
        extra_metadata["semantic_observability"] = dict(semantic_observability)
        extra_metadata.update(_action_offer_meta)
        extra_metadata["routing_trace"] = routing_trace
        response = await runner.runtime.run(
            definition=resolved.definition,
            payload=payload,
            interaction=interaction,
            current_user=current_user,
            routing_decision=resolved.routing_decision,
            extra_metadata=extra_metadata,
        )
        response = self._sanitize_execution_claims(response)
        response = self._inject_execution_capability_disclosure(
            response=response,
            message=payload.message,
            current_user=current_user,
            semantic_bundle=semantic_bundle,
        )
        response = self._inject_integration_setup_guidance(
            response=response,
            message=payload.message,
            current_user=current_user,
            semantic_bundle=semantic_bundle,
        )
        response = self._normalize_response_trust(response)

        # Persist and return.
        persist_pending_actions(
            ceo_id=current_user.ceo_id,
            conversation_id=payload.conversation_id,
            interaction_id=interaction.id,
            response=response,
        )
        persist_interaction_state(interaction.id, response=serialize_interaction_response(response))
        if payload.conversation_id and interaction.id:
            append_interaction_to_conversation(
                current_user.ceo_id, payload.conversation_id, interaction.id
            )
        return response

    # ------------------------------------------------------------------
    # Payload / intent helpers
    # ------------------------------------------------------------------

    def _apply_resolved_intent_to_payload(
        self,
        *,
        payload: AssistantQueryRequest,
        resolved_intent: IntentState,
    ) -> AssistantQueryRequest:
        # Semantic authority now belongs to RequestInterpretation.
        # Intent state may inform policy, but cannot rewrite workflow semantics here.
        return payload

    def _enforce_interpretation_authority(
        self,
        *,
        request_intent: RequestIntent,
        interpretation: RequestInterpretation,
    ) -> None:
        allowed = {candidate.name for candidate in interpretation.candidate_workflows}
        if request_intent.workflow_type not in allowed:
            raise RuntimeError(
                "Semantic override blocked: resolved workflow is not present in RequestInterpretation candidates."
            )

    def _interpreted_primary_workflow(self, interpretation: RequestInterpretation) -> str:
        if interpretation.candidate_workflows:
            return interpretation.candidate_workflows[0].name
        if interpretation.steps:
            return interpretation.steps[0].intent
        return WorkflowType.CONVERSATIONAL

    def _did_replan(self, interpretation: RequestInterpretation) -> bool:
        source = str((interpretation.provenance or {}).get("source") or "")
        return source == "canonical_replan" or "replanned" in interpretation.risk_flags

    def _build_semantic_observability(self, interpretation: RequestInterpretation) -> dict[str, Any]:
        return {
            "request_id": interpretation.request_id,
            "interpreted_primary_workflow": self._interpreted_primary_workflow(interpretation),
            "selected_workflow_implementation": "",
            "replan_happened": self._did_replan(interpretation),
            "provenance_reason": str((interpretation.provenance or {}).get("reason") or ""),
            "enforcement_mismatch": False,
        }

    def _typed_capability_requirements(self, interpretation: RequestInterpretation) -> dict[str, Any]:
        requires = {
            requirement
            for step in interpretation.steps
            for requirement in (step.requires or [])
            if requirement
        }
        write_channels: set[str] = set()
        if {"email_write", "email_draft", "email_send"} & requires:
            write_channels.add("email")
        if {"calendar_write", "calendar_create"} & requires:
            write_channels.add("calendar")
        return {
            "requires": sorted(requires),
            "contains_write_step": any(step.kind == "write_proposal" for step in interpretation.steps),
            "write_channels": sorted(write_channels),
        }

    def _should_allow_direct_action(self, intent_state: IntentState) -> bool:
        if intent_state.deliverable.kind in {"email", "artifact_revision", "resolution_language"}:
            return intent_state.write_action_requested
        if "email_ingestion" in intent_state.must_not_do or "calendar_briefing" in intent_state.must_not_do:
            return False
        return True

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    async def _classify_route(
        self,
        *,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        current_user: User,
        resolved_intent: IntentState,
        action_offer_accepted: bool = False,
        history: list[dict] | None = None,
        unified_memory: dict[str, Any] | None = None,
        semantic_bundle: SharedTurnSemanticBundle | None = None,
        request_interpretation: RequestInterpretation | None = None,
    ) -> tuple[AssistantQueryRequest, RequestIntent, dict[str, Any], RequestInterpretation]:
        if history is None:
            history = build_conversation_history(
                ceo_id=current_user.ceo_id,
                conversation_id=payload.conversation_id,
                current_interaction_id=interaction.id,
            )
        correction_decision = decide_correction_route(
            resolved_intent=resolved_intent,
            semantic_bundle=semantic_bundle,
            action_offer_accepted=action_offer_accepted,
            has_workflow_hint=bool(payload.workflow_hint),
        )
        effective_interpretation = request_interpretation
        if effective_interpretation and correction_decision.force_direct_action:
            has_write_step = any(step.kind == "write_proposal" for step in effective_interpretation.steps)
            if not has_write_step:
                # Explicit semantic change requires explicit replan with provenance.
                effective_interpretation = await replan_request_interpretation(
                    previous=effective_interpretation,
                    reason="correction_direct_action_policy",
                    message=payload.message,
                    history=history,
                    request_plan=effective_interpretation.request_plan,
                    has_attachments=bool(payload.attachments),
                )

        payload, request_intent = await classify_request_intent_async(
            payload,
            llm_router=self._runner.llm_router,
            unified_memory=unified_memory,
            precomputed_request_plan=semantic_bundle.request_plan if semantic_bundle else None,
            precomputed_write_intent=semantic_bundle.write_intent if semantic_bundle else None,
            history=history,
            interpretation=effective_interpretation,
        )
        routing_trace = {
            "path": "classifier",
            "correction_decision": correction_decision.as_trace_dict(),
            "policy_flags": {
                "pin_offer_execution_requested": bool(correction_decision.pin_offer_execution),
                "force_direct_action_requested": bool(correction_decision.force_direct_action),
            },
            "selected_route_family": request_intent.route_family,
            "selected_workflow_type": request_intent.workflow_type,
        }
        if effective_interpretation:
            routing_trace["replan"] = {
                "happened": self._did_replan(effective_interpretation),
                "reason": str((effective_interpretation.provenance or {}).get("reason") or ""),
                "source": str((effective_interpretation.provenance or {}).get("source") or ""),
            }
        return payload, request_intent, routing_trace, (effective_interpretation or request_interpretation)

    def _run_eval_routing_assertions(
        self,
        *,
        request_intent: RequestIntent,
        routing_trace: dict[str, Any],
    ) -> None:
        if os.getenv("AGENTICMIND_EVAL_ASSERTIONS") != "1":
            return
        decision = routing_trace.get("correction_decision") if isinstance(routing_trace, dict) else None
        if not isinstance(decision, dict):
            return
        if decision.get("force_direct_action"):
            assert request_intent.route_family == RouteFamily.ACT, "Correction precedence failed to route ACT."
            return
        if decision.get("pin_offer_execution"):
            assert request_intent.workflow_type == WorkflowType.REPORT_GENERATION, (
                "Accepted offer correction must stay on report_generation."
            )

    # ------------------------------------------------------------------
    # Workflow selection and resolution
    # ------------------------------------------------------------------

    def _select_workflow_type(
        self,
        payload: AssistantQueryRequest,
        request_intent: RequestIntent,
        routing_decision: RoutingDecision,
    ) -> str:
        _ = (payload, routing_decision)
        if not request_intent.workflow_type:
            raise RuntimeError("RequestIntent missing workflow_type; explicit replanning is required.")
        return request_intent.workflow_type

    async def _resolve_request(
        self,
        *,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        current_user: User,
        request_intent: RequestIntent,
    ) -> "ResolvedAssistantRequest":
        from src.workflows.runner import ResolvedAssistantRequest
        routing_decision = self._build_runtime_routing_decision(payload, current_user, request_intent)
        workflow_type = self._select_workflow_type(payload, request_intent, routing_decision)
        definition, extra_metadata = await self._resolve_workflow_definition(
            workflow_type=workflow_type,
            payload=payload,
            current_user=current_user,
            request_intent=request_intent,
        )
        resolved_metadata = {
            **(extra_metadata or {}),
            "route_decision": request_intent.to_route_decision().model_dump(mode="json"),
            "request_plan": request_intent.request_plan.model_dump(mode="json") if request_intent.request_plan else None,
        }
        return ResolvedAssistantRequest(
            workflow_type=workflow_type,
            definition=definition,
            routing_decision=routing_decision,
            extra_metadata=resolved_metadata,
        )

    async def _resolve_workflow_definition(
        self,
        *,
        workflow_type: str,
        payload: AssistantQueryRequest,
        current_user: User,
        request_intent: RequestIntent,
    ) -> tuple[object, dict | None]:
        from src.workflows.runner import _WORKFLOW_REGISTRY
        from src.workflows.report_generation import REPORT_GENERATION_WORKFLOW
        workflow_def = _WORKFLOW_REGISTRY.get(workflow_type, REPORT_GENERATION_WORKFLOW)
        if workflow_type in WATCH_PLAN_WORKFLOWS:
            event_payload = await self._runner.assembler.async_build(
                workflow_type=workflow_type,
                payload=payload,
                current_user=current_user,
                route_decision=request_intent.to_route_decision(),
            )
            return workflow_def, {"event_payload": event_payload}
        return workflow_def, None

    def _build_runtime_routing_decision(
        self,
        payload: AssistantQueryRequest,
        current_user: User,
        request_intent: RequestIntent,
    ) -> RoutingDecision:
        _ = (payload, current_user)
        if request_intent.route_family == RouteFamily.REPORT:
            return RoutingDecision(
                intent=TaskIntent.FACT_FINDING,
                specialist_required="report_agent",
                relevant_state_keys=["knowledge_base", "capital_position", "strategic_initiatives"],
                requires_approval=request_intent.requires_approval,
                rationale=request_intent.rationale,
            )
        if request_intent.route_family == RouteFamily.WATCH:
            return RoutingDecision(
                intent=TaskIntent.FACT_FINDING,
                specialist_required="briefing_agent",
                relevant_state_keys=["knowledge_base", "capital_position", "strategic_initiatives"],
                requires_approval=False,
                rationale=request_intent.rationale,
            )
        if request_intent.route_family == RouteFamily.PLAN:
            return RoutingDecision(
                intent=TaskIntent.EXECUTION_DRAFT,
                specialist_required="briefing_agent",
                relevant_state_keys=["capital_position", "strategic_initiatives", "knowledge_base"],
                requires_approval=False,
                rationale=request_intent.rationale,
            )
        return RoutingDecision(
            intent=TaskIntent.EXECUTION_REQUEST,
            specialist_required="conversational_agent",
            relevant_state_keys=["knowledge_base"],
            requires_approval=request_intent.requires_approval,
            rationale=request_intent.rationale,
        )

    # ------------------------------------------------------------------
    # Response post-processing
    # ------------------------------------------------------------------

    def _sanitize_execution_claims(self, response: AssistantMessageResponse) -> AssistantMessageResponse:
        approval = response.metadata.get("approval") if isinstance(response.metadata, dict) else None
        approved_direct_action = (
            isinstance(approval, dict)
            and approval.get("status") == "approved"
            and response.workflow_type in {WorkflowType.EMAIL_INGESTION, WorkflowType.CALENDAR_BRIEFING}
        )
        if approved_direct_action:
            return response

        replacements = (
            (re.compile(r"\bqueued for immediate execution\b", re.IGNORECASE), "prepared for immediate follow-through"),
            (re.compile(r"\bwill be executed within \d+\s*(minutes?|hours?)\b", re.IGNORECASE), "is prepared for immediate follow-through"),
            (re.compile(r"\bautomatically cc'?d\b", re.IGNORECASE), "included on the prepared draft"),
            (re.compile(r"\bin motion within the hour\b", re.IGNORECASE), "prepared for action within the hour"),
        )

        def _clean(text: str | None) -> str | None:
            if not text:
                return text
            updated = text
            for pattern, repl in replacements:
                updated = pattern.sub(repl, updated)
            return updated

        response.answer.title = _clean(response.answer.title) or response.answer.title
        response.answer.summary = _clean(response.answer.summary) or response.answer.summary
        for section in response.answer.sections or []:
            section.content = _clean(section.content)
            section.items = [(_clean(item) or item) for item in (section.items or [])]
        if response.presentation:
            response.presentation.preamble = _clean(response.presentation.preamble)
            response.presentation.summary = _clean(response.presentation.summary)
        return response

    def _normalize_response_trust(self, response: AssistantMessageResponse) -> AssistantMessageResponse:
        if not response.trust:
            return response
        normalized = self._runner.runtime._normalize_trust_payload(response.trust.model_dump(mode="json"))
        response.trust = TrustMetadata(**normalized)
        return response

    def _inject_execution_capability_disclosure(
        self,
        *,
        response: AssistantMessageResponse,
        message: str,
        current_user: User,
        semantic_bundle: SharedTurnSemanticBundle | None = None,
    ) -> AssistantMessageResponse:
        if not self._should_disclose_execution_limits(message, semantic_bundle=semantic_bundle):
            return response
        if isinstance(response.metadata, dict) and response.metadata.get("execution_unavailable"):
            return response

        unavailable: list[tuple[str, str]] = []
        for channel in self._requested_execution_channels(message, semantic_bundle=semantic_bundle):
            if not self._has_connected_write_provider(current_user.ceo_id, channel):
                reason = (
                    "No writable email provider is connected in this environment."
                    if channel == "email"
                    else "No writable calendar provider is connected in this environment."
                )
                unavailable.append((channel, reason))
        if not unavailable:
            return response

        lines = ["I can prepare the exact content and handoff details for immediate manual execution."]
        for channel, reason in unavailable:
            lines.append(reason)
            if channel == "email":
                lines.append("I cannot send emails from this environment.")
            else:
                lines.append("I cannot schedule calendar events from this environment.")
        if any(channel == "email" for channel, _ in unavailable):
            lines.append("Send this from your email client now.")
            lines.append("Use the draft below and send it manually from your email client.")
        if any(channel == "calendar" for channel, _ in unavailable):
            lines.append("Book this in your calendar manually now.")
            lines.append("Use the call details below and book it manually in your calendar.")

        sections = response.answer.sections or []
        if not any(section.label == "Execution Limit" for section in sections):
            response.answer.sections = [AnswerSection(label="Execution Limit", items=lines), *sections]
        else:
            for section in sections:
                if section.label == "Execution Limit":
                    section.items = list(dict.fromkeys([*lines, *(section.items or [])]))

        disclosure = " ".join(lines[1:])
        if disclosure and disclosure not in response.answer.summary:
            response.answer.summary = f"{disclosure} {response.answer.summary}".strip()

        if response.trust:
            for _, reason in unavailable:
                if reason not in response.trust.missing_context:
                    response.trust.missing_context.append(reason)
        if isinstance(response.metadata, dict):
            response.metadata["execution_unavailable"] = {
                "channels": [channel for channel, _ in unavailable],
                "reasons": [reason for _, reason in unavailable],
            }
        return response

    def _should_disclose_execution_limits(
        self,
        message: str,
        *,
        semantic_bundle: SharedTurnSemanticBundle | None = None,
    ) -> bool:
        return (
            self._looks_like_direct_capability_question(message, semantic_bundle=semantic_bundle)
            or self._looks_like_explicit_execution_request(message=message, semantic_bundle=semantic_bundle)
        )

    def _inject_integration_setup_guidance(
        self,
        *,
        response: AssistantMessageResponse,
        message: str,
        current_user: User,
        semantic_bundle: SharedTurnSemanticBundle | None = None,
    ) -> AssistantMessageResponse:
        if not self._looks_like_integration_setup_question(message, semantic_bundle=semantic_bundle):
            return response

        unavailable = [
            channel
            for channel in self._requested_execution_channels(message, semantic_bundle=semantic_bundle)
            if not self._has_connected_write_provider(current_user.ceo_id, channel)
        ]
        if not unavailable:
            return response

        response.answer.title = "Connect Email + Calendar to Enable Direct Execution"
        response.answer.summary = (
            "To execute sends and scheduling here, connect either Google Workspace "
            "(Gmail + Google Calendar) or Microsoft Outlook (Outlook Mail + Outlook Calendar). "
            "The buttons below open the same provider connection flow available in the sidebar."
        )
        setup_items = [
            "Google Workspace: connect Gmail first for email sends/drafts, then connect Google Calendar for scheduling.",
            "Microsoft Outlook: connect Outlook Mail first for email sends/drafts, then connect Outlook Calendar for scheduling.",
            "Once one of those stacks is connected with write permissions, I can handle the email send and calendar booking flow here.",
        ]
        sections = response.answer.sections or []
        existing_labels = {section.label for section in sections}
        if "Setup Options" not in existing_labels:
            insert_at = 1 if sections and sections[0].label == "Execution Limit" else 0
            response.answer.sections = [
                *sections[:insert_at],
                AnswerSection(label="Setup Options", items=setup_items),
                *sections[insert_at:],
            ]

        current_qo = list(response.trust.question_options or []) if response.trust else []
        connect_question = {
            "question": "I can open the provider connection flow now. Which stack do you want to connect first?",
            "offer_type": "action_offer",
            "options": [
                {
                    "label": "Google Workspace",
                    "value": "connect_google_workspace",
                    "apply_text": "Connect Google Workspace now.",
                    "description": "Starts Gmail now; then connect Google Calendar so I can schedule calls too.",
                },
                {
                    "label": "Microsoft Outlook",
                    "value": "connect_outlook_workspace",
                    "apply_text": "Connect Microsoft Outlook now.",
                    "description": "Starts Outlook Mail now; then connect Outlook Calendar so I can schedule calls too.",
                },
            ],
        }
        has_connect_offer = any(
            (isinstance(entry, dict) and any(str(opt.get("value", "")).startswith("connect_") for opt in (entry.get("options") or [])))
            or any(str(getattr(opt, "value", "")).startswith("connect_") for opt in (getattr(entry, "options", None) or []))
            for entry in current_qo
        )
        if response.trust and not has_connect_offer:
            response.trust.question_options = [connect_question, *current_qo]
        if isinstance(response.metadata, dict):
            response.metadata["integration_setup_required"] = {
                "email": "email" in unavailable,
                "calendar": "calendar" in unavailable,
                "providers": ["google_workspace", "microsoft_outlook"],
            }
        return response


    def _looks_like_direct_capability_question(
        self,
        message: str,
        *,
        semantic_bundle: SharedTurnSemanticBundle | None = None,
    ) -> bool:
        if semantic_bundle is not None:
            return semantic_bundle.runner_signals.direct_capability_question
        return classify_runner_semantics(message=message).direct_capability_question

    def _looks_like_integration_setup_question(
        self,
        message: str,
        *,
        semantic_bundle: SharedTurnSemanticBundle | None = None,
    ) -> bool:
        if semantic_bundle is not None:
            return semantic_bundle.runner_signals.integration_setup_question
        return classify_runner_semantics(message=message).integration_setup_question

    def _looks_like_explicit_execution_request(
        self,
        *,
        message: str,
        semantic_bundle: SharedTurnSemanticBundle | None = None,
    ) -> bool:
        if semantic_bundle is not None:
            return semantic_bundle.runner_signals.explicit_execution_request
        return classify_runner_semantics(message=message).explicit_execution_request

    def _requested_execution_channels(
        self,
        message: str,
        *,
        semantic_bundle: SharedTurnSemanticBundle | None = None,
    ) -> list[str]:
        if semantic_bundle is not None:
            return list(semantic_bundle.runner_signals.requested_channels)
        return list(classify_runner_semantics(message=message).requested_channels)

    def _has_connected_write_provider(self, ceo_id: str, channel: str) -> bool:
        services = {"email": {"gmail", "outlook_mail"}, "calendar": {"google_calendar", "outlook_calendar"}}.get(channel, set())
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

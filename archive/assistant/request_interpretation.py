from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.assistant.intent_classifier import ClassifiedIntent, IntentClassifier
from src.assistant.request_interpretation_policy import get_request_interpretation_policy
from src.assistant.semantic_arbitration import (
    TypedIntentContext,
    conversational_preference_evidence as semantic_conversational_preference_evidence,
    document_explanation_evidence as semantic_document_explanation_evidence,
    future_planning_evidence as semantic_future_planning_evidence,
    meeting_prep_evidence as semantic_meeting_prep_evidence,
    morning_brief_evidence as semantic_morning_brief_evidence,
    report_generation_evidence as semantic_report_generation_evidence,
    resolve_primary_workflow,
    strong_report_priority_evidence as semantic_strong_report_priority_evidence,
    weekly_recap_evidence as semantic_weekly_recap_evidence,
)
from src.workflows.action_semantics import classify_action_semantics
from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.request_planner import plan_request
from src.workflows.planning_time import _TIME_CUES
from src.workflows.planning_types import RequestPlan
from src.workflows.types import WorkflowType


StepKind = Literal["analysis", "watch", "plan", "write_proposal"]

_VALID_WORKFLOWS = {
    WorkflowType.CONVERSATIONAL,
    WorkflowType.REPORT_GENERATION,
    WorkflowType.DOCUMENT_EXPLANATION,
    WorkflowType.EMAIL_WATCHER,
    WorkflowType.EMAIL_ACTION,
    WorkflowType.CALENDAR_BRIEFING,
    WorkflowType.CALENDAR_ACTION,
    WorkflowType.MORNING_BRIEF,
    WorkflowType.WEEKLY_RECAP,
    WorkflowType.SCHEDULE_PLANNING,
    WorkflowType.MEETING_PREP,
}

_INTERPRETATION_MODEL = (
    os.getenv("REQUEST_INTERPRETATION_MODEL")
    or os.getenv("ANTHROPIC_MODEL")
    or os.getenv("ANTHROPIC_SIMPLE_MODEL")
    or "claude-sonnet-4-20250514"
)

_SYSTEM_PROMPT = """\
You are the canonical request interpreter for a CEO assistant.

Interpret each request semantically and return ONE JSON object with this shape:
{
  "job_to_be_done": string,
  "user_goal": string,
  "mode": "single" | "compound",
  "steps": [
    {
      "intent": string,
      "kind": "analysis" | "watch" | "plan" | "write_proposal",
      "requires": [string],
      "approval_required": boolean
    }
  ],
  "candidate_workflows": [
    {"name": string, "confidence": number}
  ],
  "needs_clarification": boolean,
  "risk_flags": [string],
  "explanation": string
}

Allowed workflow names for candidate_workflows.name and step intent:
conversational, report_generation, document_explanation, email_watcher, email_action,
calendar_briefing, calendar_action, morning_brief, weekly_recap, schedule_planning, meeting_prep.

Rules:
1) Use semantic understanding, not keyword matching.
2) If request has multiple objectives (e.g. prep + draft follow-up), mode must be "compound".
3) If any step is write_proposal, set approval_required true for that step.
4) For requires, use capabilities like: email_read, email_draft, calendar_read, calendar_write, documents_read, company_state_read, brief_generation.
5) Return JSON only.
6) Set job_to_be_done to the practical outcome the user is asking for (one sentence).
"""


class InterpretationStep(BaseModel):
    step_id: str
    intent: str
    kind: StepKind
    requires: list[str] = Field(default_factory=list)
    approval_required: bool = False


class CandidateWorkflow(BaseModel):
    name: str
    confidence: float


class RequestInterpretation(BaseModel):
    request_id: str
    user_goal: str
    job_to_be_done: str = ""
    mode: Literal["single", "compound"] = "single"
    steps: list[InterpretationStep] = Field(default_factory=list)
    candidate_workflows: list[CandidateWorkflow] = Field(default_factory=list)
    needs_clarification: bool = False
    risk_flags: list[str] = Field(default_factory=list)
    explanation: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    request_plan: RequestPlan | None = None
    classified_intent: dict[str, Any] = Field(default_factory=dict)


class _RawInterpretationStep(BaseModel):
    intent: str
    kind: StepKind
    requires: list[str] = Field(default_factory=list)
    approval_required: bool = False


class _RawCandidateWorkflow(BaseModel):
    name: str
    confidence: float = 0.0


class _RawInterpretation(BaseModel):
    user_goal: str
    job_to_be_done: str = ""
    mode: Literal["single", "compound"] = "single"
    steps: list[_RawInterpretationStep] = Field(default_factory=list)
    candidate_workflows: list[_RawCandidateWorkflow] = Field(default_factory=list)
    needs_clarification: bool = False
    risk_flags: list[str] = Field(default_factory=list)
    explanation: str = ""


@dataclass(frozen=True)
class _TypedPlanContext:
    visible_message: str
    lowered_message: str
    request_plan: RequestPlan | None
    plan_workflow: str
    classifier_workflow: str
    has_attachments: bool


def _step_kind_for_workflow(workflow: str) -> StepKind:
    if workflow in {WorkflowType.EMAIL_ACTION, WorkflowType.CALENDAR_ACTION, WorkflowType.EMAIL_WATCHER}:
        return "write_proposal" if workflow in {WorkflowType.EMAIL_ACTION, WorkflowType.CALENDAR_ACTION} else "watch"
    if workflow in {WorkflowType.CALENDAR_BRIEFING, WorkflowType.MORNING_BRIEF, WorkflowType.WEEKLY_RECAP}:
        return "watch"
    if workflow in {WorkflowType.SCHEDULE_PLANNING, WorkflowType.MEETING_PREP}:
        return "plan"
    return "analysis"


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _llm_available() -> bool:
    return bool(
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )


def _normalize_workflow_name(name: str) -> str:
    value = (name or "").strip()
    if value in _VALID_WORKFLOWS:
        return value
    aliases = {
        "email_draft": WorkflowType.EMAIL_ACTION,
        "email_ingestion": WorkflowType.EMAIL_ACTION,
        "calendar_create": WorkflowType.CALENDAR_ACTION,
        "report": WorkflowType.REPORT_GENERATION,
    }
    return aliases.get(value, WorkflowType.CONVERSATIONAL)


def _fallback_from_classifier(
    *,
    message: str,
    classified: ClassifiedIntent,
    request_plan: RequestPlan | None,
) -> RequestInterpretation:
    workflow = _normalize_workflow_name(classified.workflow)
    step = InterpretationStep(
        step_id="s1",
        intent=workflow,
        kind=_step_kind_for_workflow(workflow),
        requires=list(classified.data_needed or []),
        approval_required=bool(classified.action_requested),
    )
    risk_flags: list[str] = []
    if step.approval_required:
        risk_flags.append("contains_write_action")
    if classified.confidence < 0.55:
        risk_flags.append("low_confidence_route")
    return RequestInterpretation(
        request_id=str(uuid.uuid4()),
        user_goal=message.strip(),
        job_to_be_done=message.strip(),
        mode="single",
        steps=[step],
        candidate_workflows=[CandidateWorkflow(name=workflow, confidence=float(classified.confidence or 0.0))],
        # Classifier confidence alone should not force clarification;
        # clarification should be a blocking downstream policy decision.
        needs_clarification=False,
        risk_flags=risk_flags,
        explanation=classified.reasoning or "",
        provenance={"source": "intent_classifier_fallback"},
        request_plan=request_plan,
        classified_intent=classified.__dict__,
    )


def _build_typed_plan_context(
    *,
    message: str,
    classified: ClassifiedIntent,
    request_plan: RequestPlan | None,
    has_attachments: bool,
) -> _TypedPlanContext:
    visible_message = extract_visible_request_text(message).strip()
    effective_request_plan = request_plan
    if effective_request_plan is None and visible_message:
        try:
            effective_request_plan = plan_request(visible_message, has_attachments=has_attachments)
        except Exception:
            effective_request_plan = None
    plan_workflow = _normalize_workflow_name(
        str((effective_request_plan.direct_workflow if effective_request_plan else "") or (effective_request_plan.target_workflow if effective_request_plan else "") or "")
    )
    return _TypedPlanContext(
        visible_message=visible_message,
        lowered_message=visible_message.lower(),
        request_plan=effective_request_plan,
        plan_workflow=plan_workflow,
        classifier_workflow=_normalize_workflow_name(classified.workflow),
        has_attachments=has_attachments,
    )


_WORKFLOW_TO_REQUIRES: dict[str, list[str]] = {
    WorkflowType.CONVERSATIONAL: [],
    WorkflowType.REPORT_GENERATION: ["company_state_read", "brief_generation"],
    WorkflowType.DOCUMENT_EXPLANATION: ["documents_read", "brief_generation"],
    WorkflowType.EMAIL_WATCHER: ["email_read"],
    WorkflowType.EMAIL_ACTION: ["email_read", "email_draft"],
    WorkflowType.CALENDAR_BRIEFING: ["calendar_read"],
    WorkflowType.CALENDAR_ACTION: ["calendar_read", "calendar_write"],
    WorkflowType.MORNING_BRIEF: ["email_read", "calendar_read"],
    WorkflowType.WEEKLY_RECAP: ["email_read", "calendar_read"],
    WorkflowType.SCHEDULE_PLANNING: ["calendar_read", "brief_generation"],
    WorkflowType.MEETING_PREP: ["calendar_read", "documents_read", "brief_generation"],
}


def _requires_for_workflow(workflow: str, request_plan: RequestPlan | None) -> list[str]:
    requires = list(_WORKFLOW_TO_REQUIRES.get(workflow, []))
    if request_plan:
        for source in (request_plan.needed_context_sources or []):
            mapping = {
                "email": "email_read",
                "calendar": "calendar_read",
                "documents": "documents_read",
                "company_state": "company_state_read",
            }.get(str(source), "")
            if mapping and mapping not in requires:
                requires.append(mapping)
    return requires


def _semantic_context_from_typed_plan_context(context: _TypedPlanContext) -> TypedIntentContext:
    return TypedIntentContext(
        lowered_message=context.lowered_message,
        has_attachments=context.has_attachments,
        plan_workflow=context.plan_workflow,
        classifier_workflow=context.classifier_workflow,
        request_plan=context.request_plan,
    )


def _candidate_tuples(candidates: list[CandidateWorkflow]) -> list[tuple[str, float]]:
    return [(_normalize_workflow_name(item.name), float(item.confidence or 0.0)) for item in (candidates or [])]


def _semantic_pipeline_candidates(
    *,
    current_primary: str,
    context: _TypedPlanContext,
    classified_confidence: float,
    candidates: list[CandidateWorkflow],
) -> list[tuple[str, float]]:
    tuples = _candidate_tuples(candidates)
    if not tuples:
        tuples.append((current_primary, max(0.45, float(classified_confidence or 0.0))))
    names = {name for name, _score in tuples}
    if context.plan_workflow and context.plan_workflow not in names:
        tuples.append((context.plan_workflow, 0.55))
        names.add(context.plan_workflow)
    if context.classifier_workflow and context.classifier_workflow not in names:
        tuples.append((context.classifier_workflow, max(0.4, float(classified_confidence or 0.0))))
    return tuples


def _normalize_primary_with_semantic_pipeline(
    *,
    current_primary: str,
    context: _TypedPlanContext,
    action_candidate: str,
    classified_confidence: float,
    candidates: list[CandidateWorkflow],
) -> tuple[str, dict[str, Any]]:
    resolved, _signals, arbitration_meta = resolve_primary_workflow(
        context=_semantic_context_from_typed_plan_context(context),
        current_primary=current_primary,
        candidates=_semantic_pipeline_candidates(
            current_primary=current_primary,
            context=context,
            classified_confidence=classified_confidence,
            candidates=candidates,
        ),
        action_candidate=action_candidate,
    )
    return resolved, arbitration_meta


def _coerce_raw_interpretation(data: dict[str, Any]) -> _RawInterpretation:
    mode = "compound" if str(data.get("mode", "single")) == "compound" else "single"
    raw_steps = data.get("steps") if isinstance(data.get("steps"), list) else []
    steps: list[_RawInterpretationStep] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            continue
        intent = _normalize_workflow_name(str(raw_step.get("intent", "")))
        kind_value = str(raw_step.get("kind", "")).strip()
        kind: StepKind = kind_value if kind_value in {"analysis", "watch", "plan", "write_proposal"} else _step_kind_for_workflow(intent)
        steps.append(
            _RawInterpretationStep(
                intent=intent,
                kind=kind,
                requires=[str(item) for item in (raw_step.get("requires") or []) if str(item)],
                approval_required=bool(raw_step.get("approval_required", False)),
            )
        )
    raw_candidates = data.get("candidate_workflows") if isinstance(data.get("candidate_workflows"), list) else []
    candidates: list[_RawCandidateWorkflow] = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, dict):
            continue
        try:
            confidence = float(raw_candidate.get("confidence", 0.0))
        except Exception:
            confidence = 0.0
        candidates.append(
            _RawCandidateWorkflow(
                name=_normalize_workflow_name(str(raw_candidate.get("name", ""))),
                confidence=max(0.0, min(1.0, confidence)),
            )
        )
    return _RawInterpretation(
        user_goal=str(data.get("user_goal", "") or "").strip(),
        job_to_be_done=str(data.get("job_to_be_done", "") or "").strip(),
        mode=mode,
        steps=steps,
        candidate_workflows=candidates,
        needs_clarification=bool(data.get("needs_clarification", False)),
        risk_flags=[str(item) for item in (data.get("risk_flags") or []) if str(item)],
        explanation=str(data.get("explanation", "") or "").strip(),
    )


def _clarification_needed(
    *,
    parsed_flag: bool,
    steps: list[InterpretationStep],
    candidates: list[CandidateWorkflow],
    risk_flags: list[str],
) -> bool:
    if not parsed_flag:
        return False
    lowered_flags = {str(flag).strip().lower() for flag in (risk_flags or [])}
    if {"blocking_ambiguity", "missing_required_context", "missing_required_input"} & lowered_flags:
        return True
    if not steps:
        return True
    # Non-write intents should run best-effort unless explicitly blocked.
    if all(step.kind != "write_proposal" for step in steps):
        return False
    top_confidence = max((float(candidate.confidence) for candidate in candidates), default=0.0)
    has_write_step = any(step.kind == "write_proposal" for step in steps)
    if not has_write_step:
        return False
    sorted_candidates = sorted((float(candidate.confidence) for candidate in candidates), reverse=True)
    second_confidence = sorted_candidates[1] if len(sorted_candidates) > 1 else 0.0
    # Clarify only when the write intent is both low-confidence and materially ambiguous.
    return top_confidence < 0.35 or (top_confidence < 0.5 and (top_confidence - second_confidence) < 0.1)


def _canonicalize_step_kind(intent: str, declared_kind: StepKind) -> StepKind:
    canonical = _step_kind_for_workflow(intent)
    _ = declared_kind
    # Workflow semantic type is authoritative; downstream kind cannot
    # promote non-action workflows into write_proposal.
    return canonical


def _normalized_candidates(
    *,
    primary: str,
    candidates: list[CandidateWorkflow],
    fallback_confidence: float,
) -> list[CandidateWorkflow]:
    merged: dict[str, float] = {}
    for item in candidates:
        name = _normalize_workflow_name(item.name)
        merged[name] = max(float(item.confidence or 0.0), merged.get(name, 0.0))
    if primary:
        merged[primary] = max(merged.get(primary, 0.0), max(0.45, fallback_confidence))
    ranked = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
    if primary:
        ranked = [(primary, merged.get(primary, 0.0))] + [item for item in ranked if item[0] != primary]
    return [CandidateWorkflow(name=name, confidence=max(0.0, min(1.0, score))) for name, score in ranked[:4]]


def _resolve_primary_workflow(
    *,
    steps: list[InterpretationStep],
    candidates: list[CandidateWorkflow],
    classifier_workflow: str,
    plan_workflow: str,
) -> str:
    first_step = _normalize_workflow_name(steps[0].intent) if steps and steps[0].intent else ""
    first_candidate = _normalize_workflow_name(candidates[0].name) if candidates else ""
    plan = _normalize_workflow_name(plan_workflow or "")
    classifier = _normalize_workflow_name(classifier_workflow or "")

    primary = first_step or first_candidate or plan or classifier or WorkflowType.CONVERSATIONAL
    if primary == WorkflowType.CONVERSATIONAL and plan and plan != WorkflowType.CONVERSATIONAL:
        return plan
    if primary == WorkflowType.CONVERSATIONAL and classifier and classifier != WorkflowType.CONVERSATIONAL:
        return classifier

    if primary == WorkflowType.DOCUMENT_EXPLANATION and plan and plan != WorkflowType.DOCUMENT_EXPLANATION:
        return plan

    if primary in {WorkflowType.EMAIL_WATCHER, WorkflowType.CALENDAR_BRIEFING, WorkflowType.MORNING_BRIEF} and plan in {
        WorkflowType.SCHEDULE_PLANNING,
        WorkflowType.MEETING_PREP,
        WorkflowType.REPORT_GENERATION,
        WorkflowType.WEEKLY_RECAP,
    }:
        return plan

    return primary


def _is_action_workflow(workflow: str) -> bool:
    return workflow in {WorkflowType.EMAIL_ACTION, WorkflowType.CALENDAR_ACTION}


def _action_target_grounding(
    *,
    lowered: str,
    action_workflow: str,
    request_plan: RequestPlan | None = None,
) -> bool:
    policy = get_request_interpretation_policy()
    if action_workflow == WorkflowType.EMAIL_ACTION:
        has_channel = any(token in lowered for token in policy.email_channel_markers)
        explicit_draft_proposal = any(token in lowered for token in policy.email_explicit_draft_markers)
        has_target = bool(re.search(r"\bto\s+[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", lowered)) or bool(
            re.search(r"\bto\s+[a-z][a-z0-9_.-]{1,}\b", lowered)
        ) or "cc " in lowered or any(token in lowered for token in policy.email_target_pronoun_markers) or bool(
            re.search(r"\bfor\s+[a-z][a-z0-9_.-]{1,}\b", lowered)
        )
        return (has_channel and (has_target or explicit_draft_proposal)) or (explicit_draft_proposal and has_target)
    if action_workflow == WorkflowType.CALENDAR_ACTION:
        has_channel = any(token in lowered for token in policy.calendar_channel_markers)
        has_time_scope = bool(request_plan and str(request_plan.time_horizon or "unspecified") != "unspecified")
        explicit_scheduling = any(token in lowered for token in policy.calendar_explicit_scheduling_markers)
        has_time = has_time_scope or bool(_TIME_CUES.search(lowered)) or bool(re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", lowered))
        return has_channel and (has_time or explicit_scheduling)
    return False


def _act_entry_decision(
    *,
    message: str,
    primary_workflow: str,
    action_workflow: str | None,
    request_plan: RequestPlan | None = None,
) -> tuple[bool, str]:
    policy = get_request_interpretation_policy()
    lowered = extract_visible_request_text(message).strip().lower()
    effective = action_workflow or (primary_workflow if _is_action_workflow(primary_workflow) else "")
    if not effective:
        return False, "no_action_workflow"

    if not _action_target_grounding(lowered=lowered, action_workflow=effective, request_plan=request_plan):
        return False, "missing_channel_or_target_grounding"

    soft_write = any(marker in lowered for marker in policy.soft_write_markers)
    if soft_write and not any(marker in lowered for marker in policy.explicit_action_markers):
        return False, "advice_or_artifact_not_action"

    return True, "grounded_action_request"


def _non_act_workflow_fallback(
    *,
    message: str,
    classifier_workflow: str,
    plan_workflow: str,
    has_attachments: bool,
) -> str:
    policy = get_request_interpretation_policy()
    lowered = extract_visible_request_text(message).strip().lower()
    if plan_workflow and not _is_action_workflow(plan_workflow):
        return plan_workflow
    if classifier_workflow and not _is_action_workflow(classifier_workflow):
        return classifier_workflow
    if has_attachments:
        return WorkflowType.DOCUMENT_EXPLANATION
    if any(marker in lowered for marker in policy.non_act_report_markers):
        return WorkflowType.REPORT_GENERATION
    return WorkflowType.CONVERSATIONAL


def _document_explanation_evidence(*, lowered: str, has_attachments: bool) -> bool:
    return semantic_document_explanation_evidence(lowered=lowered, has_attachments=has_attachments)


def _report_generation_evidence(*, lowered: str) -> bool:
    return semantic_report_generation_evidence(lowered=lowered)


def _meeting_prep_evidence(*, lowered: str) -> bool:
    return semantic_meeting_prep_evidence(lowered=lowered)


def _weekly_recap_evidence(*, lowered: str) -> bool:
    return semantic_weekly_recap_evidence(lowered=lowered)


def _future_planning_evidence(*, lowered: str) -> bool:
    return semantic_future_planning_evidence(lowered=lowered)


def _strong_report_priority_evidence(*, lowered: str) -> bool:
    return semantic_strong_report_priority_evidence(lowered=lowered)


def _prefer_plan_primary_over_action(
    *,
    primary: str,
    action_workflow: str,
    lowered: str,
) -> bool:
    policy = get_request_interpretation_policy()
    if primary not in {WorkflowType.SCHEDULE_PLANNING, WorkflowType.MEETING_PREP}:
        return False
    if action_workflow != WorkflowType.CALENDAR_ACTION:
        return True
    explicit_calendar_execution = any(marker in lowered for marker in policy.calendar_explicit_scheduling_markers)
    if not explicit_calendar_execution:
        return True
    if _meeting_prep_evidence(lowered=lowered) or _report_generation_evidence(lowered=lowered):
        return True
    return False


def _morning_brief_evidence(*, lowered: str) -> bool:
    return semantic_morning_brief_evidence(lowered=lowered)


def _proposal_oriented_action_workflow(*, lowered: str, action_signals: Any) -> str:
    policy = get_request_interpretation_policy()
    has_email_proposal = any(marker in lowered for marker in policy.email_proposal_verbs) and any(
        marker in lowered for marker in policy.email_proposal_context_markers
    )
    has_calendar_proposal = any(marker in lowered for marker in policy.calendar_proposal_verbs) and any(
        marker in lowered for marker in policy.calendar_proposal_context_markers
    )

    if has_calendar_proposal and not has_email_proposal:
        return WorkflowType.CALENDAR_ACTION
    if has_email_proposal and not has_calendar_proposal:
        return WorkflowType.EMAIL_ACTION
    if has_calendar_proposal and bool(action_signals.calendar_action):
        return WorkflowType.CALENDAR_ACTION
    if has_email_proposal and bool(action_signals.email_action):
        return WorkflowType.EMAIL_ACTION

    return ""


def _analysis_only_guard(*, lowered: str) -> bool:
    policy = get_request_interpretation_policy()
    return any(marker in lowered for marker in policy.analysis_only_guard_markers)


def _conversational_preference_evidence(*, lowered: str) -> bool:
    return semantic_conversational_preference_evidence(lowered=lowered)


def _infer_action_workflow_candidate(
    *,
    message: str,
    action_signals: Any,
) -> str:
    policy = get_request_interpretation_policy()
    lowered = extract_visible_request_text(message).strip().lower()
    draft_with_person_target = any(marker in lowered for marker in policy.action_candidate_draft_markers) and bool(
        re.search(r"\bfor\s+[a-z][a-z0-9_.-]{1,}\b", lowered)
    )
    if bool(action_signals.email_action):
        if bool(action_signals.explicit_execution_request) or any(marker in lowered for marker in policy.action_candidate_draft_markers):
            return WorkflowType.EMAIL_ACTION
    if draft_with_person_target and not bool(action_signals.calendar_action):
        return WorkflowType.EMAIL_ACTION
    if bool(action_signals.calendar_action):
        if bool(action_signals.explicit_execution_request):
            return WorkflowType.CALENDAR_ACTION
    return ""


def _select_action_workflow_candidate(
    *,
    message: str,
    action_signals: Any,
    context: _TypedPlanContext,
) -> str:
    policy = get_request_interpretation_policy()
    lowered = context.lowered_message
    candidate = _infer_action_workflow_candidate(
        message=message,
        action_signals=action_signals,
    )
    no_email_requested = any(marker in lowered for marker in policy.no_email_markers)
    if no_email_requested and bool(action_signals.calendar_action):
        return WorkflowType.CALENDAR_ACTION
    if candidate:
        return candidate
    proposal_candidate = _proposal_oriented_action_workflow(
        lowered=context.lowered_message,
        action_signals=action_signals,
    )
    if proposal_candidate:
        return proposal_candidate
    if context.plan_workflow in {WorkflowType.EMAIL_ACTION, WorkflowType.CALENDAR_ACTION} and not _analysis_only_guard(lowered=context.lowered_message):
        return context.plan_workflow
    return ""


def _fallback_from_semantic_sources(
    *,
    message: str,
    classified: ClassifiedIntent,
    request_plan: RequestPlan | None,
    has_attachments: bool,
) -> RequestInterpretation:
    policy = get_request_interpretation_policy()
    context = _build_typed_plan_context(
        message=message,
        classified=classified,
        request_plan=request_plan,
        has_attachments=has_attachments,
    )
    visible_message = context.visible_message
    lowered = context.lowered_message
    effective_plan = context.request_plan
    plan_workflow = context.plan_workflow
    classifier_workflow = context.classifier_workflow
    primary = plan_workflow or classifier_workflow
    if classifier_workflow == WorkflowType.DOCUMENT_EXPLANATION and not plan_workflow.startswith("email") and not plan_workflow.startswith("calendar"):
        primary = WorkflowType.DOCUMENT_EXPLANATION
    if primary == WorkflowType.CONVERSATIONAL and _document_explanation_evidence(lowered=lowered, has_attachments=has_attachments):
        primary = WorkflowType.DOCUMENT_EXPLANATION
    if not primary:
        primary = WorkflowType.CONVERSATIONAL

    action_signals = classify_action_semantics(
        message=message,
        precomputed_request_workflow=primary,
    )
    action_workflow = _select_action_workflow_candidate(
        message=message,
        action_signals=action_signals,
        context=context,
    )
    primary, arbitration_meta = _normalize_primary_with_semantic_pipeline(
        current_primary=primary,
        context=context,
        action_candidate=action_workflow,
        classified_confidence=float(classified.confidence or 0.0),
        candidates=[],
    )

    compound_marker = any(marker in lowered for marker in policy.compound_markers)
    analysis_marker = any(marker in lowered for marker in policy.analysis_compound_markers)
    allow_act, act_reason = _act_entry_decision(
        message=message,
        primary_workflow=primary,
        action_workflow=action_workflow,
        request_plan=effective_plan,
    )

    steps: list[InterpretationStep] = [
        InterpretationStep(
            step_id="s1",
            intent=primary,
            kind=_step_kind_for_workflow(primary),
            requires=_requires_for_workflow(primary, effective_plan),
            approval_required=_step_kind_for_workflow(primary) == "write_proposal",
        )
    ]
    mode: Literal["single", "compound"] = "single"
    risk_flags: list[str] = []
    if action_workflow and action_workflow != primary and allow_act:
        keep_primary_and_append_action = False
        if primary in {WorkflowType.SCHEDULE_PLANNING, WorkflowType.MEETING_PREP} and compound_marker:
            keep_primary_and_append_action = _prefer_plan_primary_over_action(
                primary=primary,
                action_workflow=action_workflow,
                lowered=lowered,
            )
        if primary in {WorkflowType.REPORT_GENERATION, WorkflowType.DOCUMENT_EXPLANATION} and compound_marker and analysis_marker:
            keep_primary_and_append_action = True
        if (
            context.has_attachments
            and primary in {WorkflowType.REPORT_GENERATION, WorkflowType.DOCUMENT_EXPLANATION}
            and any(marker in lowered for marker in policy.action_deliverable_markers)
            and not _document_explanation_evidence(lowered=lowered, has_attachments=True)
            and not _report_generation_evidence(lowered=lowered)
        ):
            keep_primary_and_append_action = False

        mode = "compound" if keep_primary_and_append_action else "single"
        if mode == "single":
            steps = [
                InterpretationStep(
                    step_id="s1",
                    intent=action_workflow,
                    kind="write_proposal",
                    requires=_requires_for_workflow(action_workflow, effective_plan),
                    approval_required=True,
                )
            ]
            primary = action_workflow
        else:
            steps.append(
                InterpretationStep(
                    step_id="s2",
                    intent=action_workflow,
                    kind="write_proposal",
                    requires=_requires_for_workflow(action_workflow, effective_plan),
                    approval_required=True,
                )
            )
    if _is_action_workflow(primary) and not allow_act:
        downgraded = _non_act_workflow_fallback(
            message=message,
            classifier_workflow=classifier_workflow,
            plan_workflow=plan_workflow,
            has_attachments=has_attachments,
        )
        primary = downgraded
        steps = [
            InterpretationStep(
                step_id="s1",
                intent=primary,
                kind=_step_kind_for_workflow(primary),
                requires=_requires_for_workflow(primary, effective_plan),
                approval_required=False,
            )
        ]
        mode = "single"
    elif action_workflow and action_workflow != primary and not allow_act:
        risk_flags.append("act_downgraded_ungrounded")

    primary_confidence = float(classified.confidence or 0.0)
    candidates = [CandidateWorkflow(name=primary, confidence=max(0.45, primary_confidence))]
    if action_workflow and action_workflow != primary and allow_act:
        candidates.append(CandidateWorkflow(name=action_workflow, confidence=0.61))

    if any(step.approval_required for step in steps):
        risk_flags.append("contains_write_action")
    if primary_confidence < 0.4:
        risk_flags.append("llm_unavailable_semantic_fallback")
    if not allow_act and action_workflow:
        risk_flags.append("act_downgraded_ungrounded")

    return RequestInterpretation(
        request_id=str(uuid.uuid4()),
        user_goal=visible_message or message.strip(),
        job_to_be_done=visible_message or message.strip(),
        mode=mode,
        steps=steps,
        candidate_workflows=candidates,
        needs_clarification=False,
        risk_flags=risk_flags,
        explanation=(classified.reasoning or "Semantic fallback from canonical planner/action signals.").strip(),
        provenance={
            "source": "semantic_fallback",
            "reason": "llm_or_parse_unavailable",
            "arbitration": arbitration_meta,
            "act_gate": {
                "allow_act": allow_act,
                "reason": act_reason,
                "candidate_action_workflow": action_workflow or "",
                "selected_primary_workflow": primary,
            },
        },
        request_plan=effective_plan,
        classified_intent=classified.__dict__,
    )


async def build_request_interpretation(
    *,
    message: str,
    history: list[dict[str, Any]] | None = None,
    request_plan: RequestPlan | None = None,
    has_attachments: bool = False,
) -> RequestInterpretation:
    visible_message = extract_visible_request_text(message).strip()

    history_lines: list[str] = []
    for turn in (history or [])[-8:]:
        role = str(turn.get("role", "user"))
        content = str(turn.get("content", ""))[:400]
        history_lines.append(f"{role}: {content}")
    prompt = (
        "Recent conversation (oldest first):\n"
        + ("\n".join(history_lines) if history_lines else "(none)")
        + "\n\nAttachments present: "
        + ("yes" if has_attachments else "no")
        + "\n\nCurrent message:\n"
        + message
    )

    parsed: _RawInterpretation | None = None
    raw_interpretation: dict[str, Any] | None = None
    if _llm_available():
        try:
            from src.core.llm import LLMClient

            raw = await LLMClient(model=_INTERPRETATION_MODEL).complete_async(prompt, _SYSTEM_PROMPT)
            data = _extract_json_object(raw)
            if data is not None:
                raw_interpretation = dict(data)
                parsed = _coerce_raw_interpretation(data)
        except Exception:
            parsed = None

    classifier = IntentClassifier()
    classified = await classifier.classify(message=message, history=history or [], has_attachments=has_attachments)
    context = _build_typed_plan_context(
        message=message,
        classified=classified,
        request_plan=request_plan,
        has_attachments=has_attachments,
    )
    effective_request_plan = context.request_plan

    if parsed is None:
        return _fallback_from_semantic_sources(
            message=message,
            classified=classified,
            request_plan=effective_request_plan,
            has_attachments=has_attachments,
        )

    steps: list[InterpretationStep] = []
    for idx, step in enumerate(parsed.steps or [], start=1):
        intent = _normalize_workflow_name(step.intent)
        canonical_kind = _canonicalize_step_kind(intent, step.kind)
        steps.append(
            InterpretationStep(
                step_id=f"s{idx}",
                intent=intent,
                kind=canonical_kind,
                requires=list(dict.fromkeys(step.requires or [])),
                approval_required=bool(step.approval_required or canonical_kind == "write_proposal"),
            )
        )
    if not steps:
        fallback = _fallback_from_classifier(message=message, classified=classified, request_plan=request_plan)
        steps = fallback.steps

    candidates: list[CandidateWorkflow] = []
    for cand in parsed.candidate_workflows or []:
        candidates.append(
            CandidateWorkflow(
                name=_normalize_workflow_name(cand.name),
                confidence=max(0.0, min(1.0, float(cand.confidence))),
            )
        )
    if not candidates:
        primary = steps[0].intent if steps else _normalize_workflow_name(classified.workflow)
        candidates.append(CandidateWorkflow(name=primary, confidence=max(0.0, min(1.0, float(classified.confidence or 0.0)))))

    risk_flags = list(dict.fromkeys(parsed.risk_flags or []))
    if any(step.approval_required for step in steps) and "contains_write_action" not in risk_flags:
        risk_flags.append("contains_write_action")
    if classified.confidence < 0.55 and "low_confidence_route" not in risk_flags:
        risk_flags.append("low_confidence_route")

    mode: Literal["single", "compound"] = parsed.mode
    if len(steps) > 1:
        mode = "compound"

    plan_workflow = context.plan_workflow
    classifier_workflow = context.classifier_workflow
    primary_workflow = _resolve_primary_workflow(
        steps=steps,
        candidates=candidates,
        classifier_workflow=classifier_workflow,
        plan_workflow=plan_workflow,
    )
    lowered = context.lowered_message
    action_signals = classify_action_semantics(
        message=message,
        precomputed_request_workflow=primary_workflow,
    )
    action_candidate = _select_action_workflow_candidate(
        message=message,
        action_signals=action_signals,
        context=context,
    )
    primary_workflow, arbitration_meta = _normalize_primary_with_semantic_pipeline(
        current_primary=primary_workflow,
        context=context,
        action_candidate=action_candidate,
        classified_confidence=float(classified.confidence or 0.0),
        candidates=candidates,
    )
    allow_act, act_reason = _act_entry_decision(
        message=message,
        primary_workflow=primary_workflow,
        action_workflow=action_candidate or None,
        request_plan=effective_request_plan,
    )

    if action_candidate and allow_act and not _analysis_only_guard(lowered=lowered):
        prep_heavy = _meeting_prep_evidence(lowered=lowered)
        if not (prep_heavy and action_candidate == WorkflowType.CALENDAR_ACTION):
            if any(marker in lowered for marker in get_request_interpretation_policy().compound_markers) and primary_workflow in {
                WorkflowType.MEETING_PREP,
                WorkflowType.SCHEDULE_PLANNING,
                WorkflowType.REPORT_GENERATION,
                WorkflowType.DOCUMENT_EXPLANATION,
            }:
                keep_primary_and_append_action = True
                if primary_workflow in {WorkflowType.MEETING_PREP, WorkflowType.SCHEDULE_PLANNING}:
                    keep_primary_and_append_action = _prefer_plan_primary_over_action(
                        primary=primary_workflow,
                        action_workflow=action_candidate,
                        lowered=lowered,
                    )
                if (
                    context.has_attachments
                    and primary_workflow in {WorkflowType.REPORT_GENERATION, WorkflowType.DOCUMENT_EXPLANATION}
                    and any(marker in lowered for marker in get_request_interpretation_policy().action_deliverable_markers)
                    and not _document_explanation_evidence(lowered=lowered, has_attachments=True)
                    and not _report_generation_evidence(lowered=lowered)
                ):
                    primary_workflow = action_candidate
                    mode = "single"
                    steps = [
                        InterpretationStep(
                            step_id="s1",
                            intent=primary_workflow,
                            kind="write_proposal",
                            requires=_requires_for_workflow(primary_workflow, effective_request_plan),
                            approval_required=True,
                        )
                    ]
                elif keep_primary_and_append_action:
                    mode = "compound"
                    steps = [
                        InterpretationStep(
                            step_id="s1",
                            intent=primary_workflow,
                            kind=_step_kind_for_workflow(primary_workflow),
                            requires=_requires_for_workflow(primary_workflow, effective_request_plan),
                            approval_required=False,
                        ),
                        InterpretationStep(
                            step_id="s2",
                            intent=action_candidate,
                            kind="write_proposal",
                            requires=_requires_for_workflow(action_candidate, effective_request_plan),
                            approval_required=True,
                        ),
                    ]
                else:
                    primary_workflow = action_candidate
                    mode = "single"
                    steps = [
                        InterpretationStep(
                            step_id="s1",
                            intent=primary_workflow,
                            kind="write_proposal",
                            requires=_requires_for_workflow(primary_workflow, effective_request_plan),
                            approval_required=True,
                        )
                    ]
            else:
                primary_workflow = action_candidate
                mode = "single"
                steps = [
                    InterpretationStep(
                        step_id="s1",
                        intent=primary_workflow,
                        kind="write_proposal",
                        requires=_requires_for_workflow(primary_workflow, effective_request_plan),
                        approval_required=True,
                    )
                ]
    elif action_candidate and not allow_act:
        risk_flags.append("act_downgraded_ungrounded")
    if _is_action_workflow(primary_workflow) and not allow_act:
        primary_workflow = _non_act_workflow_fallback(
            message=message,
            classifier_workflow=classifier_workflow,
            plan_workflow=plan_workflow,
            has_attachments=has_attachments,
        )
        mode = "single"
        steps = [
            InterpretationStep(
                step_id="s1",
                intent=primary_workflow,
                kind=_step_kind_for_workflow(primary_workflow),
                requires=_requires_for_workflow(primary_workflow, request_plan),
                approval_required=False,
            )
        ]
        risk_flags.append("act_downgraded_ungrounded")
    if steps:
        first = steps[0]
        if first.intent != primary_workflow:
            steps[0] = first.model_copy(
                update={
                    "intent": primary_workflow,
                    "kind": _step_kind_for_workflow(primary_workflow),
                    "approval_required": _step_kind_for_workflow(primary_workflow) == "write_proposal",
                    "requires": _requires_for_workflow(primary_workflow, effective_request_plan),
                }
            )
    candidates = _normalized_candidates(
        primary=primary_workflow,
        candidates=candidates,
        fallback_confidence=float(classified.confidence or 0.0),
    )
    job_to_be_done = (parsed.job_to_be_done or parsed.user_goal or message).strip()
    if not job_to_be_done:
        job_to_be_done = message.strip()

    return RequestInterpretation(
        request_id=str(uuid.uuid4()),
        user_goal=(parsed.user_goal or message).strip(),
        job_to_be_done=job_to_be_done,
        mode=mode,
        steps=steps,
        candidate_workflows=candidates,
        needs_clarification=_clarification_needed(
            parsed_flag=bool(parsed.needs_clarification),
            steps=steps,
            candidates=candidates,
            risk_flags=risk_flags,
        ),
        risk_flags=risk_flags,
        explanation=(parsed.explanation or classified.reasoning or "").strip(),
        provenance={
            "source": "canonical_interpretation_llm",
            "raw_interpretation": raw_interpretation or {},
            "normalization": {
                "primary_workflow": primary_workflow,
                "mode": mode,
            },
            "arbitration": arbitration_meta,
            "act_gate": {
                "allow_act": allow_act,
                "reason": act_reason,
                "candidate_action_workflow": action_candidate or "",
                "selected_primary_workflow": primary_workflow,
            },
        },
        request_plan=effective_request_plan,
        classified_intent=classified.__dict__,
    )


async def replan_request_interpretation(
    *,
    previous: RequestInterpretation,
    reason: str,
    message: str,
    history: list[dict[str, Any]] | None = None,
    request_plan: RequestPlan | None = None,
    has_attachments: bool = False,
) -> RequestInterpretation:
    revised = await build_request_interpretation(
        message=message,
        history=history,
        request_plan=request_plan,
        has_attachments=has_attachments,
    )
    provenance = dict(revised.provenance or {})
    provenance.update(
        {
            "source": "canonical_replan",
            "reason": reason,
            "replanned_from_request_id": previous.request_id,
        }
    )
    flags = list(dict.fromkeys([*revised.risk_flags, "replanned"]))
    return revised.model_copy(update={"provenance": provenance, "risk_flags": flags})

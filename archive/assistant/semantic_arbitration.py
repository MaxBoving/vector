from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.assistant.request_interpretation_policy import get_request_interpretation_policy
from src.workflows.planning_time import _TIME_CUES
from src.workflows.planning_types import RequestPlan
from src.workflows.types import WorkflowType


@dataclass(frozen=True)
class TypedIntentContext:
    lowered_message: str
    has_attachments: bool
    plan_workflow: str
    classifier_workflow: str
    request_plan: RequestPlan | None


@dataclass(frozen=True)
class TypedIntentSignals:
    conversational_preference: bool
    strong_report_priority: bool
    report_generation: bool
    document_explanation: bool
    meeting_prep: bool
    weekly_recap: bool
    morning_brief: bool
    future_planning: bool
    cross_channel_overview: bool
    schedule_planning_context: bool


_WORKFLOW_TO_FAMILY: dict[str, str] = {
    WorkflowType.CONVERSATIONAL: "REPORT",
    WorkflowType.REPORT_GENERATION: "REPORT",
    WorkflowType.DOCUMENT_EXPLANATION: "REPORT",
    WorkflowType.EMAIL_WATCHER: "WATCH",
    WorkflowType.CALENDAR_BRIEFING: "WATCH",
    WorkflowType.MORNING_BRIEF: "WATCH",
    WorkflowType.WEEKLY_RECAP: "WATCH",
    WorkflowType.SCHEDULE_PLANNING: "PLAN",
    WorkflowType.MEETING_PREP: "PLAN",
    WorkflowType.EMAIL_ACTION: "ACT",
    WorkflowType.CALENDAR_ACTION: "ACT",
}


def workflow_family(workflow: str) -> str:
    return _WORKFLOW_TO_FAMILY.get(workflow, "REPORT")


def _family_default_workflow(family: str) -> str:
    defaults = {
        "REPORT": WorkflowType.REPORT_GENERATION,
        "WATCH": WorkflowType.MORNING_BRIEF,
        "PLAN": WorkflowType.SCHEDULE_PLANNING,
        "ACT": WorkflowType.EMAIL_ACTION,
    }
    return defaults.get(family, WorkflowType.CONVERSATIONAL)


def document_explanation_evidence(*, lowered: str, has_attachments: bool) -> bool:
    policy = get_request_interpretation_policy()
    has_doc_object = has_attachments or any(marker in lowered for marker in policy.document_object_markers)
    has_doc_deliverable = any(marker in lowered for marker in policy.document_deliverable_markers)
    has_action_deliverable = any(marker in lowered for marker in policy.action_deliverable_markers)
    return has_doc_object and has_doc_deliverable and not has_action_deliverable


def report_generation_evidence(*, lowered: str) -> bool:
    policy = get_request_interpretation_policy()
    if any(marker in lowered for marker in policy.conversational_preference_markers):
        return False
    has_report_deliverable = any(marker in lowered for marker in policy.report_deliverable_markers)
    has_decision_support = any(marker in lowered for marker in policy.report_decision_markers) and any(
        marker in lowered for marker in policy.report_topic_markers
    )
    return has_report_deliverable or has_decision_support


def meeting_prep_evidence(*, lowered: str) -> bool:
    policy = get_request_interpretation_policy()
    has_meeting_context = any(marker in lowered for marker in policy.meeting_context_markers)
    has_prep_intent = any(marker in lowered for marker in policy.meeting_prep_intent_markers)
    has_report_deliverable = any(marker in lowered for marker in policy.report_deliverable_markers)
    has_schedule_planning_context = any(marker in lowered for marker in policy.schedule_planning_markers)
    return has_meeting_context and has_prep_intent and not (has_schedule_planning_context and not has_report_deliverable)


def weekly_recap_evidence(*, lowered: str) -> bool:
    policy = get_request_interpretation_policy()
    has_overview = any(marker in lowered for marker in policy.overview_markers)
    has_retrospective = any(marker in lowered for marker in policy.retrospective_markers)
    return has_overview and has_retrospective


def future_planning_evidence(*, lowered: str) -> bool:
    policy = get_request_interpretation_policy()
    has_future_scope = any(marker in lowered for marker in policy.planning_future_markers) or bool(_TIME_CUES.search(lowered))
    has_planning_intent = any(marker in lowered for marker in policy.planning_intent_markers) or any(
        marker in lowered for marker in policy.schedule_planning_markers
    )
    return has_planning_intent and (has_future_scope or any(marker in lowered for marker in policy.schedule_planning_markers))


def morning_brief_evidence(*, lowered: str) -> bool:
    policy = get_request_interpretation_policy()
    has_scope = any(marker in lowered for marker in policy.morning_scope_markers)
    has_focus = any(marker in lowered for marker in policy.focus_intent_markers)
    return has_scope and has_focus


def conversational_preference_evidence(*, lowered: str) -> bool:
    policy = get_request_interpretation_policy()
    return any(marker in lowered for marker in policy.conversational_preference_markers)


def strong_report_priority_evidence(*, lowered: str) -> bool:
    policy = get_request_interpretation_policy()
    return any(marker in lowered for marker in policy.report_topic_markers)


def cross_channel_overview_evidence(*, lowered: str) -> bool:
    policy = get_request_interpretation_policy()
    has_overview = any(marker in lowered for marker in policy.overview_markers)
    has_email_context = "inbox" in lowered or "thread" in lowered or any(marker in lowered for marker in policy.email_channel_markers)
    has_calendar_context = "calendar" in lowered or any(marker in lowered for marker in policy.calendar_channel_markers)
    return has_overview and has_email_context and has_calendar_context


def extract_typed_intent_signals(
    *,
    context: TypedIntentContext,
) -> TypedIntentSignals:
    lowered = context.lowered_message
    policy = get_request_interpretation_policy()
    return TypedIntentSignals(
        conversational_preference=conversational_preference_evidence(lowered=lowered),
        strong_report_priority=strong_report_priority_evidence(lowered=lowered),
        report_generation=report_generation_evidence(lowered=lowered),
        document_explanation=document_explanation_evidence(lowered=lowered, has_attachments=context.has_attachments),
        meeting_prep=meeting_prep_evidence(lowered=lowered),
        weekly_recap=weekly_recap_evidence(lowered=lowered),
        morning_brief=morning_brief_evidence(lowered=lowered),
        future_planning=future_planning_evidence(lowered=lowered),
        cross_channel_overview=cross_channel_overview_evidence(lowered=lowered),
        schedule_planning_context=any(marker in lowered for marker in policy.schedule_planning_markers),
    )


def _typed_intent_precedence_workflow(
    *,
    signals: TypedIntentSignals,
    context: TypedIntentContext,
    action_candidate: str,
) -> str:
    if signals.conversational_preference:
        return WorkflowType.CONVERSATIONAL
    if signals.strong_report_priority:
        return WorkflowType.REPORT_GENERATION
    if signals.document_explanation:
        return WorkflowType.DOCUMENT_EXPLANATION
    if signals.weekly_recap:
        return WorkflowType.WEEKLY_RECAP
    if signals.morning_brief:
        return WorkflowType.MORNING_BRIEF
    if signals.future_planning:
        return WorkflowType.SCHEDULE_PLANNING
    if signals.meeting_prep:
        return WorkflowType.MEETING_PREP
    if signals.report_generation:
        return WorkflowType.REPORT_GENERATION
    if action_candidate:
        return action_candidate
    return ""


def _typed_precedence_strength(*, signals: TypedIntentSignals, action_candidate: str) -> int:
    if signals.conversational_preference or signals.strong_report_priority:
        return 3
    if signals.weekly_recap or signals.morning_brief or signals.future_planning:
        return 2
    if signals.meeting_prep or signals.report_generation or signals.document_explanation or bool(action_candidate):
        return 1
    return 0


def _representative_workflow_for_family(
    *,
    family: str,
    current_primary: str,
    context: TypedIntentContext,
    candidates: list[tuple[str, float]],
    action_candidate: str,
) -> str:
    if workflow_family(current_primary) == family:
        return current_primary
    if action_candidate and workflow_family(action_candidate) == family:
        return action_candidate
    if context.plan_workflow and workflow_family(context.plan_workflow) == family:
        return context.plan_workflow
    if context.classifier_workflow and workflow_family(context.classifier_workflow) == family:
        return context.classifier_workflow
    for name, _score in candidates:
        if workflow_family(name) == family:
            return name
    return _family_default_workflow(family)


def _semantic_family_scores(
    *,
    context: TypedIntentContext,
    current_primary: str,
    candidates: list[tuple[str, float]],
    action_candidate: str,
    signals: TypedIntentSignals,
) -> dict[str, float]:
    scores: dict[str, float] = {}

    def _add(family: str, amount: float) -> None:
        scores[family] = scores.get(family, 0.0) + amount

    for name, confidence in candidates:
        family = workflow_family(name)
        scores[family] = max(scores.get(family, 0.0), float(confidence or 0.0))
    if current_primary:
        _add(workflow_family(current_primary), 0.08)
    if context.plan_workflow:
        _add(workflow_family(context.plan_workflow), 0.06)
    if context.classifier_workflow:
        _add(workflow_family(context.classifier_workflow), 0.04)
    if action_candidate:
        _add("ACT", 0.08)

    if signals.conversational_preference:
        _add("REPORT", 0.18)
    if signals.report_generation:
        _add("REPORT", 0.18)
    if signals.strong_report_priority:
        _add("REPORT", 0.22)
    if signals.document_explanation:
        _add("REPORT", 0.16)
    if signals.meeting_prep:
        _add("PLAN", 0.18)
    if signals.future_planning:
        _add("PLAN", 0.18)
    if signals.weekly_recap:
        _add("WATCH", 0.18)
    if signals.morning_brief:
        _add("WATCH", 0.14)
    return scores


def recall_primary_workflow_from_signals(
    *,
    current_primary: str,
    context: TypedIntentContext,
    action_candidate: str,
    signals: TypedIntentSignals,
) -> str:
    primary = current_primary
    if signals.conversational_preference:
        primary = WorkflowType.CONVERSATIONAL
    if signals.strong_report_priority and primary in {
        WorkflowType.CONVERSATIONAL,
        WorkflowType.EMAIL_WATCHER,
        WorkflowType.CALENDAR_BRIEFING,
        WorkflowType.MORNING_BRIEF,
        WorkflowType.WEEKLY_RECAP,
        WorkflowType.SCHEDULE_PLANNING,
        WorkflowType.MEETING_PREP,
    }:
        primary = WorkflowType.REPORT_GENERATION

    if context.plan_workflow == WorkflowType.DOCUMENT_EXPLANATION and not action_candidate and context.has_attachments:
        primary = WorkflowType.DOCUMENT_EXPLANATION
    if signals.document_explanation and not action_candidate:
        primary = WorkflowType.DOCUMENT_EXPLANATION

    if context.plan_workflow == WorkflowType.MEETING_PREP and primary in {
        WorkflowType.CONVERSATIONAL,
        WorkflowType.REPORT_GENERATION,
        WorkflowType.EMAIL_WATCHER,
        WorkflowType.CALENDAR_BRIEFING,
        WorkflowType.MORNING_BRIEF,
    }:
        primary = WorkflowType.MEETING_PREP
    if signals.meeting_prep and primary in {
        WorkflowType.CONVERSATIONAL,
        WorkflowType.REPORT_GENERATION,
        WorkflowType.EMAIL_WATCHER,
        WorkflowType.CALENDAR_BRIEFING,
        WorkflowType.MORNING_BRIEF,
        WorkflowType.SCHEDULE_PLANNING,
    }:
        if not (primary == WorkflowType.REPORT_GENERATION and signals.strong_report_priority):
            if primary != WorkflowType.SCHEDULE_PLANNING or not signals.schedule_planning_context:
                primary = WorkflowType.MEETING_PREP
    if signals.future_planning and primary in {
        WorkflowType.CONVERSATIONAL,
        WorkflowType.CALENDAR_BRIEFING,
        WorkflowType.MORNING_BRIEF,
        WorkflowType.WEEKLY_RECAP,
        WorkflowType.MEETING_PREP,
    }:
        primary = WorkflowType.SCHEDULE_PLANNING
    if signals.morning_brief and primary in {
        WorkflowType.CONVERSATIONAL,
        WorkflowType.EMAIL_WATCHER,
        WorkflowType.CALENDAR_BRIEFING,
        WorkflowType.SCHEDULE_PLANNING,
    }:
        primary = WorkflowType.MORNING_BRIEF
    if signals.cross_channel_overview and primary in {
        WorkflowType.EMAIL_WATCHER,
        WorkflowType.CALENDAR_BRIEFING,
        WorkflowType.CONVERSATIONAL,
    }:
        primary = WorkflowType.MORNING_BRIEF
    if signals.weekly_recap and primary in {
        WorkflowType.CONVERSATIONAL,
        WorkflowType.EMAIL_WATCHER,
        WorkflowType.CALENDAR_BRIEFING,
        WorkflowType.MORNING_BRIEF,
    }:
        primary = WorkflowType.WEEKLY_RECAP
    if signals.report_generation and primary in {
        WorkflowType.CONVERSATIONAL,
        WorkflowType.MEETING_PREP,
        WorkflowType.SCHEDULE_PLANNING,
        WorkflowType.MORNING_BRIEF,
    }:
        if not signals.meeting_prep:
            primary = WorkflowType.REPORT_GENERATION
    return primary


def resolve_primary_workflow(
    *,
    context: TypedIntentContext,
    current_primary: str,
    candidates: list[tuple[str, float]],
    action_candidate: str,
) -> tuple[str, TypedIntentSignals, dict[str, Any]]:
    policy = get_request_interpretation_policy()
    signals = extract_typed_intent_signals(context=context)
    recalled = recall_primary_workflow_from_signals(
        current_primary=current_primary,
        context=context,
        action_candidate=action_candidate,
        signals=signals,
    )

    scores = _semantic_family_scores(
        context=context,
        current_primary=recalled,
        candidates=candidates,
        action_candidate=action_candidate,
        signals=signals,
    )
    ranking = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if len(ranking) < 2:
        return recalled, signals, {"applied": False, "reason": "insufficient_family_candidates"}
    top_family, top_score = ranking[0]
    second_family, second_score = ranking[1]
    if top_family == second_family:
        return recalled, signals, {"applied": False, "reason": "same_family_top_scores", "scores": ranking[:4]}
    delta = float(top_score - second_score)
    if delta > float(policy.arbitration_close_score_delta):
        return recalled, signals, {
            "applied": False,
            "reason": "not_close_score_conflict",
            "scores": ranking[:4],
            "delta": round(delta, 4),
        }
    current_family = workflow_family(recalled)
    if current_family not in {top_family, second_family}:
        return recalled, signals, {
            "applied": False,
            "reason": "primary_outside_top_conflict",
            "scores": ranking[:4],
            "delta": round(delta, 4),
        }
    if policy.arbitration_block_act_promotion_from_non_act and top_family == "ACT" and current_family != "ACT":
        return recalled, signals, {
            "applied": False,
            "reason": "act_top_family_blocked_for_non_act_primary",
            "scores": ranking[:4],
            "delta": round(delta, 4),
        }

    precedence_workflow = _typed_intent_precedence_workflow(signals=signals, context=context, action_candidate=action_candidate)
    precedence_family = workflow_family(precedence_workflow) if precedence_workflow else ""
    precedence_strength = _typed_precedence_strength(signals=signals, action_candidate=action_candidate)
    compound_prompt = any(marker in context.lowered_message for marker in policy.compound_markers)
    winning_family = precedence_family if precedence_family in {top_family, second_family, current_family} else top_family

    if (
        compound_prompt
        and precedence_family
        and precedence_family != current_family
        and precedence_strength < int(policy.arbitration_compound_override_min_strength)
    ):
        winning_family = current_family

    weekly_recap_grounded = signals.weekly_recap or recalled == WorkflowType.WEEKLY_RECAP or (
        context.plan_workflow == WorkflowType.WEEKLY_RECAP
    ) or (context.classifier_workflow == WorkflowType.WEEKLY_RECAP)
    if {top_family, second_family} == {"WATCH", "REPORT"} and weekly_recap_grounded:
        winning_family = "WATCH"

    direct_report_plan = bool(
        context.request_plan and str(context.request_plan.direct_workflow or "") == WorkflowType.REPORT_GENERATION
    )
    if current_family == "REPORT" and winning_family == "PLAN" and direct_report_plan and signals.strong_report_priority:
        winning_family = "REPORT"

    selected = _representative_workflow_for_family(
        family=winning_family,
        current_primary=recalled,
        context=context,
        candidates=candidates,
        action_candidate=action_candidate,
    )
    return selected, signals, {
        "applied": True,
        "reason": "close_score_cross_family_conflict",
        "top_family": top_family,
        "second_family": second_family,
        "delta": round(delta, 4),
        "scores": ranking[:4],
        "precedence_workflow": precedence_workflow,
        "precedence_strength": precedence_strength,
        "compound_prompt": compound_prompt,
        "selected_workflow": selected,
    }

"""
Classifier seam tests.

Covers the pure classification layer — functions that take a message or payload
and return a routing/planning decision, with NO runner, NO read model, NO DB.

Seam: classify_route, plan_request, classify_runner_semantics,
      classify_action_semantics, parse_turn_intent.

Test structure:
  1. Route classification (classify_route → RouteFamily + workflow_chain)
  2. Request planning (plan_request → RequestPlan direct/compound)
  3. Runner & action semantics (classify_runner_semantics, classify_action_semantics)
  4. Intent state (parse_turn_intent)
  5. Routing disambiguation (recap vs morning vs schedule)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.api.schemas import AssistantQueryRequest, AttachmentRef
from src.workflows.action_semantics import classify_action_semantics
from src.workflows.intent_state import parse_turn_intent
from src.workflows.request_planner import IntentClassification, plan_request
from src.workflows.routing import RouteFamily, RouteSubintent, classify_route
from src.workflows.runner_semantics import build_turn_semantic_bundle, classify_runner_semantics
from src.workflows.types import WorkflowType


# ---------------------------------------------------------------------------
# 1. Route classification — email_watcher
# ---------------------------------------------------------------------------


def test_email_watcher_route_from_scan_inbox() -> None:
    payload = AssistantQueryRequest(message="Scan my inbox for anything important.", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.WATCH
    assert WorkflowType.EMAIL_WATCHER in route.workflow_chain
    assert RouteSubintent.EMAIL_WATCH in route.subintents


def test_email_watcher_route_from_email_review() -> None:
    payload = AssistantQueryRequest(message="Review my emails and tell me what needs attention.", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.WATCH
    assert WorkflowType.EMAIL_WATCHER in route.workflow_chain


def test_email_watcher_planner_direct_workflow() -> None:
    plan = plan_request("Scan my inbox")
    assert plan.direct_workflow == WorkflowType.EMAIL_WATCHER
    assert "email" in plan.needed_context_sources


# ---------------------------------------------------------------------------
# 2. Route classification — calendar_briefing
# ---------------------------------------------------------------------------


def test_calendar_briefing_route_from_meetings_today() -> None:
    payload = AssistantQueryRequest(message="What meetings do I have today?", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.WATCH
    assert WorkflowType.CALENDAR_BRIEFING in route.workflow_chain
    assert RouteSubintent.CALENDAR_WATCH in route.subintents


def test_calendar_briefing_route_from_calendar_brief() -> None:
    payload = AssistantQueryRequest(message="Give me a calendar brief for tomorrow.", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.WATCH
    assert WorkflowType.CALENDAR_BRIEFING in route.workflow_chain


def test_calendar_briefing_planner_direct_workflow() -> None:
    plan = plan_request("Review my calendar for today")
    assert plan.direct_workflow == WorkflowType.CALENDAR_BRIEFING


# ---------------------------------------------------------------------------
# 3. Route classification — morning_brief
# ---------------------------------------------------------------------------


def test_morning_brief_route_from_morning_brief() -> None:
    payload = AssistantQueryRequest(message="Give me my morning brief.", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.WATCH
    assert WorkflowType.MORNING_BRIEF in route.workflow_chain
    assert RouteSubintent.EMAIL_WATCH in route.subintents
    assert RouteSubintent.CALENDAR_WATCH in route.subintents


def test_morning_brief_route_from_daily_digest() -> None:
    payload = AssistantQueryRequest(message="What's my daily digest?", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.WATCH
    assert WorkflowType.MORNING_BRIEF in route.workflow_chain


def test_morning_brief_planner_direct_workflow() -> None:
    plan = plan_request("Give me my morning brief")
    assert plan.direct_workflow == WorkflowType.MORNING_BRIEF
    assert "email" in plan.needed_context_sources
    assert "calendar" in plan.needed_context_sources


# ---------------------------------------------------------------------------
# 4. Route classification — weekly_recap
# ---------------------------------------------------------------------------


def test_weekly_recap_route_from_recap_my_week() -> None:
    payload = AssistantQueryRequest(message="Recap my week.", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.WATCH
    assert WorkflowType.WEEKLY_RECAP in route.workflow_chain


def test_weekly_recap_route_from_week_in_review() -> None:
    payload = AssistantQueryRequest(message="Give me a week in review.", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.WATCH
    assert WorkflowType.WEEKLY_RECAP in route.workflow_chain


def test_weekly_recap_planner_direct_workflow() -> None:
    for prompt in ("recap my week", "week in review", "weekly recap", "what happened this week"):
        plan = plan_request(prompt)
        assert plan.direct_workflow == WorkflowType.WEEKLY_RECAP, f"Failed for: {prompt!r}"


def test_weekly_recap_does_not_collide_with_morning_brief() -> None:
    recap_plan = plan_request("recap my week")
    brief_plan = plan_request("give me my morning brief")
    assert recap_plan.direct_workflow == WorkflowType.WEEKLY_RECAP
    assert brief_plan.direct_workflow == WorkflowType.MORNING_BRIEF


# ---------------------------------------------------------------------------
# 5. Route classification — meeting_prep
# ---------------------------------------------------------------------------


def test_meeting_prep_route_from_prep_for_meeting() -> None:
    payload = AssistantQueryRequest(message="Prep me for my meeting with the board.", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.PLAN
    assert WorkflowType.MEETING_PREP in route.workflow_chain
    assert RouteSubintent.MEETING_PREP in route.subintents


def test_meeting_prep_route_from_meeting_brief_for() -> None:
    payload = AssistantQueryRequest(message="Give me a meeting brief for the investor call today.", conversation_id="c1")
    route = classify_route(payload)

    assert WorkflowType.MEETING_PREP in route.workflow_chain


def test_meeting_prep_planner_direct_workflow() -> None:
    plan = plan_request("prep for my meeting with the board today")
    assert plan.direct_workflow == WorkflowType.MEETING_PREP
    assert "email" in plan.needed_context_sources
    assert "calendar" in plan.needed_context_sources


# ---------------------------------------------------------------------------
# 6. Route classification — schedule_planning (day + week)
# ---------------------------------------------------------------------------


def test_day_schedule_route_from_plan_my_day() -> None:
    payload = AssistantQueryRequest(message="Plan my day for today.", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.PLAN
    assert WorkflowType.SCHEDULE_PLANNING in route.workflow_chain


def test_day_schedule_planner_targets_schedule_planning() -> None:
    plan = plan_request("Plan my day for tomorrow")
    assert plan.target_workflow == WorkflowType.SCHEDULE_PLANNING
    assert plan.time_horizon == "tomorrow"


def test_week_schedule_route_from_plan_my_week() -> None:
    payload = AssistantQueryRequest(message="Plan my week based on my emails and calendar.", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.PLAN
    assert route.request_plan is not None
    assert route.request_plan.target_workflow == WorkflowType.SCHEDULE_PLANNING


def test_week_schedule_planner_compound_targets_week_workflow() -> None:
    plan = plan_request("Make me a schedule for this week based on my emails and calendar")
    assert plan.mode == "compound_plan"
    assert plan.target_workflow == WorkflowType.SCHEDULE_PLANNING
    assert plan.time_horizon == "this_week"
    assert "email" in plan.needed_context_sources
    assert "calendar" in plan.needed_context_sources


def test_week_schedule_planner_next_week_horizon() -> None:
    plan = plan_request("Organize my schedule for next week based on my inbox")
    assert plan.target_workflow == WorkflowType.SCHEDULE_PLANNING
    assert plan.time_horizon == "next_week"


# ---------------------------------------------------------------------------
# 7. Route classification — report_generation & document_explanation
# ---------------------------------------------------------------------------


def test_report_generation_route_is_default() -> None:
    payload = AssistantQueryRequest(message="Give me a company health summary.", conversation_id="c1")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.REPORT
    assert WorkflowType.REPORT_GENERATION in route.workflow_chain


def test_document_explanation_route_from_attachment() -> None:
    payload = AssistantQueryRequest(
        message="What does this document mean for us?",
        conversation_id="c1",
        attachments=[AttachmentRef(document_id="doc_1", filename="policy.pdf")],
    )
    route = classify_route(payload)

    assert WorkflowType.DOCUMENT_EXPLANATION in route.workflow_chain


# ---------------------------------------------------------------------------
# 8. Planner — compound & semantic fallback
# ---------------------------------------------------------------------------


def test_planner_returns_compound_plan_for_mixed_weekly_request() -> None:
    plan = plan_request("Scan my inbox and generate me a schedule plan for next week.")

    assert plan.mode == "compound_plan"
    assert plan.target_workflow == WorkflowType.SCHEDULE_PLANNING
    assert plan.time_horizon == "next_week"
    assert [subtask.key for subtask in plan.subtasks] == [
        "scan_inbox",
        "review_calendar",
        "build_schedule",
    ]
    assert "email" in plan.needed_context_sources
    assert "calendar" in plan.needed_context_sources
    assert [step.key for step in plan.execution_steps] == [
        "scan_inbox",
        "review_calendar",
        "synthesize_planning_candidates",
        "place_candidates",
        "build_schedule",
    ]


def test_planner_normalizes_common_calendar_typos_and_weekly_phrasing() -> None:
    plan = plan_request("please make a schedule for my week based on my emails and calender")

    assert plan.mode == "compound_plan"
    assert plan.target_workflow == WorkflowType.SCHEDULE_PLANNING
    assert plan.time_horizon == "this_week"
    assert "email" in plan.needed_context_sources
    assert "calendar" in plan.needed_context_sources
    assert any(subtask.key == "review_calendar" for subtask in plan.subtasks)


def test_planner_treats_week_shorthand_as_weekly_horizon() -> None:
    plan = plan_request("organize my inbox and meetings for this wk")

    assert plan.mode == "compound_plan"
    assert plan.time_horizon == "this_week"
    assert "email" in plan.needed_context_sources
    assert "calendar" in plan.needed_context_sources


def test_planner_uses_calendar_for_weekly_schedule_even_without_literal_calendar_keyword() -> None:
    plan = plan_request("make me a schedule for the week based on my emails")

    assert plan.mode == "compound_plan"
    assert plan.time_horizon == "this_week"
    assert "calendar" in plan.needed_context_sources
    assert any(step.key == "review_calendar" for step in plan.execution_steps)


def test_planner_semantic_fallback_handles_paraphrased_weekly_schedule_request() -> None:
    plan = plan_request("What do I need to do this week from my messages, and when do I have free time?")

    assert plan.mode == "compound_plan"
    assert plan.time_horizon == "this_week"
    assert "email" in plan.needed_context_sources
    assert "calendar" in plan.needed_context_sources
    assert plan.planning_metadata["semantic_fallback_used"] is True


def test_planner_preserves_weekday_target_for_morning_brief() -> None:
    plan = plan_request("Prepare a morning brief for Friday this week")

    assert plan.direct_workflow == WorkflowType.MORNING_BRIEF
    assert plan.time_horizon == "this_week"
    assert plan.target_label == "Friday this week"
    assert plan.target_date is not None


def test_planner_semantic_fallback_does_not_turn_document_readout_into_schedule_request() -> None:
    plan = plan_request("Give me a readout on these materials and attachments", has_attachments=True)

    assert plan.mode == "direct_workflow"
    assert plan.direct_workflow == WorkflowType.REPORT_GENERATION
    assert plan.planning_metadata["semantic_fallback_used"] is True
    assert plan.planning_metadata["semantic_signals"]["planning"] is False


def test_routing_semantic_fallback_keeps_planning_queries_on_planner_path() -> None:
    payload = AssistantQueryRequest(
        message="What should I work on this week from my correspondence, and where do I still have open time?",
        conversation_id="conv_test",
    )

    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.PLAN
    assert route.request_plan is not None
    assert route.request_plan.mode == "compound_plan"
    assert route.workflow_chain == [
        WorkflowType.EMAIL_WATCHER,
        WorkflowType.CALENDAR_BRIEFING,
        WorkflowType.SCHEDULE_PLANNING,
    ]


def test_routing_uses_planner_for_compound_weekly_request() -> None:
    payload = AssistantQueryRequest(
        message="Scan my inbox and generate me a schedule plan for next week.",
        conversation_id="conv_test",
    )

    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.PLAN
    assert route.request_plan is not None
    assert route.request_plan.mode == "compound_plan"
    assert route.workflow_chain == [
        WorkflowType.EMAIL_WATCHER,
        WorkflowType.CALENDAR_BRIEFING,
        WorkflowType.SCHEDULE_PLANNING,
    ]


def test_finance_close_metrics_request_stays_on_report_path() -> None:
    plan = plan_request(
        "What are the key financial metrics I should review before our finance close meeting, and are there any known issues?"
    )
    payload = AssistantQueryRequest(
        message="What are the key financial metrics I should review before our finance close meeting, and are there any known issues?",
        conversation_id="conv_test",
    )
    route = classify_route(payload)

    assert plan.direct_workflow == WorkflowType.REPORT_GENERATION
    assert route.primary_intent == RouteFamily.REPORT
    assert route.workflow_chain == [WorkflowType.REPORT_GENERATION]


def test_customer_escalation_action_plan_does_not_route_to_schedule() -> None:
    plan = plan_request(
        "Can you provide detailed action plans for the top two escalations, particularly for Apex Health and Redwood Systems? What steps should we take immediately?"
    )
    payload = AssistantQueryRequest(
        message="Can you provide detailed action plans for the top two escalations, particularly for Apex Health and Redwood Systems? What steps should we take immediately?",
        conversation_id="conv_test",
    )
    route = classify_route(payload)

    assert plan.direct_workflow == WorkflowType.REPORT_GENERATION
    assert route.primary_intent == RouteFamily.REPORT
    assert route.workflow_chain == [WorkflowType.REPORT_GENERATION]


def test_hiring_freeze_strategy_followup_stays_on_report_path() -> None:
    message = (
        "Can you help identify which specific roles are critical for immediate hiring and assess "
        "how a hiring freeze might impact team morale and project timelines?"
    )
    plan = plan_request(message)
    payload = AssistantQueryRequest(message=message, conversation_id="conv_test")
    route = classify_route(payload)

    assert plan.direct_workflow == WorkflowType.REPORT_GENERATION
    assert route.primary_intent == RouteFamily.REPORT
    assert route.workflow_chain == [WorkflowType.REPORT_GENERATION]


def test_mixed_document_and_planning_request_reaches_planner() -> None:
    payload = AssistantQueryRequest(
        message="Scan my inbox, compare this attached memo, and plan next week.",
        conversation_id="conv_test",
        attachments=[{"document_id": "doc_1", "filename": "memo.pdf"}],
    )

    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.PLAN
    assert route.request_plan is not None
    assert route.request_plan.mode == "compound_plan"
    assert "documents" in route.request_plan.needed_context_sources
    assert any(subtask.key == "review_documents" for subtask in route.request_plan.subtasks)


# ---------------------------------------------------------------------------
# 9. classify_route with precomputed semantics
# ---------------------------------------------------------------------------


def test_classify_route_keeps_generic_schedule_request_on_plan_path() -> None:
    payload = AssistantQueryRequest(message="Make me a schedule", conversation_id="conv_test")
    route = classify_route(payload)

    assert route.primary_intent == RouteFamily.PLAN
    assert route.workflow_chain == [WorkflowType.SCHEDULE_PLANNING]


def test_classify_route_uses_precomputed_semantics_without_replanning(monkeypatch) -> None:
    payload = AssistantQueryRequest(message="Make me a schedule", conversation_id="conv_test")
    bundle = build_turn_semantic_bundle(message=payload.message)

    monkeypatch.setattr("src.workflows.routing.plan_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("replanned")))
    monkeypatch.setattr("src.workflows.routing._classify_write_intent", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reclassified write intent")))

    route = classify_route(
        payload,
        precomputed_request_plan=bundle.request_plan,
        precomputed_write_intent=bundle.write_intent,
    )

    assert route.primary_intent == RouteFamily.PLAN
    assert route.workflow_chain == [WorkflowType.SCHEDULE_PLANNING]


# ---------------------------------------------------------------------------
# 10. Runner semantics & action semantics
# ---------------------------------------------------------------------------


def test_runner_semantics_identifies_execution_capability_question_and_channels() -> None:
    signals = classify_runner_semantics(
        message=(
            "Tell me explicitly whether you can send it and schedule the follow-up call, "
            "or if you cannot execute those actions, say so immediately."
        )
    )

    assert signals.direct_capability_question is True
    assert signals.explicit_execution_request is True
    assert set(signals.requested_channels) == {"email", "calendar"}


def test_action_semantics_detects_external_delivery_request() -> None:
    signals = classify_action_semantics(message="Please share with external counsel for sign off.")

    assert signals.external_delivery_requested is True


def test_runner_semantics_keeps_generic_schedule_as_planning_not_execution() -> None:
    signals = classify_runner_semantics(message="Make me a schedule")

    assert signals.request_plan_direct_workflow == WorkflowType.SCHEDULE_PLANNING
    assert signals.explicit_execution_request is False
    assert signals.requested_channels == ()


def test_runner_semantics_does_not_treat_schedule_with_email_calendar_context_as_email_action() -> None:
    signals = classify_runner_semantics(
        message="Make me a schedule for next week based off my calender and email"
    )

    assert signals.request_plan_direct_workflow == WorkflowType.SCHEDULE_PLANNING
    assert signals.explicit_execution_request is False
    assert signals.requested_channels == ()


def test_action_semantics_does_not_treat_schedule_with_email_calendar_context_as_email_action() -> None:
    signals = classify_action_semantics(
        message="Make me a schedule for next week based off my calender and email",
        workflow_preference=WorkflowType.SCHEDULE_PLANNING,
    )

    assert signals.explicit_execution_request is False
    assert signals.email_action is False
    assert signals.calendar_action is False


def test_detect_artifact_type_from_request_does_not_hijack_generic_schedule() -> None:
    from src.workflows.runner import detect_artifact_type_from_request

    assert detect_artifact_type_from_request("Make me a schedule") is None


# ---------------------------------------------------------------------------
# 11. Intent state (parse_turn_intent)
# ---------------------------------------------------------------------------


def test_parse_turn_intent_preserves_schedule_planning_without_write_action(monkeypatch) -> None:
    class _NoLLM:
        def complete_structured(self, *args, **kwargs):
            raise RuntimeError("llm unavailable in unit test")

    monkeypatch.setattr("src.workflows.intent_state.LLMClient", lambda: _NoLLM())
    parsed = parse_turn_intent(
        message="Make me a schedule",
        previous_state=None,
        artifact_context=None,
    )

    assert parsed.workflow_preference == WorkflowType.SCHEDULE_PLANNING
    assert parsed.write_action_requested is False


def test_parse_turn_intent_preserves_schedule_planning_for_compound_schedule_request(monkeypatch) -> None:
    class _NoLLM:
        def complete_structured(self, *args, **kwargs):
            raise RuntimeError("llm unavailable in unit test")

    monkeypatch.setattr("src.workflows.intent_state.LLMClient", lambda: _NoLLM())
    parsed = parse_turn_intent(
        message="Make me a schedule for next week based off my calender and email",
        previous_state=None,
        artifact_context=None,
    )

    assert parsed.workflow_preference == WorkflowType.SCHEDULE_PLANNING
    assert parsed.write_action_requested is False


def test_parse_turn_intent_uses_precomputed_request_plan_without_replanning(monkeypatch) -> None:
    class _NoLLM:
        def complete_structured(self, *args, **kwargs):
            raise RuntimeError("llm unavailable in unit test")

    precomputed_plan = plan_request("Make me a schedule", has_attachments=False)
    monkeypatch.setattr("src.workflows.intent_state.LLMClient", lambda: _NoLLM())
    monkeypatch.setattr("src.workflows.intent_state.plan_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("replanned")))

    parsed = parse_turn_intent(
        message="Make me a schedule",
        previous_state=None,
        artifact_context=None,
        precomputed_request_plan=precomputed_plan,
    )

    assert parsed.workflow_preference == WorkflowType.SCHEDULE_PLANNING
    assert parsed.write_action_requested is False


# ---------------------------------------------------------------------------
# 12. Routing disambiguation: recap vs morning vs schedule
# ---------------------------------------------------------------------------


def test_weekly_recap_does_not_route_to_morning_brief() -> None:
    for prompt in ("recap my week", "weekly recap", "week in review"):
        payload = AssistantQueryRequest(message=prompt, conversation_id="c1")
        route = classify_route(payload)
        assert WorkflowType.MORNING_BRIEF not in route.workflow_chain, f"Prompt: {prompt!r}"
        assert WorkflowType.WEEKLY_RECAP in route.workflow_chain, f"Prompt: {prompt!r}"


def test_schedule_does_not_route_to_recap() -> None:
    payload = AssistantQueryRequest(message="Plan my week based on my emails and meetings.", conversation_id="c1")
    route = classify_route(payload)
    assert WorkflowType.WEEKLY_RECAP not in route.workflow_chain


def test_morning_brief_does_not_route_to_recap() -> None:
    payload = AssistantQueryRequest(message="Give me my morning brief.", conversation_id="c1")
    route = classify_route(payload)
    assert WorkflowType.WEEKLY_RECAP not in route.workflow_chain

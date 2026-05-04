"""
Tests for WatchContextAssembler and the refactored runner._resolve_workflow_definition.

Validates:
  1. Each workflow type fetches only the provider(s) it needs
  2. Email-only workflows receive the raw email event (no calendar fetch)
  3. Calendar-only workflows receive the raw calendar event (no email fetch)
  4. Compound workflows merge email + calendar via build_watch_event_payload
  5. Document-compound workflows include document_context in the payload
  6. Provider errors are silenced and return {} per-source
  7. runner._resolve_workflow_definition returns the correct workflow def + metadata
  8. New workflow types (weekly_recap, meeting_prep, schedule_planning) all resolve
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

# Import agents package first to avoid circular init via context_loading.py
from src.agents.briefing_agent import BriefingAgent  # noqa: F401

from src.api.schemas import AssistantQueryRequest
from src.core.models import User
from src.integrations.providers import ProviderIntegrationError
from src.workflows.calendar_briefing import CALENDAR_BRIEFING_WORKFLOW
from src.workflows.document_explanation import DOCUMENT_EXPLANATION_WORKFLOW
from src.workflows.email_ingestion import EMAIL_INGESTION_WORKFLOW
from src.workflows.email_watcher import EMAIL_WATCHER_WORKFLOW
from src.workflows.meeting_prep import MEETING_PREP_WORKFLOW
from src.workflows.morning_brief import MORNING_BRIEF_WORKFLOW
from src.workflows.report_generation import REPORT_GENERATION_WORKFLOW
from src.workflows.routing import RouteDecision, RouteFamily, RouteSubintent
from src.workflows.runner import AssistantWorkflowRunner, _WORKFLOW_REGISTRY
from src.workflows.schedule_planning import SCHEDULE_PLANNING_WORKFLOW
from src.workflows.types import WorkflowType
from src.workflows.watch_context import WATCH_PLAN_WORKFLOWS, WatchContextAssembler
from src.workflows.weekly_recap import WEEKLY_RECAP_WORKFLOW

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_DEMO_EMAIL = {
    "ranked_threads": [{"subject": "Budget review", "importance_level": "high"}],
    "structured_watch": {"asks": [], "deadlines": [], "implied_docs": []},
}
_DEMO_CALENDAR = {
    "title": "Q1 Board Meeting",
    "upcoming_events": [{"title": "Q1 Board Meeting", "starts_at": "2026-03-21T10:00:00"}],
}


def _user() -> User:
    return User(id=1, username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="TestCo")


def _payload(message: str = "test", attachments=None) -> AssistantQueryRequest:
    return AssistantQueryRequest(message=message, conversation_id="conv:ceo_test:primary", attachments=attachments or [])


def _route(workflow_type: str) -> RouteDecision:
    from src.workflows.planning_types import RequestPlan
    plan = RequestPlan(mode="direct_workflow", target_workflow=workflow_type, direct_workflow=workflow_type)
    return RouteDecision(
        primary_intent=RouteFamily.WATCH,
        subintents=[RouteSubintent.EMAIL_WATCH],
        workflow_chain=[workflow_type],
        request_plan=plan,
        rationale="test",
    )


def _assembler(*, email=None, calendar=None) -> WatchContextAssembler:
    return WatchContextAssembler(
        email_fetcher=email or (lambda _: _DEMO_EMAIL),
        calendar_fetcher=calendar or (lambda _: _DEMO_CALENDAR),
    )


# ---------------------------------------------------------------------------
# WatchContextAssembler — provider call isolation
# ---------------------------------------------------------------------------

class TestAssemblerProviderIsolation:
    def test_email_only_fetches_email_not_calendar(self):
        calendar_called = []
        assembler = _assembler(calendar=lambda ceo_id: calendar_called.append(ceo_id) or _DEMO_CALENDAR)
        result = assembler.build(
            workflow_type=WorkflowType.EMAIL_WATCHER,
            payload=_payload(),
            current_user=_user(),
            route_decision=_route(WorkflowType.EMAIL_WATCHER),
        )
        assert result == _DEMO_EMAIL
        assert calendar_called == []

    def test_email_ingestion_fetches_email_not_calendar(self):
        calendar_called = []
        assembler = _assembler(calendar=lambda ceo_id: calendar_called.append(ceo_id) or _DEMO_CALENDAR)
        assembler.build(
            workflow_type=WorkflowType.EMAIL_INGESTION,
            payload=_payload(),
            current_user=_user(),
            route_decision=_route(WorkflowType.EMAIL_INGESTION),
        )
        assert calendar_called == []

    def test_calendar_only_fetches_calendar_not_email(self):
        email_called = []
        assembler = _assembler(email=lambda ceo_id: email_called.append(ceo_id) or _DEMO_EMAIL)
        result = assembler.build(
            workflow_type=WorkflowType.CALENDAR_BRIEFING,
            payload=_payload(),
            current_user=_user(),
            route_decision=_route(WorkflowType.CALENDAR_BRIEFING),
        )
        assert result == _DEMO_CALENDAR
        assert email_called == []

    def test_compound_workflow_fetches_both(self):
        email_called = []
        calendar_called = []
        assembler = _assembler(
            email=lambda ceo_id: email_called.append(ceo_id) or _DEMO_EMAIL,
            calendar=lambda ceo_id: calendar_called.append(ceo_id) or _DEMO_CALENDAR,
        )
        assembler.build(
            workflow_type=WorkflowType.MORNING_BRIEF,
            payload=_payload("morning brief"),
            current_user=_user(),
            route_decision=_route(WorkflowType.MORNING_BRIEF),
        )
        assert len(email_called) == 1
        assert len(calendar_called) == 1

    @pytest.mark.parametrize("workflow_type", [
        WorkflowType.MORNING_BRIEF,
        WorkflowType.SCHEDULE_PLANNING,
        WorkflowType.MEETING_PREP,
        WorkflowType.WEEKLY_RECAP,
    ])
    def test_all_compound_workflows_fetch_both_sources(self, workflow_type):
        email_called = []
        calendar_called = []
        assembler = _assembler(
            email=lambda ceo_id: email_called.append(ceo_id) or _DEMO_EMAIL,
            calendar=lambda ceo_id: calendar_called.append(ceo_id) or _DEMO_CALENDAR,
        )
        assembler.build(
            workflow_type=workflow_type,
            payload=_payload("test query"),
            current_user=_user(),
            route_decision=_route(workflow_type),
        )
        assert len(email_called) == 1, f"{workflow_type}: expected 1 email fetch"
        assert len(calendar_called) == 1, f"{workflow_type}: expected 1 calendar fetch"


# ---------------------------------------------------------------------------
# WatchContextAssembler — payload structure
# ---------------------------------------------------------------------------

class TestAssemblerPayloadStructure:
    def test_compound_payload_has_ranked_threads(self):
        assembler = _assembler()
        result = assembler.build(
            workflow_type=WorkflowType.MORNING_BRIEF,
            payload=_payload("give me my morning brief"),
            current_user=_user(),
            route_decision=_route(WorkflowType.MORNING_BRIEF),
        )
        assert "ranked_threads" in result
        assert "upcoming_events" in result
        assert "structured_watch" in result

    def test_compound_payload_includes_route_decision(self):
        assembler = _assembler()
        result = assembler.build(
            workflow_type=WorkflowType.WEEKLY_RECAP,
            payload=_payload("recap this week"),
            current_user=_user(),
            route_decision=_route(WorkflowType.WEEKLY_RECAP),
        )
        assert "route_decision" in result

    def test_document_compound_includes_document_context_when_attachments_present(self):
        from src.api.schemas import AttachmentRef
        attachment = AttachmentRef(document_id="doc_1", filename="Q1.pdf")
        payload = _payload("plan my week", attachments=[attachment])
        assembler = _assembler()
        result = assembler.build(
            workflow_type=WorkflowType.SCHEDULE_PLANNING,
            payload=payload,
            current_user=_user(),
            route_decision=_route(WorkflowType.SCHEDULE_PLANNING),
        )
        assert "document_context" in result
        assert result["document_context"]["attachment_count"] == 1

    def test_document_compound_no_document_context_without_attachments(self):
        assembler = _assembler()
        result = assembler.build(
            workflow_type=WorkflowType.SCHEDULE_PLANNING,
            payload=_payload("plan my day"),
            current_user=_user(),
            route_decision=_route(WorkflowType.SCHEDULE_PLANNING),
        )
        assert "document_context" not in result

    def test_morning_brief_does_not_get_document_context(self):
        from src.api.schemas import AttachmentRef
        attachment = AttachmentRef(document_id="doc_1", filename="Q1.pdf")
        payload = _payload("morning brief", attachments=[attachment])
        assembler = _assembler()
        result = assembler.build(
            workflow_type=WorkflowType.MORNING_BRIEF,
            payload=payload,
            current_user=_user(),
            route_decision=_route(WorkflowType.MORNING_BRIEF),
        )
        # morning_brief is compound but not in _DOCUMENT_COMPOUND_WORKFLOWS
        assert "document_context" not in result


# ---------------------------------------------------------------------------
# WatchContextAssembler — provider error handling
# ---------------------------------------------------------------------------

class TestAssemblerErrorHandling:
    def test_email_error_returns_empty_dict(self):
        def fail_email(_): raise ProviderIntegrationError("no email")
        assembler = _assembler(email=fail_email)
        result = assembler.build(
            workflow_type=WorkflowType.EMAIL_WATCHER,
            payload=_payload(),
            current_user=_user(),
            route_decision=_route(WorkflowType.EMAIL_WATCHER),
        )
        assert result == {}

    def test_calendar_error_returns_empty_dict(self):
        def fail(_):
            raise ProviderIntegrationError("no calendar")
        assembler = _assembler(calendar=fail)
        result = assembler.build(
            workflow_type=WorkflowType.CALENDAR_BRIEFING,
            payload=_payload(),
            current_user=_user(),
            route_decision=_route(WorkflowType.CALENDAR_BRIEFING),
        )
        assert result == {}

    def test_compound_email_error_still_builds_payload(self):
        def fail_email(_): raise ProviderIntegrationError("no email")
        assembler = _assembler(email=fail_email)
        result = assembler.build(
            workflow_type=WorkflowType.MORNING_BRIEF,
            payload=_payload("morning brief"),
            current_user=_user(),
            route_decision=_route(WorkflowType.MORNING_BRIEF),
        )
        # Should not raise — returns partial payload with empty email source
        assert isinstance(result, dict)
        assert "ranked_threads" in result  # ranked_threads will be [] from empty email event

    def test_assembler_raises_for_non_watch_workflow(self):
        assembler = _assembler()
        with pytest.raises(ValueError, match="non-watch workflow"):
            assembler.build(
                workflow_type=WorkflowType.REPORT_GENERATION,
                payload=_payload(),
                current_user=_user(),
                route_decision=_route(WorkflowType.REPORT_GENERATION),
            )


# ---------------------------------------------------------------------------
# Workflow registry completeness
# ---------------------------------------------------------------------------

class TestWorkflowRegistry:
    def test_registry_covers_all_watch_plan_workflows(self):
        missing = WATCH_PLAN_WORKFLOWS - set(_WORKFLOW_REGISTRY.keys())
        assert not missing, f"Workflow types missing from registry: {missing}"

    def test_registry_has_report_and_document_workflows(self):
        assert WorkflowType.REPORT_GENERATION in _WORKFLOW_REGISTRY
        assert WorkflowType.DOCUMENT_EXPLANATION in _WORKFLOW_REGISTRY

    @pytest.mark.parametrize("workflow_type, expected_def", [
        (WorkflowType.EMAIL_WATCHER, EMAIL_WATCHER_WORKFLOW),
        (WorkflowType.EMAIL_INGESTION, EMAIL_INGESTION_WORKFLOW),
        (WorkflowType.CALENDAR_BRIEFING, CALENDAR_BRIEFING_WORKFLOW),
        (WorkflowType.MORNING_BRIEF, MORNING_BRIEF_WORKFLOW),
        (WorkflowType.SCHEDULE_PLANNING, SCHEDULE_PLANNING_WORKFLOW),
        (WorkflowType.MEETING_PREP, MEETING_PREP_WORKFLOW),
        (WorkflowType.WEEKLY_RECAP, WEEKLY_RECAP_WORKFLOW),
        (WorkflowType.DOCUMENT_EXPLANATION, DOCUMENT_EXPLANATION_WORKFLOW),
        (WorkflowType.REPORT_GENERATION, REPORT_GENERATION_WORKFLOW),
    ])
    def test_registry_maps_to_correct_definition(self, workflow_type, expected_def):
        assert _WORKFLOW_REGISTRY[workflow_type] is expected_def


# ---------------------------------------------------------------------------
# runner._resolve_workflow_definition dispatch
# ---------------------------------------------------------------------------

class TestRunnerDispatch:
    def _runner(self) -> AssistantWorkflowRunner:
        runner = AssistantWorkflowRunner.__new__(AssistantWorkflowRunner)
        runner.assembler = _assembler()
        runner.router = MagicMock()
        return runner

    @pytest.mark.parametrize("workflow_type", list(WATCH_PLAN_WORKFLOWS))
    def test_watch_plan_workflows_return_event_payload(self, workflow_type):
        runner = self._runner()
        definition, extra = runner._resolve_workflow_definition(
            workflow_type=workflow_type,
            payload=_payload("test"),
            current_user=_user(),
            route_decision=_route(workflow_type),
        )
        assert definition is _WORKFLOW_REGISTRY[workflow_type]
        assert extra is not None
        assert "event_payload" in extra

    def test_report_generation_returns_no_extra_metadata(self):
        runner = self._runner()
        definition, extra = runner._resolve_workflow_definition(
            workflow_type=WorkflowType.REPORT_GENERATION,
            payload=_payload("give me a report"),
            current_user=_user(),
            route_decision=_route(WorkflowType.REPORT_GENERATION),
        )
        assert definition is REPORT_GENERATION_WORKFLOW
        assert extra is None

    def test_document_explanation_returns_no_event_payload(self):
        runner = self._runner()
        definition, extra = runner._resolve_workflow_definition(
            workflow_type=WorkflowType.DOCUMENT_EXPLANATION,
            payload=_payload("explain this contract"),
            current_user=_user(),
            route_decision=_route(WorkflowType.DOCUMENT_EXPLANATION),
        )
        assert definition is DOCUMENT_EXPLANATION_WORKFLOW
        assert extra is None

    def test_unknown_workflow_falls_back_to_report(self):
        runner = self._runner()
        definition, extra = runner._resolve_workflow_definition(
            workflow_type="some_future_workflow_type",
            payload=_payload("test"),
            current_user=_user(),
            route_decision=_route("some_future_workflow_type"),
        )
        assert definition is REPORT_GENERATION_WORKFLOW
        assert extra is None


# ---------------------------------------------------------------------------
# context_loading coverage for new workflow types
# ---------------------------------------------------------------------------

class TestContextLoadingCoverage:
    def test_new_briefing_types_get_event_briefing_stage_definitions(self):
        from src.workflows.context_loading import (
            EVENT_BRIEFING_CONTEXT_STAGE_DEFINITIONS,
            get_context_stage_definitions,
        )
        for workflow_type in (
            "email_watcher",
            "weekly_recap",
            "meeting_prep",
            "schedule_planning",
        ):
            result = get_context_stage_definitions(workflow_type)
            assert result is EVENT_BRIEFING_CONTEXT_STAGE_DEFINITIONS, (
                f"{workflow_type} should use EVENT_BRIEFING_CONTEXT_STAGE_DEFINITIONS"
            )

    def test_original_briefing_types_still_covered(self):
        from src.workflows.context_loading import (
            EVENT_BRIEFING_CONTEXT_STAGE_DEFINITIONS,
            get_context_stage_definitions,
        )
        for workflow_type in (
            "email_ingestion",
            "calendar_briefing",
            "morning_brief",
            "schedule_planning",
        ):
            result = get_context_stage_definitions(workflow_type)
            assert result is EVENT_BRIEFING_CONTEXT_STAGE_DEFINITIONS

    def test_load_signals_uses_get_recent_signals_for_all_briefing_types(self):
        from src.workflows.context_loading import build_context_stage_actions
        for workflow_type in (
            "email_watcher",
            "weekly_recap",
            "meeting_prep",
            "schedule_planning",
            "email_ingestion",
            "morning_brief",
        ):
            actions = build_context_stage_actions(workflow_type, "load_signals", "test input")
            assert len(actions) == 1
            assert actions[0].target == "get_recent_signals", (
                f"{workflow_type}: expected get_recent_signals"
            )

    def test_finalize_context_prepare_context_uses_briefing_path_for_new_types(self):
        from src.workflows.context_loading import finalize_context_stage
        context = {
            "company_state": {},
            "preferences": {},
            "retrieval": [],
            "history": [],
            "signals": [],
            "event_payload": {"ranked_threads": []},
        }
        for workflow_type in ("email_watcher", "weekly_recap", "meeting_prep", "schedule_planning"):
            result = finalize_context_stage(workflow_type, "prepare_context", context)
            assert "prepared_context" in result, f"{workflow_type}: missing prepared_context"
            assert "event_payload" in result["prepared_context"], (
                f"{workflow_type}: prepared_context missing event_payload"
            )

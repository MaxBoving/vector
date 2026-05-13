"""
Empty-provider smoke test.

Drives the full path from provider failure → WatchContextAssembler → event payload
→ BriefingAgent → rendered output for every compound watch workflow.

A single test that would catch the whole class of null-propagation bug:
explicit None values stored in payloads, garbage strings like "• None" or
"Cross-referenced by None", and crashes from list(None).

Does NOT mock individual methods — exercises the real code path with both
providers returning {} (the error case from WatchContextAssembler._safe_*).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agents.briefing_agent import BriefingAgent
from src.integrations.providers import ProviderIntegrationError
from src.workflows.watch_context import WatchContextAssembler, WATCH_PLAN_WORKFLOWS
from src.workflows.types import WorkflowType
from src.api.schemas import AssistantQueryRequest
from src.core.models import User
from src.workflows.routing import RouteDecision, RouteFamily, RouteSubintent
from src.workflows.planning_types import RequestPlan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user() -> User:
    return User(id=1, username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="TestCo")


def _payload(message: str = "test") -> AssistantQueryRequest:
    return AssistantQueryRequest(message=message, conversation_id="conv:ceo_test:primary")


def _route(workflow_type: str) -> RouteDecision:
    plan = RequestPlan(mode="direct_workflow", target_workflow=workflow_type, direct_workflow=workflow_type)
    return RouteDecision(
        primary_intent=RouteFamily.WATCH,
        subintents=[RouteSubintent.EMAIL_WATCH],
        workflow_chain=[workflow_type],
        request_plan=plan,
        rationale="smoke test",
    )


def _failing_provider(_ceo_id: str) -> dict:
    raise ProviderIntegrationError("provider unavailable")


def _assembler_both_fail() -> WatchContextAssembler:
    return WatchContextAssembler(
        email_fetcher=_failing_provider,
        calendar_fetcher=_failing_provider,
    )


def _no_garbage(text: str) -> list[str]:
    """Return any garbage substrings found in text."""
    needles = ["• None", ": None", "from None", "by None", " None.", " None,"]
    return [n for n in needles if n in text]


# ---------------------------------------------------------------------------
# Smoke test — all watch workflow types with both providers failing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("workflow_type", sorted(WATCH_PLAN_WORKFLOWS))
def test_empty_provider_produces_valid_output_no_garbage(workflow_type: str) -> None:
    """
    Full path: both providers fail → assembler returns {} for email and calendar
    → build_watch_event_payload → BriefingAgent._generate_payload + _apply_presentation_metadata
    → no crashes, no 'None' strings in output.
    """
    assembler = _assembler_both_fail()
    event_payload = assembler.build(
        workflow_type=workflow_type,
        payload=_payload("smoke test query"),
        current_user=_user(),
        route_decision=_route(workflow_type),
    )

    # Payload must be a dict — never None, never raises
    assert isinstance(event_payload, dict), f"{workflow_type}: event_payload is not a dict"

    # No optional fields stored as explicit None (would poison .get(key, default))
    none_keys = [k for k, v in event_payload.items() if v is None]
    assert not none_keys, f"{workflow_type}: explicit None values in payload keys: {none_keys}"

    # Drive through the full agent rendering path
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    brief = agent._generate_payload(  # type: ignore[attr-defined]
        workflow_type=workflow_type,
        event_payload=event_payload,
        prepared_context={},
        completion=None,
    )
    brief = agent._apply_presentation_metadata(  # type: ignore[attr-defined]
        brief,
        event_payload=event_payload,
        workflow_type=workflow_type,
    )

    # Must produce a valid title and summary
    assert brief.answer.title, f"{workflow_type}: answer.title is empty"
    assert brief.answer.summary, f"{workflow_type}: answer.summary is empty"
    assert brief.presentation is not None, f"{workflow_type}: presentation is None"

    # Serialize the whole payload and check for garbage strings
    serialized = json.dumps(brief.model_dump(), default=str)
    garbage = _no_garbage(serialized)
    assert not garbage, (
        f"{workflow_type}: garbage strings {garbage} found in output.\n"
        f"Serialized (first 500 chars): {serialized[:500]}"
    )

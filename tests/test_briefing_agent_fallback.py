from __future__ import annotations

from types import SimpleNamespace
import asyncio

import pytest

from src.agents.briefing_agent import (
    BriefAnswer,
    BriefPresentation,
    BriefPayload,
    BriefTrust,
    BriefingAgent,
)
from src.agents.schemas import AgentInput, tool_action
from src.presentation import PresentationQualityResult, PresentationSpec
from src.runtime.state import WorkflowStageState, WorkflowState, WorkflowStatus


def _workflow_state(workflow_type: str, metadata: dict[str, object] | None = None) -> WorkflowState:
    return WorkflowState(
        workflow_id=f"wf:{workflow_type}",
        workflow_type=workflow_type,
        ceo_id="ceo_test",
        company_name="TestCo",
        status=WorkflowStatus.RUNNING,
        current_stage="synthesizer",
        stages=[WorkflowStageState(name="synthesizer")],
        metadata=metadata or {},
    )


def _brief_payload(title: str, summary: str) -> BriefPayload:
    return BriefPayload(
        answer=BriefAnswer(title=title, summary=summary),
        trust=BriefTrust(
            confidence="medium",
            confidence_score=0.5,
            assumptions=[],
            open_questions=[],
            data_quality="medium",
        ),
        sources=[],
        presentation=BriefPresentation(mode="brief", summary=summary),
    )


@pytest.mark.parametrize(
    ("workflow_type", "expected_question", "expected_first", "expected_second"),
    [
        ("schedule_planning", "Do you want this as a timeline or a compact list?", "timeline", "list_form"),
        ("day_schedule_planning", "Do you want this as a timeline or a compact list?", "timeline", "list_form"),
        ("week_schedule_planning", "Do you want this as a timeline or a compact list?", "timeline", "list_form"),
        ("weekly_recap", "Do you want this as a list recap or a narrative recap?", "list_form", "narrative_recap"),
        ("morning_brief", "Do you want this as a compact brief or a narrative recap?", "list_form", "narrative_recap"),
    ],
)
def test_briefing_workflows_ask_for_presentation_style_until_learned(
    monkeypatch,
    workflow_type: str,
    expected_question: str,
    expected_first: str,
    expected_second: str,
) -> None:
    agent = BriefingAgent(SimpleNamespace())
    workflow_state = _workflow_state(workflow_type, {"event_payload": {}})
    agent_input = AgentInput(
        workflow_state=workflow_state,
        stage="synthesizer",
        task_input="Plan my week with inbox and calendar context.",
        context={},
        metadata={},
    )

    monkeypatch.setattr("src.core.database.get_learned_preference", lambda *args, **kwargs: None)

    result = asyncio.run(agent.run(agent_input))

    assert result.metadata["response_type"] == "clarification"
    assert result.metadata["needs_clarification"] is True
    assert result.structured_output["presentation"]["preamble"].startswith(expected_question)
    assert result.metadata["clarification_options"][0]["value"] == expected_first
    assert result.metadata["clarification_options"][1]["value"] == expected_second


def test_schedule_planning_skips_presentation_gate_when_learned(monkeypatch) -> None:
    agent = BriefingAgent(SimpleNamespace())
    workflow_state = _workflow_state("schedule_planning", {"event_payload": {}})
    agent_input = AgentInput(
        workflow_state=workflow_state,
        stage="synthesizer",
        task_input="Plan my week with inbox and calendar context.",
        context={},
        metadata={},
    )

    monkeypatch.setattr("src.core.database.get_learned_preference", lambda *args, **kwargs: "timeline")
    monkeypatch.setattr(agent, "_detect_schedule_ambiguity", lambda **kwargs: [])
    monkeypatch.setattr(agent, "_has_live_event_context", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_generate_payload", lambda **kwargs: _brief_payload("Executive Schedule", "Fallback schedule"))
    monkeypatch.setattr(agent, "_apply_presentation_metadata", lambda payload, **kwargs: payload)
    monkeypatch.setattr(
        agent,
        "_build_presentation_spec",
        lambda **kwargs: PresentationSpec(title="Executive Schedule", executive_summary="Fallback schedule"),
    )
    monkeypatch.setattr(
        "src.agents.briefing_agent.normalize_and_validate_presentation_spec",
        lambda spec: (spec, PresentationQualityResult()),
    )
    monkeypatch.setattr(agent, "_to_markdown", lambda payload: "# Executive Schedule\nFallback schedule")
    monkeypatch.setattr(agent, "_extract_memory_save_actions", lambda payload: [])
    monkeypatch.setattr(agent, "_build_thread_entry_action", lambda **kwargs: tool_action("record_thread_entry"))
    monkeypatch.setattr(agent, "_extract_situational_updates", lambda **kwargs: None)

    result = asyncio.run(agent.run(agent_input))

    assert result.metadata["response_type"] == "report"
    assert result.summary == "Requesting structured briefing completion."


def test_morning_brief_uses_local_fallback_without_completion_tool(monkeypatch) -> None:
    agent = BriefingAgent(SimpleNamespace())
    workflow_state = _workflow_state("morning_brief", {"event_payload": {}})
    agent_input = AgentInput(
        workflow_state=workflow_state,
        stage="synthesizer",
        task_input="Give me my morning brief.",
        context={},
        metadata={},
    )

    monkeypatch.setattr("src.core.database.get_learned_preference", lambda *args, **kwargs: "list_form")
    monkeypatch.setattr(agent, "_has_live_event_context", lambda *args, **kwargs: False)
    monkeypatch.setattr(agent, "_generate_payload", lambda **kwargs: _brief_payload("Morning Brief", "Local morning brief"))
    monkeypatch.setattr(agent, "_apply_presentation_metadata", lambda payload, **kwargs: payload)
    monkeypatch.setattr(
        agent,
        "_build_presentation_spec",
        lambda **kwargs: PresentationSpec(title="Morning Brief", executive_summary="Local morning brief"),
    )
    monkeypatch.setattr(
        "src.agents.briefing_agent.normalize_and_validate_presentation_spec",
        lambda spec: (spec, PresentationQualityResult()),
    )
    monkeypatch.setattr(agent, "_to_markdown", lambda payload: "# Morning Brief\nLocal morning brief")
    monkeypatch.setattr(agent, "_extract_memory_save_actions", lambda payload: [])
    monkeypatch.setattr(agent, "_build_thread_entry_action", lambda **kwargs: tool_action("record_thread_entry"))
    monkeypatch.setattr(agent, "_extract_situational_updates", lambda **kwargs: None)

    result = asyncio.run(agent.run(agent_input))

    assert result.metadata["response_type"] == "report"
    assert result.summary == "Local morning brief"
    assert all(action.target != "structured_completion" for action in result.actions)


def test_morning_brief_default_title_is_morning_specific() -> None:
    agent = BriefingAgent(SimpleNamespace())

    title = agent._default_title("morning_brief", {}, {})

    assert title.startswith("Morning Brief • ")


def test_morning_brief_presentation_variant_prefers_resolved_style() -> None:
    agent = BriefingAgent(SimpleNamespace())

    variant = agent._presentation_variant(  # type: ignore[attr-defined]
        "morning_brief",
        ceo_id="ceo_test",
        resolved_clarifications={"presentation_style": "narrative_recap"},
    )

    assert variant == "narrative_recap"


def test_morning_brief_next_actions_are_semantic_not_workflow_specific() -> None:
    agent = BriefingAgent(SimpleNamespace())

    follow_ups = agent._next_actions("morning_brief", [])  # type: ignore[attr-defined]

    assert follow_ups == ["Review the most important item."]

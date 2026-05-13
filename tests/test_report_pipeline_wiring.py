# tests/test_report_pipeline_wiring.py
from unittest.mock import MagicMock, patch

from src.agents.composition_plan import CompositionPlan, ReportCompletionWithPlan
from src.agents.report_agent import ReportAgent, ReportAnswer, ReportPayload, ReportSection, ReportTrust


def _make_plan_completion(labels: list[str]) -> ReportCompletionWithPlan:
    plan = CompositionPlan(
        section_labels=labels,
        context_gaps=[],
        output_modality="docx",
        capability_requires=[],
    )
    payload = ReportPayload(
        answer=ReportAnswer(
            title="Test",
            summary="Test summary",
            sections=[
                ReportSection(label="Wrong 1", items=["a"]),
                ReportSection(label="Wrong 2", items=["b"]),
                ReportSection(label="Wrong 3", items=["c"]),
            ],
        ),
        trust=ReportTrust(confidence="high", confidence_score=0.9, data_quality="high"),
    )
    return ReportCompletionWithPlan(plan=plan, payload=payload)


def test_generate_report_payload_uses_plan_labels():
    agent = ReportAgent(tools=None)  # type: ignore
    completion = _make_plan_completion(
        ["Competitive Position", "Margin Impact", "Strategic Options"]
    )
    payload, plan = agent._generate_report_payload(
        task_input="pricing analysis",
        company_state={},
        signals=[],
        retrieval=[],
        completion=completion.model_dump(),
    )
    assert payload.answer.sections[0].label == "Competitive Position"
    assert payload.answer.sections[1].label == "Margin Impact"
    assert payload.answer.sections[2].label == "Strategic Options"
    assert plan is not None
    assert plan.section_labels[0] == "Competitive Position"


def test_report_agent_asks_for_presentation_style_until_learned(monkeypatch):
    agent = ReportAgent(tools=None)  # type: ignore

    monkeypatch.setattr("src.core.database.get_learned_preference", lambda *args, **kwargs: None)

    gate = agent._presentation_style_gate(  # type: ignore[attr-defined]
        ceo_id="ceo_test",
        resolved_clarifications={},
    )

    assert gate is not None
    question, options = gate
    assert question == "Do you want this as a list form or a narrative recap?"
    assert options[0]["value"] == "list_form"
    assert options[1]["value"] == "narrative_recap"


def test_report_agent_skips_presentation_gate_when_learned(monkeypatch):
    agent = ReportAgent(tools=None)  # type: ignore

    monkeypatch.setattr("src.core.database.get_learned_preference", lambda *args, **kwargs: "list_form")

    gate = agent._presentation_style_gate(  # type: ignore[attr-defined]
        ceo_id="ceo_test",
        resolved_clarifications={},
    )

    assert gate is None

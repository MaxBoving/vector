from src.agents.composition_plan import CompositionPlan, ReportCompletionWithPlan
from src.agents.report_agent import ReportAnswer, ReportPayload, ReportSection, ReportTrust


def _make_payload() -> ReportPayload:
    return ReportPayload(
        answer=ReportAnswer(
            title="Test",
            summary="Test summary",
            sections=[ReportSection(label="S1", items=["item"])],
        ),
        trust=ReportTrust(
            confidence="high",
            confidence_score=0.9,
            data_quality="high",
        ),
    )


def test_composition_plan_requires_three_section_labels():
    plan = CompositionPlan(
        section_labels=["Competitive Position", "Margin Impact", "Strategic Options"],
        context_gaps=[],
        output_modality="docx",
        capability_requires=[],
    )
    assert len(plan.section_labels) == 3


def test_composition_plan_context_gaps_default_empty():
    plan = CompositionPlan(
        section_labels=["A", "B", "C"],
        context_gaps=[],
        output_modality="inline",
        capability_requires=[],
    )
    assert plan.context_gaps == []


def test_composition_plan_capability_requires_default_empty():
    plan = CompositionPlan(
        section_labels=["A", "B", "C"],
        context_gaps=[],
        output_modality="inline",
        capability_requires=[],
    )
    assert plan.capability_requires == []


def test_report_completion_with_plan_holds_both():
    plan = CompositionPlan(
        section_labels=["A", "B", "C"],
        context_gaps=[],
        output_modality="docx",
        capability_requires=[],
    )
    completion = ReportCompletionWithPlan(plan=plan, payload=_make_payload())
    assert completion.plan.output_modality == "docx"
    assert completion.payload.answer.title == "Test"

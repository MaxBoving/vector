# tests/test_output_normalizer.py
from src.agents.composition_plan import CompositionPlan
from src.agents.output_normalizer import OutputNormalizer
from src.agents.report_agent import ReportAnswer, ReportPayload, ReportSection, ReportTrust


def _make_plan(labels: list[str] | None = None) -> CompositionPlan:
    return CompositionPlan(
        section_labels=labels or ["Competitive Position", "Margin Impact", "Strategic Options"],
        context_gaps=[],
        output_modality="docx",
        capability_requires=[],
    )


def _make_payload(sections: list[ReportSection] | None = None) -> ReportPayload:
    return ReportPayload(
        answer=ReportAnswer(
            title="Test",
            summary="Test summary",
            sections=sections or [],
        ),
        trust=ReportTrust(confidence="high", confidence_score=0.9, data_quality="high"),
    )


def test_normalizer_applies_plan_labels_to_existing_sections():
    normalizer = OutputNormalizer()
    payload = _make_payload([
        ReportSection(label="Wrong Label 1", items=["item a"]),
        ReportSection(label="Wrong Label 2", items=["item b"]),
        ReportSection(label="Wrong Label 3", items=["item c"]),
    ])
    plan = _make_plan(["Competitive Position", "Margin Impact", "Strategic Options"])
    result = normalizer.normalize(payload, plan)
    assert result.answer.sections[0].label == "Competitive Position"
    assert result.answer.sections[1].label == "Margin Impact"
    assert result.answer.sections[2].label == "Strategic Options"


def test_normalizer_preserves_section_items():
    normalizer = OutputNormalizer()
    payload = _make_payload([
        ReportSection(label="Old", items=["real content here"]),
        ReportSection(label="Old2", items=["more content"]),
        ReportSection(label="Old3", items=["even more"]),
    ])
    plan = _make_plan(["A", "B", "C"])
    result = normalizer.normalize(payload, plan)
    assert result.answer.sections[0].items == ["real content here"]
    assert result.answer.sections[1].items == ["more content"]


def test_normalizer_pads_to_three_sections_when_fewer():
    normalizer = OutputNormalizer()
    payload = _make_payload([ReportSection(label="Only One", items=["item"])])
    plan = _make_plan(["A", "B", "C"])
    result = normalizer.normalize(payload, plan)
    assert len(result.answer.sections) == 3
    assert result.answer.sections[0].label == "A"
    assert result.answer.sections[1].label == "B"
    assert result.answer.sections[2].label == "C"


def test_normalizer_padded_sections_use_plan_labels_not_defaults():
    normalizer = OutputNormalizer()
    payload = _make_payload([])
    plan = _make_plan(["Risk Summary", "Recovery Actions", "Owner Assignments"])
    result = normalizer.normalize(payload, plan)
    assert result.answer.sections[0].label == "Risk Summary"
    assert result.answer.sections[1].label == "Recovery Actions"
    assert result.answer.sections[2].label == "Owner Assignments"


def test_normalizer_pads_items_to_minimum_three():
    normalizer = OutputNormalizer()
    payload = _make_payload([
        ReportSection(label="S1", items=["only one item"]),
        ReportSection(label="S2", items=[]),
        ReportSection(label="S3", items=["a", "b", "c"]),
    ])
    plan = _make_plan(["S1", "S2", "S3"])
    result = normalizer.normalize(payload, plan)
    assert len(result.answer.sections[0].items) >= 1  # preserves existing
    assert len(result.answer.sections[2].items) == 3  # exact 3 not truncated


def test_normalizer_does_not_truncate_more_than_three_sections():
    normalizer = OutputNormalizer()
    payload = _make_payload([
        ReportSection(label="S1", items=["a"]),
        ReportSection(label="S2", items=["b"]),
        ReportSection(label="S3", items=["c"]),
        ReportSection(label="S4", items=["d"]),
    ])
    plan = _make_plan(["A", "B", "C"])
    result = normalizer.normalize(payload, plan)
    # Only the first 3 get plan labels; extra sections preserved as-is
    assert len(result.answer.sections) >= 3


def test_normalizer_never_modifies_original_payload():
    normalizer = OutputNormalizer()
    original_label = "Original Label"
    payload = _make_payload([ReportSection(label=original_label, items=["item"])])
    plan = _make_plan(["New Label", "B", "C"])
    normalizer.normalize(payload, plan)
    assert payload.answer.sections[0].label == original_label  # unchanged

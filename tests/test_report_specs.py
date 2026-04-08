from src.agents.report_agent import ReportAgent, ReportAnswer, ReportPayload, ReportSection, ReportTrust
from src.presentation import DeckSpec, MemoSpec


def _make_agent() -> ReportAgent:
    return ReportAgent(tools=None)  # type: ignore[arg-type]


def _make_payload() -> ReportPayload:
    return ReportPayload(
        answer=ReportAnswer(
            title="Board Financial Update",
            summary="Liquidity remains stable while spend discipline tightened.",
            sections=[
                ReportSection(label="Key Findings", items=["Cash runway improved by 2.1 months."]),
                ReportSection(label="Business Implications", items=["Hiring pace can remain controlled."]),
                ReportSection(label="Recommended Actions", items=["Keep cloud spend controls in place."]),
            ],
        ),
        trust=ReportTrust(
            confidence="high",
            confidence_score=0.88,
            assumptions=["Board pack reflects current close assumptions."],
            open_questions=["Need final legal review of note terms."],
            data_quality="high",
        ),
        sources=[],
    )


def test_report_agent_emits_neutral_memo_spec_with_template_metadata() -> None:
    agent = _make_agent()
    memo_spec = agent._to_memo_spec(payload=_make_payload(), finance_template="board_financial_update")

    assert isinstance(memo_spec, MemoSpec)
    assert memo_spec.metadata["template_id"] == "board_memo_v1"
    assert memo_spec.metadata["theme_id"] == "board_formal"
    assert memo_spec.metadata["presentation_version"] == "memo_spec_v1"
    assert memo_spec.section_order[0] == "Executive Summary"


def test_finance_workbook_spec_includes_template_and_theme_metadata() -> None:
    agent = _make_agent()
    workbook_spec = agent._build_finance_workbook_spec(
        task_input="Generate a board financial update workbook",
        payload=_make_payload(),
        metrics=[],
        company_state={
            "revenue_segmentation": {"north america": 35_000_000},
            "cost_structure": {"aws": 4_500_000},
            "capital_position": {"cash at bank": 15_200_000},
        },
        ceo_id=None,
        current_interaction_id=None,
        session_history=[],
        retrieval=[],
    )

    assert workbook_spec.metadata["template_id"] == "finance_workbook_v1"
    assert workbook_spec.metadata["theme_id"] == "board_formal"
    assert workbook_spec.metadata["presentation_version"] == "workbook_spec_v1"


def test_report_agent_emits_neutral_deck_spec_with_template_metadata() -> None:
    agent = _make_agent()
    deck_spec = agent._to_deck_spec(
        task_input="Prepare a board deck for the next meeting",
        payload=_make_payload(),
        finance_template="board_financial_update",
    )

    assert isinstance(deck_spec, DeckSpec)
    assert deck_spec.metadata["template_id"] == "board_deck_v1"
    assert deck_spec.metadata["theme_id"] == "board_formal"
    assert deck_spec.metadata["presentation_version"] == "deck_spec_v1"
    assert deck_spec.slide_order[0] == "Title"
    assert any(slide.title == "Recommended Actions" for slide in deck_spec.slides)
    assert any(slide.title == "Executive Summary" for slide in deck_spec.slides)
    assert any(slide.title == "Key Metrics" and slide.kind == "metric" for slide in deck_spec.slides)
    assert any(slide.title == "Decision Points" and slide.kind == "decision" for slide in deck_spec.slides)


def test_meeting_prep_request_uses_meeting_prep_deck_template() -> None:
    agent = _make_agent()
    deck_spec = agent._to_deck_spec(
        task_input="Prepare me for tomorrow's exec staff meeting with a slide deck",
        payload=_make_payload(),
        finance_template=None,
    )

    assert deck_spec.metadata["template_id"] == "meeting_prep_deck_v1"
    assert any(slide.title == "Key Questions" for slide in deck_spec.slides)


def test_board_deck_request_selects_pptx_artifact_action() -> None:
    agent = _make_agent()
    output_modality, artifact_plan = agent._select_output_modality("Build a board deck and powerpoint for next week's review")

    assert output_modality == "pptx"
    assert artifact_plan[0].artifact_type == "report_pptx"


def test_plain_report_request_stays_inline_without_export_artifacts() -> None:
    agent = _make_agent()
    output_modality, artifact_plan = agent._select_output_modality("Give me a company health summary")

    assert output_modality == "inline"
    assert artifact_plan == []


def test_report_agent_build_artifact_actions_uses_typed_artifact_helpers() -> None:
    agent = _make_agent()
    actions = agent._build_artifact_actions(  # type: ignore[attr-defined]
        task_input="Create a board memo with a supporting financial analysis workbook for the board financial update.",
        payload=_make_payload(),
        finance_template="board_financial_update",
        company_state={
            "revenue_segmentation": {"north america": 35_000_000},
            "cost_structure": {"aws": 4_500_000},
            "capital_position": {"cash at bank": 15_200_000},
        },
        ceo_id="ceo_test",
        current_interaction_id=None,
        session_history=[],
        retrieval=[],
        markdown="# Executive Summary",
        output_modality="docx+xlsx",
        stage="synthesizer",
        finance_rows=None,
    )

    docx_action = next(action for action in actions if action.target == "create_docx_memo")
    workbook_action = next(action for action in actions if action.target == "create_workbook")

    assert sorted(docx_action.args.keys()) == [
        "artifact_stage",
        "filename",
        "format",
        "label",
        "memo_spec",
        "preview_filename",
        "preview_stage",
    ]
    assert docx_action.args["memo_spec"]["metadata"]["template_id"] == "board_memo_v1"

    assert sorted(workbook_action.args.keys()) == [
        "artifact_stage",
        "filename",
        "format",
        "label",
        "preview_filename",
        "preview_stage",
        "workbook_spec",
    ]
    assert workbook_action.args["workbook_spec"]["metadata"]["template_id"] == "finance_workbook_v1"


def test_report_agent_builds_clickable_clarification_options_for_finance_scope() -> None:
    agent = _make_agent()

    options = agent._clarification_options(  # type: ignore[attr-defined]
        task_input="What should I say about cloud spend variance in the board packet?",
        questions=["Do you want this framed for the board packet, the finance close meeting, or your own operating decision?"],
    )

    assert [option["label"] for option in options] == [
        "Board Packet",
        "Finance Close",
        "Operating Decision",
    ]
    assert all(option.get("apply_text") for option in options)


def test_gap_clarification_output_includes_pick_one_options() -> None:
    agent = _make_agent()

    output = agent._build_gap_clarification_output(  # type: ignore[attr-defined]
        task_input="What should I say about cloud spend variance in the board packet?",
        company_state={"company_name": "InnovateCorp"},
        questions=["Do you want this framed for the board packet, the finance close meeting, or your own operating decision?"],
        options=[
            {
                "label": "Board Packet",
                "value": "board_packet",
                "description": "Frame this as board-facing material.",
                "apply_text": "Frame this for the board packet.",
            }
        ],
    )

    assert output["answer"]["sections"][0]["label"] == "Pick One"
    assert output["answer"]["sections"][0]["items"] == ["Board Packet"]
    assert output["presentation"]["decision"]["recommended_option"] == "Board Packet"
    assert output["clarification_options"][0]["value"] == "board_packet"

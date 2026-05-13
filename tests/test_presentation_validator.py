from src.presentation.presentation_adapters import presentation_spec_to_deck_spec, presentation_spec_to_memo_spec
from src.presentation.presentation_contract import ChartIntentKind, PresentationBlock, PresentationSpec
from src.presentation.presentation_validator import normalize_and_validate_presentation_spec
from src.tools.artifact_requests import build_deck_payload, build_memo_payload


def test_presentation_validator_flags_placeholder_board_deck_content() -> None:
    spec = PresentationSpec(
        artifact_kind="board_deck",
        audience="board",
        intent="decide",
        title="Beta launch April 15 with 20 design partners drives $18-22M ARR potential, but cloud costs 29% over plan threaten Series C covenant breach by June.",
        executive_summary="Beta launch and cloud costs create a decision tradeoff before the board packet deadline.",
        recommendation="Approve reserved instances and preserve the April 15 launch.",
        blocks=[
            PresentationBlock(kind="headline", title="Title", bullets=["Beta launch overview"]),
            PresentationBlock(kind="analysis", title="Key Questions", bullets=["Confirm the key assumption behind the current recommendation."]),
        ],
    )

    normalized, quality = normalize_and_validate_presentation_spec(spec)

    assert normalized.title != spec.title
    assert any(item.startswith("placeholder_content") for item in quality.hard_failures)
    assert quality.presentation_ready is False
    assert normalized.blocks[1].title == "Decision Framing"
    assert normalized.blocks[1].bullets == []


def test_presentation_adapters_preserve_story_but_apply_format_rules() -> None:
    spec = PresentationSpec(
        artifact_kind="memo",
        audience="ceo",
        intent="inform",
        title="Northstar renewal recovery plan",
        executive_summary="Northstar needs an executive recovery path before April 15.",
        recommendation="CEO should call Northstar this week and assign product remediation ownership.",
        blocks=[
            PresentationBlock(kind="headline", title="Context", summary="Renewal is at risk.", bullets=["$1.8M ARR renewal expires April 15."]),
            PresentationBlock(kind="actions", title="Recommended Actions", bullets=["CEO to call the customer this week.", "Product to own remediation plan by Friday."]),
        ],
        assumptions=["Assumes contract date is firm."],
    )

    memo = presentation_spec_to_memo_spec(spec, template_id="board_memo_v1", theme_id="default")
    deck = presentation_spec_to_deck_spec(spec.model_copy(update={"artifact_kind": "board_deck", "audience": "board", "decision_required": "Approve the renewal recovery plan."}), template_id="board_deck_v1", theme_id="default")

    assert memo.title == "Northstar renewal recovery plan"
    assert memo.sections[0].label == "Context"
    assert deck.slides[0].title == "Title"
    assert deck.slides[1].title == "Recommendation"
    assert any(slide.title == "Decision Required" for slide in deck.slides)


def test_presentation_adapters_dedupe_duplicate_section_text() -> None:
    spec = PresentationSpec(
        artifact_kind="memo",
        audience="ceo",
        intent="inform",
        title="Duplicate guard",
        executive_summary="The summary should not repeat in the highlights.",
        blocks=[
            PresentationBlock(
                kind="analysis",
                title="Highlights",
                summary="The same highlight sentence.",
                bullets=["The same highlight sentence.", "A second distinct point."],
            ),
        ],
    )

    memo = presentation_spec_to_memo_spec(spec, template_id="board_memo_v1", theme_id="default")

    assert memo.sections[0].items == [
        "The same highlight sentence.",
        "A second distinct point.",
    ]


def test_presentation_validator_normalizes_chart_requests_and_validation() -> None:
    spec = PresentationSpec(
        artifact_kind="financial_analysis",
        audience="ceo",
        intent="inform",
        title="Finance chart request",
        executive_summary="Show the trend.",
        charts=[
            {
                "kind": ChartIntentKind.TREND,
                "title": "Revenue Trend",
                "purpose": "Show revenue over time.",
                "x_axis": "Period",
                "y_axis": "Actual",
                "series": ["actual", "budget", "actual"],
                "required": False,
            }
        ],
        metadata={"available_chart_series": ["actual", "budget", "forecast"]},
    )

    normalized, quality = normalize_and_validate_presentation_spec(spec)

    assert normalized.charts[0].title == "Revenue Trend"
    assert normalized.charts[0].series == ["actual", "budget"]
    assert quality.chart_validation is not None
    assert quality.chart_validation.supported is True
    assert quality.chart_validation.available_series == ["actual", "budget", "forecast"]


def test_artifact_payload_builders_accept_presentation_spec_directly() -> None:
    presentation_spec = PresentationSpec(
        artifact_kind="board_deck",
        audience="board",
        intent="decide",
        title="Cloud spend decision before April 15",
        executive_summary="Cloud spend and beta launch timing need a board-safe recommendation.",
        recommendation="Approve reserved instances and preserve launch timing.",
        decision_required="Approve the cloud spend path.",
        blocks=[
            PresentationBlock(kind="headline", title="Context", bullets=["Cloud costs are 29% over plan."]),
            PresentationBlock(kind="actions", title="Recommended Actions", bullets=["Approve reserved instances."]),
        ],
    )

    memo_payload = build_memo_payload({"presentation_spec": presentation_spec.model_dump(mode="json"), "template_id": "board_memo_v1", "theme_id": "default"})
    deck_payload = build_deck_payload({"presentation_spec": presentation_spec.model_dump(mode="json"), "template_id": "board_deck_v1", "theme_id": "default"})

    assert memo_payload["title"] == "Cloud spend decision before April 15"
    assert memo_payload["metadata"]["presentation_version"] == "presentation_spec_v1"
    assert deck_payload["title"] == "Cloud spend decision before April 15"
    assert any(slide["title"] == "Decision Required" for slide in deck_payload["slides"])

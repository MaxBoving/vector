from src.finance import get_finance_template_definition
from src.presentation import MemoTemplate, get_artifact_template, get_workbook_template, list_artifact_templates


def test_template_registry_exposes_expected_structural_templates() -> None:
    template_ids = {template.template_id for template in list_artifact_templates()}
    assert {"board_memo_v1", "finance_workbook_v1", "meeting_prep_deck_v1", "board_deck_v1"} <= template_ids


def test_finance_workbook_template_has_expected_tab_order() -> None:
    template = get_workbook_template("finance_workbook_v1")
    assert template.tab_order == ["Summary", "Model", "Variance", "Forecast", "Charts"]


def test_finance_templates_reference_valid_workbook_template_ids() -> None:
    for template_id in [
        "cost_review",
        "runway_review",
        "project_spend_review",
        "budget_variance_review",
        "board_financial_update",
    ]:
        definition = get_finance_template_definition(template_id)
        workbook_template = get_workbook_template(definition.workbook_template_id)
        assert workbook_template.template_id == "finance_workbook_v1"
        assert definition.workbook_tabs == workbook_template.tab_order


def test_board_memo_template_section_order_is_formalized() -> None:
    template = get_artifact_template("board_memo_v1")
    assert isinstance(template, MemoTemplate)
    assert template.artifact_family == "memo"
    assert template.section_order[:3] == ["Executive Summary", "Key Findings", "Business Implications"]


def test_board_deck_template_has_formal_board_sequence() -> None:
    template = get_artifact_template("board_deck_v1")
    assert template.artifact_family == "deck"
    assert template.slide_sequence[:4] == ["Title", "Executive Summary", "Business Context", "Key Metrics"]

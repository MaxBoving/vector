from src.finance import get_finance_template_definition
from src.presentation import DEFAULT_THEME_ID, get_brand_theme, list_brand_themes, resolve_brand_theme


def test_theme_registry_exposes_expected_built_in_themes() -> None:
    themes = {theme.theme_id for theme in list_brand_themes()}
    assert {"executive_classic", "operator_modern", "board_formal"} <= themes


def test_resolve_brand_theme_defaults_to_registry_default() -> None:
    theme = resolve_brand_theme()
    assert theme.theme_id == DEFAULT_THEME_ID


def test_finance_templates_reference_valid_theme_ids() -> None:
    template_ids = [
        "cost_review",
        "runway_review",
        "project_spend_review",
        "budget_variance_review",
        "board_financial_update",
    ]

    for template_id in template_ids:
        definition = get_finance_template_definition(template_id)
        theme = get_brand_theme(definition.default_theme_id)
        assert theme.theme_id == definition.default_theme_id


def test_board_financial_update_uses_formal_board_theme() -> None:
    definition = get_finance_template_definition("board_financial_update")
    assert definition.default_theme_id == "board_formal"

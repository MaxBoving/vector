from __future__ import annotations

from src.presentation.template_models import DeckTemplate, MemoTemplate, WorkbookTabTemplate, WorkbookTemplate


BOARD_MEMO_V1 = MemoTemplate(
    template_id="board_memo_v1",
    label="Board Memo v1",
    summary="Formal executive memo structure for board-facing and investor-style written updates.",
    section_order=[
        "Executive Summary",
        "Key Findings",
        "Business Implications",
        "Recommended Actions",
        "Assumptions",
        "Open Questions",
    ],
    optional_sections=["Risks", "Appendix"],
    appendix_enabled=True,
)

FINANCE_WORKBOOK_V1 = WorkbookTemplate(
    template_id="finance_workbook_v1",
    label="Finance Workbook v1",
    summary="Standard executive workbook layout for financial model, variance, forecast, and chart review.",
    tab_order=["Summary", "Model", "Variance", "Forecast", "Charts"],
    optional_tabs=["Appendix", "Sensitivity"],
    chart_priority=["variance", "forecast", "comparison"],
    tab_specs=[
        WorkbookTabTemplate(name="Summary", kind="summary", allow_metrics=True, allow_tables=True, allow_charts=False, allow_pivots=False),
        WorkbookTabTemplate(name="Model", kind="model", allow_metrics=False, allow_tables=True, allow_charts=True, allow_pivots=True),
        WorkbookTabTemplate(name="Variance", kind="variance", allow_metrics=False, allow_tables=True, allow_charts=False, allow_pivots=False),
        WorkbookTabTemplate(name="Forecast", kind="forecast", allow_metrics=False, allow_tables=True, allow_charts=False, allow_pivots=False),
        WorkbookTabTemplate(name="Charts", kind="charts", allow_metrics=False, allow_tables=True, allow_charts=True, allow_pivots=False),
    ],
)

MEETING_PREP_DECK_V1 = DeckTemplate(
    template_id="meeting_prep_deck_v1",
    label="Meeting Prep Deck v1",
    summary="Compact prep deck sequence for executive meetings and board committee sessions.",
    slide_sequence=[
        "Title",
        "Context",
        "Key Questions",
        "Decision Points",
        "Risks",
        "Recommended Actions",
    ],
    optional_slides=["Attendee Briefs", "Appendix"],
    appendix_enabled=True,
)

BOARD_DECK_V1 = DeckTemplate(
    template_id="board_deck_v1",
    label="Board Deck v1",
    summary="Formal board-facing presentation sequence for board meetings, investor reviews, and committee sessions.",
    slide_sequence=[
        "Title",
        "Executive Summary",
        "Business Context",
        "Key Metrics",
        "Decision Points",
        "Risks",
        "Recommended Actions",
        "Appendix",
    ],
    optional_slides=["Financial Detail", "Operating Detail"],
    appendix_enabled=True,
)

TEMPLATE_REGISTRY: dict[str, MemoTemplate | DeckTemplate | WorkbookTemplate] = {
    BOARD_MEMO_V1.template_id: BOARD_MEMO_V1,
    FINANCE_WORKBOOK_V1.template_id: FINANCE_WORKBOOK_V1,
    BOARD_DECK_V1.template_id: BOARD_DECK_V1,
    MEETING_PREP_DECK_V1.template_id: MEETING_PREP_DECK_V1,
}

DEFAULT_WORKBOOK_TEMPLATE_ID = FINANCE_WORKBOOK_V1.template_id


def get_artifact_template(template_id: str) -> MemoTemplate | DeckTemplate | WorkbookTemplate:
    return TEMPLATE_REGISTRY[template_id]


def get_workbook_template(template_id: str) -> WorkbookTemplate:
    template = TEMPLATE_REGISTRY[template_id]
    if not isinstance(template, WorkbookTemplate):
        raise KeyError(f"Template `{template_id}` is not a workbook template.")
    return template


def list_artifact_templates() -> list[MemoTemplate | DeckTemplate | WorkbookTemplate]:
    return list(TEMPLATE_REGISTRY.values())

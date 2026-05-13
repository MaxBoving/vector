from __future__ import annotations

from src.presentation.theme_models import (
    BrandChartTokens,
    BrandColorPalette,
    BrandTableTokens,
    BrandTheme,
    BrandTypography,
)


EXECUTIVE_CLASSIC = BrandTheme(
    theme_id="executive_classic",
    label="Executive Classic",
    summary="Formal board-ready styling with restrained contrast and conservative accents.",
    typography=BrandTypography(
        heading_family="Georgia",
        body_family="Aptos",
        mono_family="Courier New",
    ),
    colors=BrandColorPalette(
        primary="#16324F",
        secondary="#4F6D7A",
        accent="#B88A44",
        background="#F7F5F0",
        surface="#FFFFFF",
        text="#1F2933",
        muted_text="#52606D",
        border="#D9E2EC",
    ),
    charts=BrandChartTokens(
        series=["#16324F", "#4F6D7A", "#B88A44", "#7B8794"],
        axis="#52606D",
        grid="#D9E2EC",
        highlight="#B88A44",
    ),
    tables=BrandTableTokens(
        header_background="#16324F",
        header_text="#FFFFFF",
        row_even_background="#FFFFFF",
        row_odd_background="#F7F5F0",
        border="#D9E2EC",
        emphasis_background="#FFF3D6",
    ),
    logo_lockup="crest",
    cover_style="board",
)

OPERATOR_MODERN = BrandTheme(
    theme_id="operator_modern",
    label="Operator Modern",
    summary="Clean operator-focused styling with sharper contrast and lighter surfaces.",
    typography=BrandTypography(
        heading_family="Aptos Display",
        body_family="Aptos",
        mono_family="Courier New",
    ),
    colors=BrandColorPalette(
        primary="#0F172A",
        secondary="#2563EB",
        accent="#14B8A6",
        background="#F8FAFC",
        surface="#FFFFFF",
        text="#0F172A",
        muted_text="#475569",
        border="#CBD5E1",
    ),
    charts=BrandChartTokens(
        series=["#2563EB", "#14B8A6", "#F59E0B", "#6366F1"],
        axis="#475569",
        grid="#E2E8F0",
        highlight="#14B8A6",
    ),
    tables=BrandTableTokens(
        header_background="#0F172A",
        header_text="#FFFFFF",
        row_even_background="#FFFFFF",
        row_odd_background="#F8FAFC",
        border="#CBD5E1",
        emphasis_background="#DBEAFE",
    ),
    logo_lockup="wordmark",
    cover_style="operator",
)

BOARD_FORMAL = BrandTheme(
    theme_id="board_formal",
    label="Board Formal",
    summary="Institutional styling for governance-heavy memos and formal review decks.",
    typography=BrandTypography(
        heading_family="Cambria",
        body_family="Calibri",
        mono_family="Courier New",
    ),
    colors=BrandColorPalette(
        primary="#1E3A5F",
        secondary="#6B7280",
        accent="#8B5E34",
        background="#FAFAF8",
        surface="#FFFFFF",
        text="#111827",
        muted_text="#4B5563",
        border="#D1D5DB",
    ),
    charts=BrandChartTokens(
        series=["#1E3A5F", "#8B5E34", "#6B7280", "#9CA3AF"],
        axis="#4B5563",
        grid="#E5E7EB",
        highlight="#8B5E34",
    ),
    tables=BrandTableTokens(
        header_background="#1E3A5F",
        header_text="#FFFFFF",
        row_even_background="#FFFFFF",
        row_odd_background="#F9FAFB",
        border="#D1D5DB",
        emphasis_background="#F3E8D8",
    ),
    logo_lockup="seal",
    cover_style="formal",
)

THEME_REGISTRY: dict[str, BrandTheme] = {
    EXECUTIVE_CLASSIC.theme_id: EXECUTIVE_CLASSIC,
    OPERATOR_MODERN.theme_id: OPERATOR_MODERN,
    BOARD_FORMAL.theme_id: BOARD_FORMAL,
}

DEFAULT_THEME_ID = EXECUTIVE_CLASSIC.theme_id


def get_brand_theme(theme_id: str) -> BrandTheme:
    return THEME_REGISTRY[theme_id]


def list_brand_themes() -> list[BrandTheme]:
    return list(THEME_REGISTRY.values())


def resolve_brand_theme(theme_id: str | None = None) -> BrandTheme:
    return THEME_REGISTRY[theme_id or DEFAULT_THEME_ID]


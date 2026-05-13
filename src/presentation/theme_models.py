from __future__ import annotations

from pydantic import BaseModel, Field


class BrandTypography(BaseModel):
    heading_family: str
    body_family: str
    mono_family: str = "Courier New"
    title_weight: int = 700
    heading_weight: int = 600
    body_weight: int = 400


class BrandColorPalette(BaseModel):
    primary: str
    secondary: str
    accent: str
    background: str
    surface: str
    text: str
    muted_text: str
    border: str
    success: str = "#2E7D32"
    warning: str = "#C67C00"
    danger: str = "#B3261E"


class BrandChartTokens(BaseModel):
    series: list[str] = Field(default_factory=list)
    axis: str
    grid: str
    highlight: str


class BrandTableTokens(BaseModel):
    header_background: str
    header_text: str
    row_even_background: str
    row_odd_background: str
    border: str
    emphasis_background: str


class BrandTheme(BaseModel):
    theme_id: str
    label: str
    summary: str
    typography: BrandTypography
    colors: BrandColorPalette
    charts: BrandChartTokens
    tables: BrandTableTokens
    logo_lockup: str = "wordmark"
    cover_style: str = "executive"


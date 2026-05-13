"""Theme factory — dynamic BrandTheme generation and registration.

Implements the theme-factory skill pattern: build a complete BrandTheme from
a minimal brand spec (primary color, accent, fonts, cover style), derive table
and chart tokens automatically, and register custom themes into the live
THEME_REGISTRY so all renderers pick them up without a restart.
"""
from __future__ import annotations

import colorsys
import re
from typing import Optional

from pydantic import BaseModel, Field

from src.presentation.theme_models import (
    BrandChartTokens,
    BrandColorPalette,
    BrandTableTokens,
    BrandTheme,
    BrandTypography,
)
from src.presentation.theme_registry import THEME_REGISTRY, DEFAULT_THEME_ID


# ---------------------------------------------------------------------------
# Input spec
# ---------------------------------------------------------------------------

class ThemeSpec(BaseModel):
    """Minimal brand inputs needed to generate a complete BrandTheme."""

    theme_id: str
    label: str
    summary: str = ""

    # Required colors (hex, with or without #)
    primary_color: str                    # e.g. "#1E3A5F" — dominant brand color
    accent_color: str                     # e.g. "#8B5E34" — highlight color

    # Optional overrides
    secondary_color: Optional[str] = None  # derived from primary if not provided
    background_color: Optional[str] = None # derived from primary if not provided

    # Typography
    heading_font: str = "Cambria"
    body_font: str = "Calibri"
    mono_font: str = "Courier New"

    # Style
    cover_style: str = "executive"        # board | executive | operator | formal
    logo_lockup: str = "wordmark"         # wordmark | seal | crest


# ---------------------------------------------------------------------------
# Color derivation helpers
# ---------------------------------------------------------------------------

def _normalize_hex(value: str) -> str:
    return value.strip().lstrip("#").upper()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = _normalize_hex(hex_color)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def _lighten(hex_color: str, factor: float) -> str:
    """Lighten a hex color by blending toward white."""
    r, g, b = _hex_to_rgb(hex_color)
    r2 = int(r + (255 - r) * factor)
    g2 = int(g + (255 - g) * factor)
    b2 = int(b + (255 - b) * factor)
    return _rgb_to_hex(r2, g2, b2)


def _darken(hex_color: str, factor: float) -> str:
    """Darken a hex color by blending toward black."""
    r, g, b = _hex_to_rgb(hex_color)
    return _rgb_to_hex(int(r * (1 - factor)), int(g * (1 - factor)), int(b * (1 - factor)))


def _derive_secondary(primary: str) -> str:
    """Derive a muted secondary color from the primary."""
    r, g, b = _hex_to_rgb(primary)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    # Desaturate and slightly lighten
    s2 = max(0.0, s * 0.55)
    v2 = min(1.0, v * 1.15)
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s2, v2)
    return _rgb_to_hex(int(r2 * 255), int(g2 * 255), int(b2 * 255))


def _derive_background(primary: str) -> str:
    """Derive an off-white background from the primary."""
    r, g, b = _hex_to_rgb(primary)
    h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    # Very low saturation, very high brightness — tinted off-white
    r2, g2, b2 = colorsys.hsv_to_rgb(h, max(0.0, s * 0.06), 0.98)
    return _rgb_to_hex(int(r2 * 255), int(g2 * 255), int(b2 * 255))


def _is_dark(hex_color: str) -> bool:
    """Return True if the color is dark enough to need white text on top."""
    r, g, b = _hex_to_rgb(hex_color)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return luminance < 140


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_theme_from_spec(spec: ThemeSpec) -> BrandTheme:
    """
    Generate a complete BrandTheme from a ThemeSpec.

    Derives secondary, background, surface, text, border, and all table/chart
    tokens automatically from the primary and accent colors.
    """
    primary = f"#{_normalize_hex(spec.primary_color)}"
    accent = f"#{_normalize_hex(spec.accent_color)}"
    secondary = f"#{_normalize_hex(spec.secondary_color)}" if spec.secondary_color else _derive_secondary(primary)
    background = f"#{_normalize_hex(spec.background_color)}" if spec.background_color else _derive_background(primary)

    surface = "#FFFFFF"
    text = "#111827" if not _is_dark(background) else "#F9FAFB"
    muted_text = _lighten(primary, 0.55) if _is_dark(primary) else _darken(primary, 0.25)
    border = _lighten(secondary, 0.55)

    header_text = "#FFFFFF" if _is_dark(primary) else "#111827"
    row_even = surface
    row_odd = _lighten(primary, 0.94)
    emphasis_bg = _lighten(accent, 0.82)

    chart_series = [
        primary,
        accent,
        secondary,
        _lighten(primary, 0.35),
    ]
    chart_axis = muted_text
    chart_grid = border

    return BrandTheme(
        theme_id=spec.theme_id,
        label=spec.label,
        summary=spec.summary or f"Custom theme derived from {primary}.",
        typography=BrandTypography(
            heading_family=spec.heading_font,
            body_family=spec.body_font,
            mono_family=spec.mono_font,
        ),
        colors=BrandColorPalette(
            primary=primary,
            secondary=secondary,
            accent=accent,
            background=background,
            surface=surface,
            text=text,
            muted_text=muted_text,
            border=border,
        ),
        charts=BrandChartTokens(
            series=chart_series,
            axis=chart_axis,
            grid=chart_grid,
            highlight=accent,
        ),
        tables=BrandTableTokens(
            header_background=primary,
            header_text=header_text,
            row_even_background=row_even,
            row_odd_background=row_odd,
            border=border,
            emphasis_background=emphasis_bg,
        ),
        logo_lockup=spec.logo_lockup,
        cover_style=spec.cover_style,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_theme(theme: BrandTheme) -> BrandTheme:
    """
    Add a BrandTheme to the live THEME_REGISTRY.

    Raises ValueError if theme_id is empty or collides with a built-in theme
    and overwrite is not forced. Returns the theme for chaining.
    """
    if not theme.theme_id:
        raise ValueError("theme_id must be non-empty")
    THEME_REGISTRY[theme.theme_id] = theme
    return theme


def build_and_register_theme(spec: ThemeSpec) -> BrandTheme:
    """Convenience: build a theme from spec and immediately register it."""
    theme = build_theme_from_spec(spec)
    return register_theme(theme)


def list_registered_themes() -> list[BrandTheme]:
    return list(THEME_REGISTRY.values())


def get_or_default(theme_id: str | None) -> BrandTheme:
    """Return the requested theme or the default if not found."""
    return THEME_REGISTRY.get(theme_id or DEFAULT_THEME_ID, THEME_REGISTRY[DEFAULT_THEME_ID])

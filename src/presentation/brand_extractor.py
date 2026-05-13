"""Brand guidelines extraction — structured brand profile only.

Parses explicit brand tokens out of a CompanyIdentityProfile.profile_data
blob and auto-derives a matching BrandTheme via the theme factory. Styling is
driven only by structured brand fields; free-text reconstruction is not used.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.presentation.theme_factory import ThemeSpec, build_and_register_theme, get_or_default
from src.presentation.theme_models import BrandTheme


# ---------------------------------------------------------------------------
# Extracted guidelines model
# ---------------------------------------------------------------------------

class BrandGuidelines(BaseModel):
    """Structured brand tokens extracted from an identity profile."""

    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    background_color: Optional[str] = None
    heading_font: Optional[str] = None
    body_font: Optional[str] = None
    cover_style: Optional[str] = None
    logo_lockup: Optional[str] = None
    tone: Optional[str] = None
    preferred_formats: List[str] = Field(default_factory=list)
    section_patterns: List[str] = Field(default_factory=list)

    @property
    def has_colors(self) -> bool:
        return bool(self.primary_color or self.accent_color)

    @property
    def has_typography(self) -> bool:
        return bool(self.heading_font or self.body_font)


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

def extract_brand_guidelines(profile_data: Dict[str, Any]) -> BrandGuidelines:
    """
    Extract structured BrandGuidelines from a CompanyIdentityProfile.profile_data dict.

    Handles only explicit structured keys (for example
    profile_data["brand_colors"]["primary"]).
    """
    # Try structured paths first
    colors_blob = profile_data.get("brand_colors") or profile_data.get("colors") or {}
    fonts_blob = profile_data.get("typography") or profile_data.get("fonts") or {}
    style_blob = profile_data.get("style") or profile_data.get("cover") or {}

    def _coerce(d: Any, *keys: str) -> Optional[str]:
        if not isinstance(d, dict):
            return None
        for k in keys:
            v = d.get(k)
            if v and isinstance(v, str):
                return v.strip()
        return None

    primary = _coerce(colors_blob, "primary", "primary_color", "brand_color")
    secondary = _coerce(colors_blob, "secondary", "secondary_color")
    accent = _coerce(colors_blob, "accent", "accent_color", "highlight")
    background = _coerce(colors_blob, "background", "background_color", "bg")
    heading_font = _coerce(fonts_blob, "heading", "heading_family", "title_font")
    body_font = _coerce(fonts_blob, "body", "body_family", "body_font")
    cover_style = _coerce(style_blob, "cover_style", "style", "type")
    logo_lockup = _coerce(profile_data, "logo_lockup", "logo_type", "logo_style")
    tone = _coerce(profile_data, "tone", "voice", "writing_style")
    preferred_formats = profile_data.get("preferred_formats") or []
    section_patterns = profile_data.get("section_patterns") or []

    return BrandGuidelines(
        primary_color=primary,
        secondary_color=secondary,
        accent_color=accent,
        background_color=background,
        heading_font=heading_font,
        body_font=body_font,
        cover_style=cover_style or "executive",
        logo_lockup=logo_lockup or "wordmark",
        tone=tone,
        preferred_formats=preferred_formats if isinstance(preferred_formats, list) else [],
        section_patterns=section_patterns if isinstance(section_patterns, list) else [],
    )


# ---------------------------------------------------------------------------
# Theme derivation from guidelines
# ---------------------------------------------------------------------------

def derive_theme_from_guidelines(
    guidelines: BrandGuidelines,
    *,
    company_name: str,
    fallback_theme_id: str = "executive_classic",
) -> BrandTheme:
    """
    Build and register a custom BrandTheme from extracted brand guidelines.

    If the guidelines lack colors, returns the fallback theme unchanged.
    The derived theme_id is `{company_slug}_brand`.
    """
    if not guidelines.has_colors:
        return get_or_default(fallback_theme_id)

    slug = re.sub(r'[^a-z0-9]+', '_', company_name.lower()).strip('_')
    theme_id = f"{slug}_brand"

    spec = ThemeSpec(
        theme_id=theme_id,
        label=f"{company_name} Brand",
        summary=f"Auto-derived brand theme for {company_name}.",
        primary_color=guidelines.primary_color or "#1E3A5F",
        accent_color=guidelines.accent_color or "#8B5E34",
        secondary_color=guidelines.secondary_color,
        background_color=guidelines.background_color,
        heading_font=guidelines.heading_font or "Cambria",
        body_font=guidelines.body_font or "Calibri",
        cover_style=guidelines.cover_style or "executive",
        logo_lockup=guidelines.logo_lockup or "wordmark",
    )

    return build_and_register_theme(spec)


def extract_and_apply_brand_theme(
    identity_profile: Dict[str, Any],
    *,
    company_name: str,
    fallback_theme_id: str = "executive_classic",
) -> BrandTheme:
    """
    Full pipeline: extract guidelines from profile_data → derive and register theme.

    Args:
        identity_profile: dict from CompanyIdentityProfile or its profile_data sub-dict
        company_name: used for the theme_id slug and label
        fallback_theme_id: theme to return if no brand colors are found
    """
    # Accept either the full model dict or just profile_data
    profile_data = identity_profile.get("profile_data", identity_profile)
    guidelines = extract_brand_guidelines(profile_data)
    return derive_theme_from_guidelines(
        guidelines,
        company_name=company_name,
        fallback_theme_id=fallback_theme_id,
    )

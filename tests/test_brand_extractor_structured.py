from __future__ import annotations

from src.presentation.brand_extractor import extract_brand_guidelines


def test_extract_brand_guidelines_ignores_free_text_when_structured_fields_missing() -> None:
    guidelines = extract_brand_guidelines(
        {
            "raw_text": "Primary color #ff0000, heading font Calibri, formal executive wordmark.",
            "summary": "Brand guidance in prose only.",
        }
    )

    assert guidelines.primary_color is None
    assert guidelines.secondary_color is None
    assert guidelines.accent_color is None
    assert guidelines.background_color is None
    assert guidelines.heading_font is None
    assert guidelines.body_font is None
    assert guidelines.cover_style == "executive"
    assert guidelines.logo_lockup == "wordmark"


def test_extract_brand_guidelines_uses_structured_fields() -> None:
    guidelines = extract_brand_guidelines(
        {
            "brand_colors": {"primary": "#112233", "accent_color": "#445566"},
            "typography": {"heading_family": "Cambria", "body_family": "Aptos"},
            "cover": {"style": "formal"},
            "logo_lockup": "crest",
            "tone": "direct",
        }
    )

    assert guidelines.primary_color == "#112233"
    assert guidelines.accent_color == "#445566"
    assert guidelines.heading_font == "Cambria"
    assert guidelines.body_font == "Aptos"
    assert guidelines.cover_style == "formal"
    assert guidelines.logo_lockup == "crest"
    assert guidelines.tone == "direct"

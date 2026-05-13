"""Canvas one-pager renderer — canvas-design skill pattern.

Generates a self-contained, styled HTML executive one-pager from a CanvasSpec.
The output is a single HTML file with inline CSS (no external dependencies)
that renders correctly in any browser and can be saved or embedded as an artifact.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field

from src.presentation.theme_factory import get_or_default
from src.presentation.theme_models import BrandTheme


# ---------------------------------------------------------------------------
# Spec model
# ---------------------------------------------------------------------------

class CanvasSectionSpec(BaseModel):
    label: str
    bullets: List[str] = Field(default_factory=list)
    content: Optional[str] = None
    highlight: bool = False   # renders with accent-color left border


class CanvasHeroMetric(BaseModel):
    label: str
    value: str
    delta: Optional[str] = None   # e.g. "+7% vs plan" — shown in muted text


class CanvasSpec(BaseModel):
    title: str
    subtitle: Optional[str] = None
    hero_metric: Optional[CanvasHeroMetric] = None
    sections: List[CanvasSectionSpec] = Field(default_factory=list)
    summary: Optional[str] = None
    source_credit: Optional[str] = None
    theme_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def _inline_css(theme: BrandTheme) -> str:
    p = theme.colors.primary.lstrip("#")
    a = theme.colors.accent.lstrip("#")
    s = theme.colors.secondary.lstrip("#")
    bg = theme.colors.background.lstrip("#")
    sur = theme.colors.surface.lstrip("#")
    txt = theme.colors.text.lstrip("#")
    muted = theme.colors.muted_text.lstrip("#")
    bdr = theme.colors.border.lstrip("#")
    hf = theme.typography.heading_family
    bf = theme.typography.body_family

    return f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: '{bf}', Calibri, Arial, sans-serif;
  background: #{bg};
  color: #{txt};
  padding: 48px 64px;
  max-width: 920px;
  margin: 0 auto;
  line-height: 1.55;
}}
.canvas-header {{
  border-bottom: 3px solid #{p};
  padding-bottom: 20px;
  margin-bottom: 28px;
}}
.canvas-title {{
  font-family: '{hf}', Cambria, Georgia, serif;
  font-size: 28px;
  font-weight: 700;
  color: #{p};
  letter-spacing: -0.3px;
}}
.canvas-subtitle {{
  font-size: 14px;
  color: #{muted};
  margin-top: 4px;
}}
.canvas-hero {{
  background: #{sur};
  border: 1px solid #{bdr};
  border-left: 5px solid #{a};
  border-radius: 6px;
  padding: 20px 28px;
  margin-bottom: 28px;
  display: flex;
  align-items: baseline;
  gap: 16px;
}}
.canvas-hero-label {{
  font-size: 13px;
  font-weight: 600;
  color: #{muted};
  text-transform: uppercase;
  letter-spacing: 0.5px;
  flex-shrink: 0;
}}
.canvas-hero-value {{
  font-family: '{hf}', Cambria, Georgia, serif;
  font-size: 36px;
  font-weight: 700;
  color: #{p};
}}
.canvas-hero-delta {{
  font-size: 14px;
  color: #{muted};
  margin-left: 4px;
}}
.canvas-summary {{
  font-size: 15px;
  color: #{txt};
  margin-bottom: 28px;
  padding: 16px 20px;
  background: #{sur};
  border-radius: 4px;
  border: 1px solid #{bdr};
}}
.canvas-sections {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
  margin-bottom: 28px;
}}
.canvas-section {{
  background: #{sur};
  border: 1px solid #{bdr};
  border-radius: 6px;
  padding: 18px 20px;
}}
.canvas-section.highlight {{
  border-left: 4px solid #{a};
}}
.canvas-section-label {{
  font-family: '{hf}', Cambria, Georgia, serif;
  font-size: 14px;
  font-weight: 700;
  color: #{p};
  text-transform: uppercase;
  letter-spacing: 0.4px;
  margin-bottom: 10px;
}}
.canvas-section-content {{
  font-size: 13.5px;
  color: #{txt};
  line-height: 1.5;
}}
.canvas-bullets {{
  list-style: none;
  padding: 0;
}}
.canvas-bullets li {{
  padding: 3px 0 3px 14px;
  position: relative;
  font-size: 13.5px;
}}
.canvas-bullets li::before {{
  content: '—';
  position: absolute;
  left: 0;
  color: #{a};
  font-weight: 700;
}}
.canvas-footer {{
  border-top: 1px solid #{bdr};
  padding-top: 12px;
  font-size: 11px;
  color: #{muted};
  display: flex;
  justify-content: space-between;
}}
@media (max-width: 640px) {{
  .canvas-sections {{ grid-template-columns: 1fr; }}
  body {{ padding: 24px 20px; }}
}}
""".strip()


def _section_html(section: CanvasSectionSpec) -> str:
    highlight_class = " highlight" if section.highlight else ""
    label_html = f'<div class="canvas-section-label">{escape(section.label)}</div>'
    body_parts: List[str] = []
    if section.content:
        body_parts.append(f'<div class="canvas-section-content">{escape(section.content)}</div>')
    if section.bullets:
        items = "".join(f"<li>{escape(b)}</li>" for b in section.bullets)
        body_parts.append(f'<ul class="canvas-bullets">{items}</ul>')
    body_html = "\n".join(body_parts)
    return f'<div class="canvas-section{highlight_class}">{label_html}{body_html}</div>'


def render_canvas_html(spec: CanvasSpec) -> str:
    """
    Render a CanvasSpec to a self-contained HTML string.

    The returned HTML has no external dependencies — safe to write to disk,
    embed in a response, or serve via FileResponse.
    """
    theme = get_or_default(spec.theme_id)
    css = _inline_css(theme)

    # Header
    subtitle_html = (
        f'<div class="canvas-subtitle">{escape(spec.subtitle)}</div>'
        if spec.subtitle else ""
    )
    header_html = (
        f'<div class="canvas-header">'
        f'<div class="canvas-title">{escape(spec.title)}</div>'
        f'{subtitle_html}'
        f'</div>'
    )

    # Hero metric
    hero_html = ""
    if spec.hero_metric:
        delta_html = (
            f'<span class="canvas-hero-delta">{escape(spec.hero_metric.delta)}</span>'
            if spec.hero_metric.delta else ""
        )
        hero_html = (
            f'<div class="canvas-hero">'
            f'<span class="canvas-hero-label">{escape(spec.hero_metric.label)}</span>'
            f'<span class="canvas-hero-value">{escape(spec.hero_metric.value)}</span>'
            f'{delta_html}'
            f'</div>'
        )

    # Summary
    summary_html = (
        f'<div class="canvas-summary">{escape(spec.summary)}</div>'
        if spec.summary else ""
    )

    # Sections
    sections_html = (
        f'<div class="canvas-sections">{"".join(_section_html(s) for s in spec.sections)}</div>'
        if spec.sections else ""
    )

    # Footer
    footer_html = ""
    if spec.source_credit:
        footer_html = (
            f'<div class="canvas-footer">'
            f'<span>Source: {escape(spec.source_credit)}</span>'
            f'<span>Generated by agenticMIND</span>'
            f'</div>'
        )

    body = f"{header_html}\n{hero_html}\n{summary_html}\n{sections_html}\n{footer_html}"

    return (
        f"<!DOCTYPE html>\n"
        f'<html lang="en">\n'
        f"<head>\n"
        f'<meta charset="UTF-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{escape(spec.title)}</title>\n"
        f"<style>\n{css}\n</style>\n"
        f"</head>\n"
        f"<body>\n{body}\n</body>\n"
        f"</html>"
    )


def render_canvas_file(*, path: Path, spec: CanvasSpec) -> Dict[str, Any]:
    """
    Write the canvas HTML to disk and return renderer metadata.
    Mirrors the return contract of render_docx_memo / render_pptx_deck.
    """
    html = render_canvas_html(spec)
    path.write_text(html, encoding="utf-8")
    section_count = len(spec.sections)
    return {
        "preview_content": html,
        "preview_format": "html",
        "preview_metadata": {
            **spec.metadata,
            "theme_id": spec.theme_id,
            "section_count": section_count,
            "has_hero_metric": spec.hero_metric is not None,
            "file_size_bytes": path.stat().st_size,
        },
    }

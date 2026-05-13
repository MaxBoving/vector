from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DeckSlideSpec(BaseModel):
    title: str
    bullets: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    kind: str = "content"


class DeckSpec(BaseModel):
    title: str
    subtitle: str | None = None
    slide_order: list[str] = Field(default_factory=list)
    slides: list[DeckSlideSpec] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def deck_spec_to_preview_markdown(spec: DeckSpec) -> str:
    lines = [spec.title]
    if spec.subtitle:
        lines.extend(["", spec.subtitle])
    slide_map = {slide.title: slide for slide in spec.slides}
    ordered_titles = list(spec.slide_order) or [slide.title for slide in spec.slides]
    for title in ordered_titles:
        slide = slide_map.get(title)
        if not slide:
            continue
        lines.extend(["", f"Slide: {slide.title}"])
        lines.extend([f"- {item}" for item in slide.bullets])
    return "\n".join(lines).strip()

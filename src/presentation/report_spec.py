from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoSectionSpec(BaseModel):
    label: str
    items: list[str] = Field(default_factory=list)


class MemoSpec(BaseModel):
    title: str
    summary: str
    section_order: list[str] = Field(default_factory=list)
    sections: list[MemoSectionSpec] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def memo_spec_to_preview_markdown(spec: MemoSpec) -> str:
    lines = [spec.title, "", f"Executive Summary: {spec.summary}".strip()]
    section_map = {section.label: section for section in spec.sections}
    ordered_labels = list(spec.section_order) or [section.label for section in spec.sections]

    for label in ordered_labels:
        section = section_map.get(label)
        if not section:
            continue
        lines.extend(["", label])
        lines.extend([f"- {item}" for item in section.items])

    if spec.assumptions:
        lines.extend(["", "Assumptions"])
        lines.extend([f"- {item}" for item in spec.assumptions])

    if spec.open_questions:
        lines.extend(["", "Open Questions"])
        lines.extend([f"- {item}" for item in spec.open_questions])

    return "\n".join(lines).strip()

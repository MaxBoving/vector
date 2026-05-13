from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ArtifactFamily = Literal["memo", "deck", "workbook"]
WorkbookTabKind = Literal["summary", "model", "variance", "forecast", "charts"]


class ArtifactTemplate(BaseModel):
    template_id: str
    label: str
    artifact_family: ArtifactFamily
    summary: str
    default_variant: str = "v1"


class MemoTemplate(ArtifactTemplate):
    artifact_family: Literal["memo"] = "memo"
    section_order: list[str] = Field(default_factory=list)
    optional_sections: list[str] = Field(default_factory=list)
    appendix_enabled: bool = True


class DeckTemplate(ArtifactTemplate):
    artifact_family: Literal["deck"] = "deck"
    slide_sequence: list[str] = Field(default_factory=list)
    optional_slides: list[str] = Field(default_factory=list)
    appendix_enabled: bool = True


class WorkbookTemplate(ArtifactTemplate):
    artifact_family: Literal["workbook"] = "workbook"
    tab_order: list[str] = Field(default_factory=list)
    optional_tabs: list[str] = Field(default_factory=list)
    chart_priority: list[str] = Field(default_factory=list)
    tab_specs: list["WorkbookTabTemplate"] = Field(default_factory=list)


class WorkbookTabTemplate(BaseModel):
    name: str
    kind: WorkbookTabKind
    allow_metrics: bool = False
    allow_tables: bool = True
    allow_charts: bool = False
    allow_pivots: bool = False

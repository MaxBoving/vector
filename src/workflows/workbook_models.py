from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


WorkbookTabKind = Literal["summary", "model", "variance", "forecast", "charts"]


class WorkbookMetric(BaseModel):
    label: str
    value: str


class WorkbookFinancialRow(BaseModel):
    period: str
    metric: str
    budget: float
    actual: float
    variance: float
    forecast: float
    source_type: str = "derived"
    source_ref: str = ""
    source_excerpt: Optional[str] = None


class WorkbookTable(BaseModel):
    title: str
    columns: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)
    row_provenance: List[Dict[str, Any]] = Field(default_factory=list)


class WorkbookChartSpec(BaseModel):
    title: str
    chart_type: str
    x_axis: str
    y_axis: str
    series_label: str
    source_sheet: Optional[str] = None
    source_table: Optional[str] = None


class WorkbookPivotRow(BaseModel):
    label: str
    value: float


class WorkbookPivotSnapshot(BaseModel):
    title: str
    dimension: str
    measure: str
    rows: List[WorkbookPivotRow] = Field(default_factory=list)


class WorkbookSheetSpec(BaseModel):
    name: str
    kind: WorkbookTabKind = "summary"
    metrics: List[WorkbookMetric] = Field(default_factory=list)
    financial_rows: List[WorkbookFinancialRow] = Field(default_factory=list)
    tables: List[WorkbookTable] = Field(default_factory=list)
    chart_specs: List[WorkbookChartSpec] = Field(default_factory=list)
    pivot_snapshots: List[WorkbookPivotSnapshot] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkbookSpec(BaseModel):
    workbook_title: str
    sheets: List[WorkbookSheetSpec] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


def workbook_spec_to_view_model(spec: WorkbookSpec, artifact_id: str) -> Dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "title": spec.workbook_title,
        "tabs": [sheet_to_view_model(sheet) for sheet in spec.sheets],
        "metadata": spec.metadata,
    }


def sheet_to_view_model(sheet: WorkbookSheetSpec) -> Dict[str, Any]:
    return {
        "name": sheet.name,
        "kind": sheet.kind,
        "metrics": [metric.model_dump() for metric in sheet.metrics],
        "tables": [table.model_dump() for table in _resolved_tables(sheet)],
        "charts": [chart.model_dump() for chart in sheet.chart_specs],
        "pivot_snapshots": [pivot.model_dump() for pivot in sheet.pivot_snapshots],
        "metadata": sheet.metadata,
    }


def _resolved_tables(sheet: WorkbookSheetSpec) -> List[WorkbookTable]:
    if sheet.tables:
        return sheet.tables
    if not sheet.financial_rows:
        return []

    rows = [
        [
            row.period,
            row.metric,
            format_currency(row.budget),
            format_currency(row.actual),
            format_currency(row.variance),
            format_currency(row.forecast),
            row.source_ref or row.source_type,
        ]
        for row in sheet.financial_rows
    ]
    return [
        WorkbookTable(
            title=f"{sheet.name} Table",
            columns=["Period", "Metric", "Budget", "Actual", "Variance", "Forecast", "Source"],
            rows=rows,
            row_provenance=[
                {
                    "source_type": row.source_type,
                    "source_ref": row.source_ref,
                    "source_excerpt": row.source_excerpt,
                }
                for row in sheet.financial_rows
            ],
        )
    ]


def format_currency(value: float) -> str:
    sign = "-" if value < 0 else ""
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{sign}${absolute / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{sign}${absolute / 1_000:.1f}K"
    return f"{sign}${absolute:.0f}"

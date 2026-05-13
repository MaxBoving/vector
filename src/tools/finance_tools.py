"""Finance tools — variance-analysis skill pattern.

VarianceAnalysisTool runs the variance analysis engine and optionally exports
the result as an XLSX workbook sheet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.finance.variance import run_variance_analysis, variance_report_to_sheet
from src.presentation.render_xlsx import render_xlsx_workbook
from src.workflows.workbook_models import WorkbookSpec

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


class VarianceAnalysisTool(BaseTool):
    metadata = ToolMetadata(
        name="variance_analysis",
        description=(
            "Compare actual vs budget or prior-period metrics. Flags threshold breaches "
            "(critical / warning), computes variance amounts and percentages, and returns "
            "a structured narrative. Optionally exports an XLSX variance sheet."
        ),
        read_only=True,
        side_effects=False,
        tags=["finance", "variance", "analysis"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        period: str = str(kwargs.get("period") or "Current Period")
        metrics: list = kwargs.get("metrics") or []
        thresholds = kwargs.get("thresholds")
        export_xlsx: bool = bool(kwargs.get("export_xlsx", False))
        output_path: str | None = kwargs.get("output_path")

        if not metrics:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error=(
                    "'metrics' list is required. Each item must contain: "
                    "metric (str), actual (float), reference (float), reference_label (str)."
                ),
            )

        # Coerce and validate each metric dict
        coerced: list[dict] = []
        for i, m in enumerate(metrics):
            try:
                coerced.append(
                    {
                        "metric": str(m.get("metric", f"Metric {i + 1}")),
                        "actual": float(m.get("actual", 0)),
                        "reference": float(m.get("reference", 0)),
                        "reference_label": str(m.get("reference_label", "Budget")),
                    }
                )
            except (TypeError, ValueError) as exc:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error=f"Invalid numeric value in metric[{i}] {m!r}: {exc}",
                )

        report = run_variance_analysis(
            period=period,
            metrics=coerced,
            thresholds=thresholds if isinstance(thresholds, dict) else None,
        )

        result_data: dict[str, Any] = {
            "period": report.period,
            "reference_label": report.reference_label,
            "critical_count": report.critical_count,
            "warning_count": report.warning_count,
            "narrative": report.narrative,
            "thresholds": report.thresholds,
            "lines": [
                {
                    "metric": l.metric,
                    "actual": l.actual,
                    "reference": l.reference,
                    "variance_abs": round(l.variance_abs, 2),
                    "variance_pct": round(l.variance_pct, 2),
                    "direction": l.direction,
                    "severity": l.severity,
                    "flag": l.flag,
                }
                for l in report.lines
            ],
        }

        if export_xlsx:
            sheet = variance_report_to_sheet(report)
            spec = WorkbookSpec(
                workbook_title=f"Variance Analysis — {period}",
                sheets=[sheet],
                metadata={
                    "template_id": "variance_v1",
                    "theme_id": "board_formal",
                    "period": period,
                },
            )
            path_str = output_path or f"variance-{period.replace(' ', '-').lower()}.xlsx"
            path = Path(path_str)
            path.parent.mkdir(parents=True, exist_ok=True)
            render_meta = render_xlsx_workbook(
                path=path,
                spec=spec,
                artifact_id=f"variance:{context.interaction_id}:{period}",
            )
            result_data["xlsx_path"] = str(path)
            result_data["render_metadata"] = render_meta

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data=result_data,
        )

from __future__ import annotations

import json
import re

from fastapi import HTTPException

from src.api.schemas import WorkbookViewResponse
from src.tools.artifact_tools import read_stage_artifact, read_stage_artifact_metadata
from src.workflows.workbook_models import WorkbookSpec, workbook_spec_to_view_model


def build_workbook_view_response(*, interaction_id: int, ceo_id: str) -> WorkbookViewResponse:
    raw_spec = read_stage_artifact(interaction_id, ceo_id, "analysis_spec")
    if not raw_spec:
        raise HTTPException(status_code=404, detail="Workbook view not found.")

    try:
        normalized = re.sub(r"^---\n.*?\n---\n+", "", raw_spec, flags=re.DOTALL).strip()
        parsed = json.loads(normalized)
        spec = WorkbookSpec(**parsed)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Workbook view payload is invalid.") from exc

    artifact_metadata = read_stage_artifact_metadata(interaction_id, ceo_id, "analysis_spec")
    view_model = workbook_spec_to_view_model(spec, artifact_id=f"interaction:{interaction_id}:analysis_xlsx")
    view_model["metadata"] = {
        "period_coverage": artifact_metadata.get("period_coverage", {}),
        "comparison_basis": _extract_comparison_basis(spec),
    }
    return WorkbookViewResponse(**view_model)


def _extract_comparison_basis(spec: WorkbookSpec) -> list[dict[str, str]]:
    basis: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for sheet in spec.sheets or []:
        for table in sheet.tables or []:
            for provenance in table.row_provenance or []:
                if not isinstance(provenance, dict):
                    continue
                if provenance.get("source_type") != "historical_artifact":
                    continue
                source_ref = str(provenance.get("source_ref") or "")
                source_excerpt = str(provenance.get("source_excerpt") or "")
                key = (source_ref, source_excerpt)
                if key in seen:
                    continue
                seen.add(key)
                basis.append(
                    {
                        "source_ref": source_ref,
                        "source_excerpt": source_excerpt,
                    }
                )
    return basis

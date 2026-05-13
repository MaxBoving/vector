from __future__ import annotations

from typing import Any, Mapping

from src.finance import DEFAULT_THEME_ID
from src.presentation import DeckSpec, MemoSpec
from src.workflows.workbook_models import WorkbookSpec

DEFAULT_MEMO_TEMPLATE_ID = "board_memo_v1"
DEFAULT_DECK_TEMPLATE_ID = "meeting_prep_deck_v1"
DEFAULT_WORKBOOK_TEMPLATE_ID = "finance_workbook_v1"


def normalize_artifact_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    default_template_id: str,
) -> dict[str, Any]:
    normalized = dict(metadata or {})
    normalized["template_id"] = str(normalized.get("template_id") or default_template_id)
    normalized["theme_id"] = str(normalized.get("theme_id") or DEFAULT_THEME_ID)
    return normalized


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def normalize_memo_spec(spec: MemoSpec) -> MemoSpec:
    sections = [
        section.model_copy(update={"items": _dedupe_preserve_order([str(item) for item in section.items if str(item).strip()])})
        for section in spec.sections
    ]
    return spec.model_copy(
        update={
            "sections": sections,
            "metadata": normalize_artifact_metadata(
                spec.metadata,
                default_template_id=DEFAULT_MEMO_TEMPLATE_ID,
            )
        }
    )


def normalize_deck_spec(spec: DeckSpec) -> DeckSpec:
    return spec.model_copy(
        update={
            "metadata": normalize_artifact_metadata(
                spec.metadata,
                default_template_id=DEFAULT_DECK_TEMPLATE_ID,
            )
        }
    )


def normalize_workbook_spec(spec: WorkbookSpec) -> WorkbookSpec:
    return spec.model_copy(
        update={
            "metadata": normalize_artifact_metadata(
                spec.metadata,
                default_template_id=DEFAULT_WORKBOOK_TEMPLATE_ID,
            )
        }
    )

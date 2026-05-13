from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from src.presentation import presentation_spec_to_deck_spec, presentation_spec_to_memo_spec

from .base import ToolContext, ToolResult

logger = logging.getLogger(__name__)

SpecT = TypeVar("SpecT", bound=BaseModel)


def ensure_output_path(*, output_path: str | None = None, output_dir: str | None = None, filename: str) -> Path:
    if output_path:
        path = Path(output_path)
    elif output_dir:
        path = Path(output_dir) / filename
    else:
        path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def coerce_json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return {}


def metadata_from_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    metadata = coerce_json_payload(kwargs.get("metadata"))
    if "template_id" in kwargs and kwargs.get("template_id") is not None:
        metadata.setdefault("template_id", kwargs.get("template_id"))
    if "theme_id" in kwargs and kwargs.get("theme_id") is not None:
        metadata.setdefault("theme_id", kwargs.get("theme_id"))
    if "presentation_version" in kwargs and kwargs.get("presentation_version") is not None:
        metadata.setdefault("presentation_version", kwargs.get("presentation_version"))
    return metadata


def _canonical_sections(value: Any) -> list[dict[str, Any]]:
    sections = value or []
    normalized: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        normalized.append(
            {
                "label": str(section.get("label") or "Section"),
                "items": [str(item) for item in (section.get("items") or [])],
            }
        )
    return normalized


def build_workbook_payload(kwargs: dict[str, Any]) -> dict[str, Any]:
    payload = coerce_json_payload(kwargs.get("workbook_spec"))
    if payload:
        return payload
    sheets = kwargs.get("sheets")
    if sheets is None and not any(key in kwargs for key in {"workbook_title", "title", "metadata", "template_id", "theme_id"}):
        return {}
    return {
        "workbook_title": kwargs.get("workbook_title") or kwargs.get("title"),
        "sheets": sheets or [],
        "metadata": metadata_from_kwargs(kwargs),
    }


def build_memo_payload(kwargs: dict[str, Any]) -> dict[str, Any]:
    payload = coerce_json_payload(kwargs.get("memo_spec"))
    if payload:
        return payload
    presentation_spec = coerce_json_payload(kwargs.get("presentation_spec"))
    if presentation_spec:
        memo = presentation_spec_to_memo_spec(
            presentation_spec,
            template_id=str(kwargs.get("template_id") or metadata_from_kwargs(kwargs).get("template_id") or "board_memo_v1"),
            theme_id=str(kwargs.get("theme_id") or metadata_from_kwargs(kwargs).get("theme_id") or "default"),
            finance_template=kwargs.get("finance_template"),
        )
        return memo.model_dump(mode="json")
    trust = kwargs.get("trust") or {}
    if not any(
        key in kwargs
        for key in {"title", "summary", "sections", "assumptions", "open_questions", "metadata", "template_id", "theme_id", "trust"}
    ):
        return {}
    return {
        "title": kwargs.get("title"),
        "summary": kwargs.get("summary") or "",
        "sections": _canonical_sections(kwargs.get("sections")),
        "assumptions": kwargs.get("assumptions") or trust.get("assumptions") or [],
        "open_questions": kwargs.get("open_questions") or trust.get("open_questions") or [],
        "metadata": metadata_from_kwargs(kwargs),
    }


def build_deck_payload(kwargs: dict[str, Any]) -> dict[str, Any]:
    payload = coerce_json_payload(kwargs.get("deck_spec"))
    if payload:
        return payload
    presentation_spec = coerce_json_payload(kwargs.get("presentation_spec"))
    if presentation_spec:
        deck = presentation_spec_to_deck_spec(
            presentation_spec,
            template_id=str(kwargs.get("template_id") or metadata_from_kwargs(kwargs).get("template_id") or "board_deck_v1"),
            theme_id=str(kwargs.get("theme_id") or metadata_from_kwargs(kwargs).get("theme_id") or "default"),
            finance_template=kwargs.get("finance_template"),
        )
        return deck.model_dump(mode="json")
    sections = kwargs.get("sections") or []
    slides = kwargs.get("slides")
    if slides is None:
        slides = [
            {
                "title": str(section.get("label") or "Slide"),
                "bullets": [str(item) for item in (section.get("items") or [])],
            }
            for section in sections
            if isinstance(section, dict)
        ]
    if not any(
        key in kwargs
        for key in {"title", "subtitle", "slides", "sections", "metadata", "template_id", "theme_id"}
    ):
        return {}
    return {
        "title": kwargs.get("title"),
        "subtitle": kwargs.get("subtitle"),
        "slides": slides or [],
        "slide_order": kwargs.get("slide_order") or [],
        "metadata": metadata_from_kwargs(kwargs),
    }


@dataclass(frozen=True)
class ArtifactRequestConfig:
    tool_name: str
    payload_kind: str
    model_cls: type[SpecT]
    default_filename: str
    payload_builder: Callable[[dict[str, Any]], dict[str, Any]]


def parse_artifact_request(
    *,
    context: ToolContext,
    kwargs: dict[str, Any],
    config: ArtifactRequestConfig,
) -> tuple[Path, SpecT] | ToolResult:
    path = ensure_output_path(
        output_path=kwargs.get("output_path"),
        output_dir=kwargs.get("output_dir"),
        filename=str(kwargs.get("filename") or config.default_filename),
    )
    payload = config.payload_builder(kwargs)
    try:
        spec = config.model_cls(**payload)
    except ValidationError as exc:
        logger.warning(
            "Invalid %s payload for %s; ceo_id=%s interaction_id=%s stage=%s payload_keys=%s kwargs_keys=%s error=%s",
            config.payload_kind,
            config.tool_name,
            context.ceo_id,
            context.interaction_id,
            context.stage,
            sorted(payload.keys()),
            sorted(kwargs.keys()),
            exc,
        )
        return ToolResult(
            tool_name=config.tool_name,
            success=False,
            data={"path": str(path)},
            error=f"Invalid {config.payload_kind} payload: {exc}",
            metadata={"payload_keys": sorted(payload.keys())},
        )
    return path, spec

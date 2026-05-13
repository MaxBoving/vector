from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Literal, cast

from pydantic import BaseModel, Field

from src.presentation import DEFAULT_THEME_ID, DEFAULT_WORKBOOK_TEMPLATE_ID, get_workbook_template


FinanceTemplateType = Literal[
    "cost_review",
    "runway_review",
    "project_spend_review",
    "budget_variance_review",
    "board_financial_update",
]


class FinanceTemplateDefinition(BaseModel):
    template_type: FinanceTemplateType
    label: str
    expected_metric_keys: List[str] = Field(default_factory=list)
    expected_period_granularities: List[str] = Field(default_factory=list)
    primary_chart_type: str = "bar"
    primary_chart_title: str = ""
    default_theme_id: str = DEFAULT_THEME_ID
    workbook_template_id: str = DEFAULT_WORKBOOK_TEMPLATE_ID

    @property
    def workbook_tabs(self) -> List[str]:
        return list(get_workbook_template(self.workbook_template_id).tab_order)


def _load_registry() -> dict[str, Any]:
    registry_path = Path(__file__).with_name("template_registry.json")
    with registry_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


_REGISTRY = _load_registry()

FINANCE_TEMPLATES: dict[FinanceTemplateType, FinanceTemplateDefinition] = {
    cast(FinanceTemplateType, template_type): FinanceTemplateDefinition(
        template_type=cast(FinanceTemplateType, template_type),
        **template_config,
    )
    for template_type, template_config in _REGISTRY["templates"].items()
}

FINANCE_TEMPLATE_ALIASES: dict[str, FinanceTemplateType] = {
    alias: cast(FinanceTemplateType, canonical)
    for alias, canonical in _REGISTRY.get("aliases", {}).items()
}

DEFAULT_FINANCE_SECTION_LABELS: list[str] = _REGISTRY["defaults"]["section_labels"]

FINANCE_TEMPLATE_SECTION_LABELS: dict[FinanceTemplateType, list[str]] = {
    cast(FinanceTemplateType, template_type): config.get("section_labels", DEFAULT_FINANCE_SECTION_LABELS)
    for template_type, config in _REGISTRY["templates"].items()
}

FINANCE_TEMPLATE_PRIMARY_VISUALS: dict[FinanceTemplateType, dict[str, str]] = {
    cast(FinanceTemplateType, template_type): config.get("primary_visual", {})
    for template_type, config in _REGISTRY["templates"].items()
}

def resolve_finance_template_type(template_type: str) -> FinanceTemplateType:
    resolved = FINANCE_TEMPLATE_ALIASES.get(template_type, template_type)
    return cast(FinanceTemplateType, resolved)


def get_finance_template_definition(template_type: FinanceTemplateType) -> FinanceTemplateDefinition:
    return FINANCE_TEMPLATES[template_type]


def get_finance_template_definition_resolved(template_type: str) -> FinanceTemplateDefinition:
    return get_finance_template_definition(resolve_finance_template_type(template_type))


def get_finance_template_section_labels(template_type: str) -> list[str]:
    resolved = resolve_finance_template_type(template_type)
    return list(FINANCE_TEMPLATE_SECTION_LABELS.get(resolved, DEFAULT_FINANCE_SECTION_LABELS))


def get_finance_template_primary_visual(template_type: str) -> dict[str, str] | None:
    resolved = resolve_finance_template_type(template_type)
    visual = FINANCE_TEMPLATE_PRIMARY_VISUALS.get(resolved)
    return dict(visual) if visual else None


def get_finance_template_expected_metrics(template_type: str) -> list[str]:
    try:
        return get_finance_template_definition_resolved(template_type).expected_metric_keys
    except Exception:
        return []


def get_finance_template_expected_periods(template_type: str) -> list[str]:
    try:
        return get_finance_template_definition_resolved(template_type).expected_period_granularities
    except Exception:
        return ["Current Quarter"]


def get_finance_template_request_options() -> list[str]:
    configured = _REGISTRY.get("request_template_options", [])
    if isinstance(configured, list):
        options = [item for item in configured if isinstance(item, str) and item]
        if options:
            return options

    options: list[str] = []
    for template_key in _REGISTRY.get("aliases", {}).keys():
        if isinstance(template_key, str) and template_key not in options:
            options.append(template_key)
    for template_key in _REGISTRY.get("templates", {}).keys():
        if isinstance(template_key, str) and template_key not in options:
            options.append(template_key)
    default_template = _REGISTRY.get("defaults", {}).get("template")
    if isinstance(default_template, str) and default_template not in options:
        options.append(default_template)
    return options


def get_default_finance_template() -> str:
    default_template = _REGISTRY.get("defaults", {}).get("template")
    if isinstance(default_template, str) and default_template:
        return default_template
    options = get_finance_template_request_options()
    return options[-1] if options else next(iter(_REGISTRY.get("templates", {}).keys()), "")

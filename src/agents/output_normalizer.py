from __future__ import annotations

from src.agents.composition_plan import CompositionPlan
from src.agents.report_agent import ReportPayload, ReportSection


class OutputNormalizer:
    """
    Deterministic post-generation contract enforcer.
    Applies CompositionPlan.section_labels to the payload and pads structure.
    Never generates content — only sets labels on existing sections and adds
    placeholder sections if the LLM produced fewer than 3.
    """

    _PLACEHOLDER_ITEM = "See full report for details."

    def normalize(self, payload: ReportPayload, plan: CompositionPlan) -> ReportPayload:
        payload = payload.model_copy(deep=True)
        labels = plan.section_labels
        existing = list(payload.answer.sections)

        normalized: list[ReportSection] = []
        for i, label in enumerate(labels):
            if i < len(existing):
                section = existing[i].model_copy(deep=True)
                section.label = label
            else:
                section = ReportSection(label=label, items=[self._PLACEHOLDER_ITEM])
            normalized.append(section)

        # Preserve any extra sections the LLM produced beyond the 3 planned labels
        if len(existing) > len(labels):
            normalized.extend(existing[len(labels):])

        payload.answer.sections = normalized
        return payload

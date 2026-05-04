from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from src.agents.report_agent import ReportPayload


class CompositionPlan(BaseModel):
    """
    The LLM's upfront decisions about response shape.
    Produced alongside ReportPayload in a single structured completion.
    Enforced deterministically post-generation by OutputNormalizer and CapabilityGuard.
    """

    section_labels: List[str] = Field(
        description=(
            "Exactly 3 section labels chosen for this specific request. "
            "Examples: ['Competitive Position', 'Margin Impact', 'Strategic Options'] "
            "for pricing queries; ['Risk Summary', 'Recovery Actions', 'Owner Assignments'] "
            "for customer escalations; ['Financial Snapshot', 'Board Implications', 'Recommended Actions'] "
            "for board financial reviews. Choose freely — do not default to finance labels for non-finance requests."
        )
    )
    context_gaps: List[str] = Field(
        default_factory=list,
        description=(
            "Information genuinely missing to answer this request well. "
            "Empty list if you can answer with available context. "
            "These surface in the response as missing_context — they do not block generation."
        ),
    )
    output_modality: str = Field(
        description=(
            "Best output format for this request. "
            "One of: 'docx', 'xlsx', 'pptx', 'docx+xlsx', 'pptx+xlsx', 'inline'. "
            "Use 'inline' for conversational or simple factual responses. "
            "Use 'docx' for memos and briefs. Use 'xlsx' for financial models. "
            "Use 'pptx' for board presentations."
        )
    )
    capability_requires: List[str] = Field(
        default_factory=list,
        description=(
            "Write capabilities this response claims to exercise. "
            "Use 'email_send' if offering to send an email. "
            "Use 'calendar_write' if offering to create a calendar event. "
            "Leave empty if the response only drafts content for manual execution."
        ),
    )


class ReportCompletionWithPlan(BaseModel):
    """
    Combined LLM output: composition decisions + report content.
    Both produced in a single structured completion call.
    """

    plan: CompositionPlan
    payload: ReportPayload

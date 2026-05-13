from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class PriorConversationRef(BaseModel):
    """A specific prior conversation turn relevant to the current query."""
    turn_summary: str           # "3 days ago: CEO asked about burn rate in context of Q2 hiring"
    interaction_id: Optional[int] = None
    days_ago: Optional[int] = None
    intent: Optional[str] = None


class RetrievalManifest(BaseModel):
    """Describes exactly what was loaded for this workflow run.

    Passed to agents alongside prepared_context so preambles
    can be specific rather than templated.
    """
    documents_loaded: List[str] = Field(default_factory=list)
    # e.g. ["Q1 Finance Close", "AWS Variance Report (low authority)"]

    signals_found: int = 0
    signals_summary: List[str] = Field(default_factory=list)
    # e.g. ["AWS spend variance flagged as unread", "Finance close signal active"]

    memories_surfaced: List[str] = Field(default_factory=list)
    # e.g. ["CEO committed to holding runway > 18 months (2026-03-15)"]

    live_threads_scanned: int = 0
    live_events_scanned: int = 0

    prior_conversation_refs: List[PriorConversationRef] = Field(default_factory=list)
    # Most relevant prior turns retrieved from session history

    retrieval_gaps: List[str] = Field(default_factory=list)
    # e.g. ["No board deck found for current quarter", "Finance close docs are 6 weeks old"]

    crm_deals_loaded: int = 0

    def is_rich(self) -> bool:
        """True if enough was found to write a specific preamble."""
        return bool(
            self.documents_loaded
            or self.signals_found > 0
            or self.memories_surfaced
            or self.prior_conversation_refs
        )

    def to_prompt_block(self) -> str:
        """Render as a structured block injected into agent prompts."""
        if not self.is_rich():
            return "=== WHAT WAS LOADED ===\nNo supporting documents, signals, or memories were found.\n\n"

        lines = ["=== WHAT WAS LOADED (use this to write a specific preamble) ==="]

        if self.documents_loaded:
            lines.append(f"Documents ({len(self.documents_loaded)}): " + ", ".join(self.documents_loaded))

        if self.signals_found:
            lines.append(f"Signals ({self.signals_found}): " + "; ".join(self.signals_summary[:3]))

        if self.memories_surfaced:
            lines.append("CEO memories: " + "; ".join(self.memories_surfaced[:3]))

        if self.live_threads_scanned:
            lines.append(f"Live email threads scanned: {self.live_threads_scanned}")

        if self.live_events_scanned:
            lines.append(f"Live calendar events scanned: {self.live_events_scanned}")

        if self.prior_conversation_refs:
            ref_lines = [f"  - {r.turn_summary}" for r in self.prior_conversation_refs[:3]]
            lines.append("Relevant prior conversations:\n" + "\n".join(ref_lines))

        if self.retrieval_gaps:
            lines.append("Gaps / missing data: " + "; ".join(self.retrieval_gaps))

        return "\n".join(lines) + "\n\n"

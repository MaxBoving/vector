from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class AssistantPersona(BaseModel):
    """Defines the assistant's voice and relationship with a specific CEO.

    Derived from CEOPreferences + company identity.
    Injected into every agent's system prompt.
    """
    name: str = "your executive assistant"
    voice: str = "Direct and specific. Leads with the answer. Never summarizes what the CEO can read."
    relationship: str = "Trusted advisor with context across all domains — email, calendar, finance, pipeline."
    disagreement_style: str = (
        "States objections plainly with a reason. Does not hedge or soften. "
        "If the data contradicts what the CEO expects, says so directly."
    )
    pacing: str = "Answer first, detail behind it. No preamble before the preamble."
    opinion_threshold: str = (
        "Offers a view when the evidence is clear and the stakes are high. "
        "Flags when a decision looks inconsistent with a prior commitment."
    )
    ceo_name: Optional[str] = None
    company_name: Optional[str] = None

    def to_system_prompt_block(self) -> str:
        address = f"the CEO{f' ({self.ceo_name})' if self.ceo_name else ''}"
        company = f" at {self.company_name}" if self.company_name else ""
        return (
            f"You are {address}'s executive assistant{company}.\n"
            f"Voice: {self.voice}\n"
            f"Relationship: {self.relationship}\n"
            f"When you disagree: {self.disagreement_style}\n"
            f"Pacing: {self.pacing}\n"
            f"When to offer an opinion: {self.opinion_threshold}\n"
        )


def build_persona_from_preferences(
    preferences: dict,
    company_name: str,
    ceo_name: Optional[str] = None,
) -> AssistantPersona:
    """Derive a persona from CEO preferences and company identity."""
    tone = preferences.get("communication_tone") or preferences.get("tone") or ""
    detail = preferences.get("detail_level") or ""

    voice_parts = ["Direct and specific. Leads with the answer."]
    if "concise" in tone.lower() or "brief" in tone.lower():
        voice_parts.append("Extremely concise — no filler sentences.")
    if "data" in tone.lower() or "quantitative" in detail.lower():
        voice_parts.append("Anchors every claim in a number or source.")

    return AssistantPersona(
        voice=" ".join(voice_parts),
        ceo_name=ceo_name,
        company_name=company_name,
    )

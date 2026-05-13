"""Company vocabulary extraction and prompt injection.

Implements the memory-management skill pattern: extract company-specific
terminology from CompanyState and indexed documents, then inject a concise
vocabulary block into agent prompts so the LLM uses the right language.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class CompanyVocabulary(BaseModel):
    """Extracted company terminology for prompt injection."""

    product_names: List[str] = Field(default_factory=list)
    team_names: List[str] = Field(default_factory=list)
    key_people: List[Dict[str, str]] = Field(default_factory=list)  # [{name, role}]
    metric_nicknames: List[str] = Field(default_factory=list)
    company_shorthand: List[str] = Field(default_factory=list)
    domain_tags: List[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any([
            self.product_names, self.team_names, self.key_people,
            self.metric_nicknames, self.company_shorthand, self.domain_tags,
        ])


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

_METRIC_NICKNAME_PATTERN = re.compile(
    r'\b([A-Z]{2,6}(?:\s+\d+)?)\b'  # 2-6 uppercase letters, optionally followed by a number
)
_TEAM_KEYWORDS = {"team", "squad", "pod", "division", "group", "department", "dept", "unit", "org"}
_PRODUCT_KEYWORDS = {"product", "platform", "service", "app", "tool", "module", "api", "sdk", "stack"}


def _extract_capitalized_phrases(text: str, keyword_set: set[str], max_tokens: int = 4) -> List[str]:
    """Extract capitalized multi-word phrases that contain at least one keyword."""
    results: List[str] = []
    tokens = text.split()
    for i, token in enumerate(tokens):
        clean = token.strip(".,;:()\"'").lower()
        if clean in keyword_set and i > 0:
            # grab up to max_tokens preceding capitalized words
            phrase_tokens: List[str] = []
            j = i - 1
            while j >= max(0, i - max_tokens) and tokens[j][0].isupper():
                phrase_tokens.insert(0, tokens[j].strip(".,;:()\"'"))
                j -= 1
            phrase_tokens.append(token.strip(".,;:()\"'"))
            if phrase_tokens:
                candidate = " ".join(phrase_tokens)
                if candidate not in results:
                    results.append(candidate)
    return results


def _extract_metric_nicknames(text: str) -> List[str]:
    """Extract likely metric acronyms (NRR, ARR, MoM, EBITDA, etc.)."""
    known_finance = {
        "ARR", "MRR", "NRR", "GRR", "CAC", "LTV", "EBITDA", "COGS", "GMV",
        "NPS", "ARPU", "ARPA", "DAU", "MAU", "WAU", "MoM", "QoQ", "YoY",
        "OPEX", "CAPEX", "P&L", "FCF", "ROI", "IRR", "NPV", "WACC", "OKR",
    }
    found: List[str] = []
    for match in _METRIC_NICKNAME_PATTERN.finditer(text):
        candidate = match.group(1)
        if candidate in known_finance and candidate not in found:
            found.append(candidate)
    return found


def _org_structure_to_people(org_structure: Dict[str, Any]) -> List[Dict[str, str]]:
    """Convert org_structure dict to [{name, role}] list."""
    people: List[Dict[str, str]] = []
    for role, name in org_structure.items():
        if isinstance(name, str) and name.strip():
            people.append({"name": name.strip(), "role": str(role).replace("_", " ").title()})
    return people


def _extract_domain_tags(knowledge_base: List[Dict[str, Any]]) -> List[str]:
    """Collect unique domain tags from knowledge base docs."""
    tags: List[str] = []
    for doc in knowledge_base:
        for domain in doc.get("domains", []):
            if domain and domain not in tags:
                tags.append(domain)
    return tags


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

def extract_company_vocabulary(
    company_state: Dict[str, Any],
    knowledge_base: List[Dict[str, Any]] | None = None,
) -> CompanyVocabulary:
    """
    Scan company state and knowledge base documents to build a CompanyVocabulary.

    Args:
        company_state: dict from CompanyState (or its serialized form)
        knowledge_base: list of knowledge_base doc dicts from CompanyState
    """
    kb = knowledge_base or company_state.get("knowledge_base", []) or []

    # Key people from org_structure
    org = company_state.get("org_structure") or {}
    key_people = _org_structure_to_people(org)

    # Build a corpus from all text fields for phrase extraction
    corpus_parts: List[str] = []
    for doc in kb:
        content = doc.get("content", "") or ""
        corpus_parts.append(content[:2000])  # cap per-doc to avoid noise
        title = doc.get("title", "")
        if title:
            corpus_parts.append(title)
    # Include initiative names
    for initiative in company_state.get("strategic_initiatives", []) or []:
        name = initiative.get("name", "") or initiative.get("title", "")
        if name:
            corpus_parts.append(name)

    corpus = " ".join(corpus_parts)

    product_names = _extract_capitalized_phrases(corpus, _PRODUCT_KEYWORDS)
    team_names = _extract_capitalized_phrases(corpus, _TEAM_KEYWORDS)
    metric_nicknames = _extract_metric_nicknames(corpus)
    domain_tags = _extract_domain_tags(kb)

    # Company shorthand: initiative abbreviations and any parenthetical abbreviations in corpus
    shorthand: List[str] = []
    for match in re.finditer(r'\(([A-Z]{2,6})\)', corpus):
        abbr = match.group(1)
        if abbr not in shorthand and abbr not in metric_nicknames:
            shorthand.append(abbr)

    return CompanyVocabulary(
        product_names=product_names[:10],
        team_names=team_names[:10],
        key_people=key_people,
        metric_nicknames=metric_nicknames[:15],
        company_shorthand=shorthand[:10],
        domain_tags=domain_tags[:10],
    )


# ---------------------------------------------------------------------------
# Prompt injection
# ---------------------------------------------------------------------------

def vocabulary_prompt_block(vocab: CompanyVocabulary) -> str:
    """
    Render the vocabulary as a concise prompt block for agent injection.
    Returns an empty string if the vocabulary is empty.
    """
    if vocab.is_empty:
        return ""

    lines: List[str] = ["=== COMPANY VOCABULARY ===",
                        "Use this terminology exactly as written. Do not substitute generic synonyms."]

    if vocab.key_people:
        people_str = ", ".join(f"{p['name']} ({p['role']})" for p in vocab.key_people[:6])
        lines.append(f"Key people: {people_str}")

    if vocab.product_names:
        lines.append(f"Products/platforms: {', '.join(vocab.product_names)}")

    if vocab.team_names:
        lines.append(f"Teams/orgs: {', '.join(vocab.team_names)}")

    if vocab.metric_nicknames:
        lines.append(f"Metric shorthand in use: {', '.join(vocab.metric_nicknames)}")

    if vocab.company_shorthand:
        lines.append(f"Company abbreviations: {', '.join(vocab.company_shorthand)}")

    if vocab.domain_tags:
        lines.append(f"Active domain focus areas: {', '.join(vocab.domain_tags)}")

    lines.append("")
    return "\n".join(lines)

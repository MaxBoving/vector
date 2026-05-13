from __future__ import annotations
import re
from typing import List

# Known entity patterns for a CEO context
_DEAL_PATTERN = re.compile(
    r'\b([A-Z][a-z]+(?: [A-Z][a-z]+)*)'
    r'(?:\s+(?:deal|account|client|customer|contract|partnership|pilot|renewal))?\b'
)
_PERSON_PATTERN = re.compile(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b')

# Common false positives to exclude
_EXCLUSIONS = frozenset({
    "United States", "New York", "San Francisco", "North America",
    "South America", "Board Meeting", "Finance Close", "Chief Executive",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "June", "July", "August",
    "September", "October", "November", "December",
})


def extract_entities_from_text(text: str) -> List[str]:
    """
    Lightweight entity extraction from CEO context text.
    Returns deduplicated list of named entities.

    Heuristic covers the majority of CEO assistant entity types:
    deal names, person names, project names, and key finance topics.
    """
    if not text:
        return []

    entities = set()

    # Deal/company names: capitalized proper nouns
    for match in _DEAL_PATTERN.finditer(text):
        candidate = match.group(0).strip()
        if len(candidate) > 3 and candidate not in _EXCLUSIONS:
            entities.add(candidate)

    # Person names: two consecutive capitalized words
    for match in _PERSON_PATTERN.finditer(text):
        candidate = match.group(0)
        if candidate not in _EXCLUSIONS:
            entities.add(candidate)

    # Explicit finance/product topics
    finance_topics = [
        "AWS variance", "burn rate", "runway", "board pack", "finance close",
        "Q1", "Q2", "Q3", "Q4",
    ]
    lowered = text.lower()
    for topic in finance_topics:
        if topic.lower() in lowered:
            entities.add(topic)

    return sorted(entities)[:15]  # cap to avoid noise

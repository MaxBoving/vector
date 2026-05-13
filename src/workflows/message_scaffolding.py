from __future__ import annotations

import re


_FOLLOWUP_PREFIXES = (
    "Follow-up action:",
    "CEO follow-up:",
    "CEO context:",
)


def extract_visible_request_text(message: str | None) -> str:
    text = (message or "").strip()
    if not text:
        return ""

    current = text
    for _ in range(6):
        updated = _strip_one_wrapper(current).strip()
        if updated == current:
            break
        current = updated
    return current


def _strip_one_wrapper(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""

    for prefix in _FOLLOWUP_PREFIXES:
        marker_idx = stripped.lower().find(prefix.lower())
        if marker_idx >= 0:
            return stripped[marker_idx + len(prefix):].strip()

    context_block = re.match(r"^\[Context:[\s\S]{0,2400}?\]\s*", stripped)
    if context_block:
        remainder = stripped[context_block.end():].strip()
        if remainder:
            return remainder

    original_question_idx = stripped.lower().find("[original question:")
    if original_question_idx == 0:
        split_idx = stripped.lower().find("ceo context:")
        if split_idx >= 0:
            return stripped[split_idx + len("CEO context:"):].strip()

    return stripped

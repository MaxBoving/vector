"""Response formatter — post-processing pass after the agent tool loop.

Takes the raw answer text + list of tools that were called, and returns a
structured AnswerPayload with title, sections, and action items.

Response type is determined deterministically from the tool list (no LLM).
A lightweight Haiku call extracts structure from the text.
Falls back to raw text if the Haiku call fails.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

from src.api.schemas import AnswerPayload, AnswerSection

logger = logging.getLogger(__name__)

_HAIKU_MODEL = "claude-haiku-4-5-20251001"

# ---------------------------------------------------------------------------
# Response type detection — deterministic from tool names
# ---------------------------------------------------------------------------

_TYPE_RULES: list[tuple[frozenset[str], str]] = [
    (frozenset({"read_email_threads", "read_calendar_events", "get_recent_signals"}), "morning_brief"),
    (frozenset({"read_email_threads"}), "inbox_summary"),
    (frozenset({"read_calendar_events"}), "calendar_summary"),
    (frozenset({"crm_deal_context"}), "pipeline_summary"),
    (frozenset({"get_entity_context"}), "entity_lookup"),
    (frozenset({"memory_management"}), "memory_response"),
    (frozenset({"create_docx_memo", "create_pptx_deck", "create_workbook", "create_canvas"}), "document_created"),
    (frozenset({"slack_read", "slack_post"}), "slack_response"),
    (frozenset({"google_drive_search", "google_drive_read"}), "drive_response"),
]


def _detect_response_type(tools_called: list[str]) -> str:
    called = frozenset(tools_called)
    # Morning brief requires at least 2 of the 3 signals
    brief_overlap = called & frozenset({"read_email_threads", "read_calendar_events", "get_recent_signals"})
    if len(brief_overlap) >= 2:
        return "morning_brief"
    for required, response_type in _TYPE_RULES[1:]:  # skip morning_brief — already handled
        if called & required:
            return response_type
    return "conversational"


# ---------------------------------------------------------------------------
# Haiku extraction prompt per response type
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPTS: dict[str, str] = {
    "morning_brief": (
        "Extract a structured morning brief. Return JSON with:\n"
        "- title: string (e.g. 'Morning Brief — May 4')\n"
        "- sections: array of {label, content, items} — e.g. Urgent, Calendar, FYI\n"
        "- action_items: array of strings (concrete things to do today)\n"
        "- one_liner: one sentence summary of the day"
    ),
    "inbox_summary": (
        "Extract a structured inbox summary. Return JSON with:\n"
        "- title: string (e.g. '4 emails need attention')\n"
        "- sections: array of {label, content, items} — e.g. Urgent, Needs Reply, FYI\n"
        "- action_items: array of strings (emails to reply to or act on)\n"
        "- one_liner: one sentence inbox status"
    ),
    "calendar_summary": (
        "Extract a structured calendar summary. Return JSON with:\n"
        "- title: string (e.g. '3 meetings this week')\n"
        "- sections: array of {label, content, items} — e.g. Today, Tomorrow, This Week\n"
        "- action_items: array of strings (prep needed for any meeting)\n"
        "- one_liner: one sentence schedule summary"
    ),
    "pipeline_summary": (
        "Extract a structured pipeline summary. Return JSON with:\n"
        "- title: string (e.g. 'Pipeline — 5 active deals')\n"
        "- sections: array of {label, content, items} — e.g. Hot, Stalled, New\n"
        "- action_items: array of strings (deals needing action)\n"
        "- one_liner: one sentence pipeline health"
    ),
    "entity_lookup": (
        "Extract a structured entity summary. Return JSON with:\n"
        "- title: string (the entity name)\n"
        "- sections: array of {label, content, items} — e.g. Background, Recent Activity, Key Contacts\n"
        "- action_items: array of strings (follow-ups if any)\n"
        "- one_liner: one sentence about the entity"
    ),
    "conversational": (
        "Structure this CEO assistant response. Return JSON with these fields:\n\n"
        "title: A 2-5 word label that names the topic — NOT a sentence, NOT copied from the text.\n"
        "  Good: 'Series B Readiness', 'Board Meeting Date', 'Hiring Freeze Policy'\n"
        "  Bad: 'The board meeting is June 12th', 'Here is the information', 'Summary'\n\n"
        "sections: Use sections ONLY when the answer contains 3+ distinct points that benefit from grouping.\n"
        "  Each section must synthesize and label — never copy sentences verbatim as a section body.\n"
        "  Return [] for answers that are 1-2 sentences, direct facts, or already well-structured prose.\n\n"
        "action_items: Include ONLY if the text explicitly states something the CEO must do.\n"
        "  Each item MUST be a plain string — never an object or dict.\n"
        "  Good: 'Send updated cap table to James Park by April 10'\n"
        "  Bad: {\"action\": \"Send cap table\", \"deadline\": \"April 10\"}\n"
        "  Must be specific: include who, what, and when if mentioned in the text.\n"
        "  Return [] if the answer is purely informational with no stated next steps.\n\n"
        "one_liner: The single most important thing the CEO needs to know. Distill — don't restate."
    ),
}

# Types where formatting adds little value — skip the Haiku call
_SKIP_FORMAT_TYPES = {"memory_response", "document_created", "slack_response", "drive_response"}

# Minimum text length to bother formatting
_MIN_FORMAT_LENGTH = 80


# ---------------------------------------------------------------------------
# Core formatter
# ---------------------------------------------------------------------------

class ResponseFormatter:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic()

    def format(self, text: str, tools_called: list[str]) -> AnswerPayload:
        response_type = _detect_response_type(tools_called)

        # Short or low-value responses — skip extraction
        if response_type in _SKIP_FORMAT_TYPES or len(text) < _MIN_FORMAT_LENGTH:
            return AnswerPayload(
                title=_quick_title(text, response_type),
                summary=text,
                sections=[],
            )

        extracted = self._extract(text, response_type)
        if not extracted:
            return AnswerPayload(title="", summary=text, sections=[])

        def _str_items(raw: Any) -> list[str]:
            """Coerce items to strings — Haiku sometimes returns dicts."""
            if not raw:
                return []
            return [i if isinstance(i, str) else json.dumps(i) for i in raw if i]

        sections = [
            AnswerSection(
                label=s.get("label") or s.get("heading") or "",
                content=s.get("content") or s.get("body") or "",
                items=_str_items(s.get("items")),
            )
            for s in (extracted.get("sections") or [])
            if s.get("label") or s.get("heading")
        ]

        # Append action items as a dedicated section if present
        action_items = _str_items(extracted.get("action_items"))
        if action_items:
            sections.append(AnswerSection(label="Action Items", content="", items=action_items))

        return AnswerPayload(
            title=extracted.get("title") or "",
            summary=extracted.get("one_liner") or text,
            sections=sections,
        )

    def _extract(self, text: str, response_type: str) -> dict[str, Any] | None:
        prompt = _EXTRACTION_PROMPTS.get(response_type, _EXTRACTION_PROMPTS["conversational"])
        try:
            response = self._client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"{prompt}\n\n"
                            "Respond with valid JSON only. No explanation, no markdown fences.\n\n"
                            f"TEXT:\n{text}"
                        ),
                    }
                ],
            )
            raw = response.content[0].text.strip()
            # Strip fences if Haiku adds them anyway
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)
        except Exception as exc:
            logger.warning("ResponseFormatter extraction failed (%s): %s", response_type, exc)
            return None


def _quick_title(text: str, response_type: str) -> str:
    """Generate a minimal title without an LLM call.

    For short answers the summary is the answer — a title that copies the text
    adds no value, so return empty and let the frontend omit it.
    """
    first_line = text.strip().split("\n")[0].lstrip("#").strip()
    # Only use the first line as a title if it reads like a heading (ends without
    # sentence punctuation and is short enough to be a label, not a sentence).
    if first_line and len(first_line) <= 40 and not first_line[-1] in ".?!":
        return first_line
    return ""

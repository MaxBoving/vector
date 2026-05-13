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

from src.api.schemas import AnswerPayload, AnswerSection, ChartDataPoint, ChartSpec, FollowUpChip, SectionType, TrustMetadata

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
        "Extract a structured morning brief. Return JSON with EXACTLY these fields:\n\n"
        "title: string — e.g. 'Morning Brief — Monday May 4'\n\n"
        "priorities: array of strings — MAXIMUM 4 items. TODAY only: decisions, approvals, or actions "
        "that must happen before end of business today. If a deadline or time is mentioned (noon, 3 PM, EOD), "
        "include it in the item. Drop anything that can wait until tomorrow.\n"
        "  Good: 'Board packet markup by noon — CEO narrative for slides 4, 9, 12'\n"
        "  Good: 'Cloud containment decision by 3 PM — $591K/month approaching $650K covenant'\n"
        "  Bad: 'Schedule Northstar call this week' (not today)\n"
        "  Bad: 'Kepler beta approvals' with no today-specific deadline\n\n"
        "upcoming: array of strings — milestones, meetings, and deadlines later THIS WEEK or next. "
        "NOT today. Each item must include a date or day. "
        "Good: 'Monday: VP Engineering competing offer expires'. Bad: 'VP Engineering offer' with no date.\n\n"
        "risks: array of strings — active blockers or things that could go wrong. "
        "Only items the text explicitly flags as a risk or concern. "
        "Do NOT put deadlines or meetings here. Return [] if nothing qualifies.\n\n"
        "one_liner: one sentence naming the 1-2 most time-critical decisions and their specific deadlines today.\n\n"
        "follow_ups: array of {label, prompt} — 3-5 follow-up actions grounded in THIS brief's content. "
        "label is 2-4 words (chip label). prompt is the full question the CEO would ask next.\n"
        "  Examples based on content:\n"
        "  {\"label\": \"Northstar briefing\", \"prompt\": \"Give me a full briefing on the Northstar Health renewal — status, contacts, and what I need to say on the call\"}\n"
        "  {\"label\": \"Draft board narrative\", \"prompt\": \"Draft the CEO narrative for the board packet covering cloud cost variance for slides 4, 9, 12\"}\n"
        "  {\"label\": \"VP Eng offer details\", \"prompt\": \"Show me the VP Engineering offer details and the competing offer timeline\"}\n"
        "  {\"label\": \"Show this week\", \"prompt\": \"What are all my priorities and deadlines for this week?\"}\n"
        "  {\"label\": \"Show inbox\", \"prompt\": \"What's in my inbox that needs attention today?\"}\n"
        "Never use generic labels. Always tie to actual names, deals, or decisions in the brief.\n\n"
        "Return [] for any array where you have nothing confident to say."
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

# Response types where a chart extraction pass is worthwhile
_CHART_WORTHY_TYPES = {"pipeline_summary", "conversational", "morning_brief", "calendar_summary", "inbox_summary"}

_CHART_RULES = (
    "Return a JSON object in EXACTLY this format if a chart is appropriate:\n"
    '{"type":"bar","title":"short title","subtitle":"one-line takeaway","data":[{"label":"...","value":number}],'
    '"value_format":"currency"|"percent"|"number","color_scheme":"pipeline"|"finance"|"neutral"}\n\n'
    "Return the single word null (not quoted, not in an object) if no chart is appropriate.\n\n"
    "Rules:\n"
    "- Only generate a chart if there are 2 or more comparable numerical values\n"
    "- value must be a raw number, never a string\n"
    "- label must be 1-4 words, no punctuation\n"
    "- use value_format 'currency' for dollar amounts, 'percent' for 0-100 percentages, 'number' otherwise\n"
    "- use color_scheme 'pipeline' for deal/sales data, 'finance' for revenue/cost/burn data, 'neutral' otherwise\n"
    "- omit null or zero values from data array\n"
    "- max 8 data points — keep only the most meaningful\n"
    "- do NOT generate a chart for plain text, emails, calendar titles, or qualitative data\n\n"
)

_CHART_PROMPT = (
    "You are given raw JSON from a tool result. Decide if a simple bar chart would help "
    "a CEO understand the key numbers at a glance.\n\n"
    + _CHART_RULES
    + "Tool result JSON:\n"
)

_CHART_PROMPT_TEXT = (
    "You are given an executive assistant response. Decide if a simple bar chart would help "
    "communicate the key numbers at a glance.\n\n"
    + _CHART_RULES
    + "Response text:\n"
)


# ---------------------------------------------------------------------------
# Trust computation — deterministic, no LLM
# ---------------------------------------------------------------------------

# Maps tool name → label used in trust.missing_context entries.
# Shared with agent.py for preamble generation.
TOOL_LABELS: dict[str, str] = {
    "read_email_threads": "email",
    "read_calendar_events": "calendar",
    "crm_deal_context": "CRM / pipeline",
    "get_recent_signals": "signals",
    "get_live_context": "live context",
    "get_company_state": "company state",
    "get_entity_context": "entity context",
    "slack_read": "Slack",
    "google_drive_search": "Google Drive",
    "google_drive_read": "Google Drive",
}


def _compute_trust(
    tools_called: list[str],
    tool_results: dict[str, str],
) -> TrustMetadata:
    if not tools_called:
        return TrustMetadata(confidence="high", confidence_score=0.9, data_quality="high")

    # Write/logging tools don't affect data confidence
    _WRITE_TOOLS = {"write_thread_entry", "update_situational_profile", "memory_management"}

    good, missing, errors = [], [], []
    for name, result in tool_results.items():
        if name in _WRITE_TOOLS:
            continue
        # execute_tool returns {"error": "..."} on failure, plain data dict on success
        try:
            parsed = json.loads(result)
            err = parsed.get("error") if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, AttributeError):
            err = None

        _err_lower = err.lower() if err else ""
        if err and ("connected" in _err_lower or "/connect" in _err_lower):
            missing.append(name)
            logger.info("trust[%s]: not_connected — %s", name, err[:80])
        elif err:
            errors.append(name)
            logger.info("trust[%s]: error — %s", name, err[:80])
        else:
            good.append(name)
            logger.info("trust[%s]: ok", name)

    total = len(tool_results) or 1
    coverage = len(good) / total
    score = round(0.5 + coverage * 0.4, 2)  # 0.50–0.90 range
    confidence: str = "high" if score >= 0.78 else "medium" if score >= 0.55 else "low"
    data_quality: str = "high" if coverage >= 0.8 else "medium" if coverage >= 0.4 else "low"
    evidence_state: str = "strong" if coverage >= 0.8 else "mixed" if coverage >= 0.4 else "sparse"

    missing_context = [
        f"{TOOL_LABELS.get(n, n)}:not_connected" for n in missing
    ]
    missing_context += [
        f"{TOOL_LABELS.get(n, n)}:error" for n in errors
    ]

    return TrustMetadata(
        confidence=confidence,  # type: ignore[arg-type]
        confidence_score=score,
        data_quality=data_quality,  # type: ignore[arg-type]
        evidence_state=evidence_state,  # type: ignore[arg-type]
        missing_context=missing_context,
    )


# ---------------------------------------------------------------------------
# Section type classification — backend stamps the type so frontend never guesses
# ---------------------------------------------------------------------------

_SECTION_TYPE_MAP: dict[str, SectionType] = {
    # Morning brief explicit sections
    "Priorities": "priority",
    "Upcoming": "upcoming",
    "Risks": "risk",
    # Common Haiku-generated labels
    "Key Findings": "priority",
    "Key Priorities": "priority",
    "Focus Areas": "priority",
    "Why This Wins": "priority",
    "Recommendation": "priority",
    "Action Items": "action",
    "Recommended Actions": "action",
    "Next Steps": "action",
    "Follow-Ups": "action",
    "Risks And Gaps": "risk",
    "At Risk": "risk",
    "Blockers": "risk",
    "Concerns": "risk",
    "Tradeoffs": "risk",
    "Downsides": "risk",
    "Deadlines": "upcoming",
    "This Week": "upcoming",
    "Upcoming Meetings": "upcoming",
}


def _classify_section(label: str) -> SectionType:
    return _SECTION_TYPE_MAP.get(label, "detail")


# ---------------------------------------------------------------------------
# Core formatter
# ---------------------------------------------------------------------------

class ResponseFormatter:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic()

    def format(
        self,
        text: str,
        tools_called: list[str],
        tool_results: dict[str, str] | None = None,
    ) -> tuple[AnswerPayload, TrustMetadata, str]:
        tr = tool_results or {}
        trust = _compute_trust(tools_called, tr)
        response_type = _detect_response_type(tools_called)
        logger.info("formatter: tools=%s type=%s text_len=%d", tools_called, response_type, len(text))

        # Short or low-value responses — skip extraction
        if response_type in _SKIP_FORMAT_TYPES or len(text) < _MIN_FORMAT_LENGTH:
            fallback_title = _quick_title(text, response_type)
            if response_type == "morning_brief":
                fallback_title = "Morning Brief"
            fallback_summary = text.strip() or ("Working on your request." if response_type == "morning_brief" else text)
            return AnswerPayload(
                title=fallback_title,
                summary=fallback_summary,
                sections=[],
            ), trust, response_type

        extracted = self._extract(text, response_type)
        if not extracted:
            fallback_title = _quick_title(text, response_type)
            if response_type == "morning_brief":
                fallback_title = "Morning Brief"
            fallback_summary = text.strip() or ("Working on your request." if response_type == "morning_brief" else text)
            return AnswerPayload(title=fallback_title, summary=fallback_summary, sections=[]), trust, response_type

        def _str_items(raw: Any) -> list[str]:
            """Coerce items to strings — Haiku sometimes returns dicts."""
            if not raw:
                return []
            return [i if isinstance(i, str) else json.dumps(i) for i in raw if i]

        follow_ups: list[FollowUpChip] = []

        # Morning brief uses explicit named fields instead of generic sections
        if response_type == "morning_brief":
            sections = []
            priorities = _str_items(extracted.get("priorities"))
            if priorities:
                sections.append(AnswerSection(label="Priorities", section_type="priority", content="", items=priorities))
            upcoming = _str_items(extracted.get("upcoming"))
            if upcoming:
                sections.append(AnswerSection(label="Upcoming", section_type="upcoming", content="", items=upcoming))
            risks = _str_items(extracted.get("risks"))
            if risks:
                sections.append(AnswerSection(label="Risks", section_type="risk", content="", items=risks))
            follow_ups = [
                FollowUpChip(label=str(f.get("label", "")), prompt=str(f.get("prompt", "")))
                for f in (extracted.get("follow_ups") or [])
                if isinstance(f, dict) and f.get("label") and f.get("prompt")
            ]
        else:
            sections = [
                AnswerSection(
                    label=s.get("label") or s.get("heading") or "",
                    content=s.get("content") or s.get("body") or "",
                    items=_str_items(s.get("items")),
                    section_type=_classify_section(s.get("label") or s.get("heading") or ""),
                )
                for s in (extracted.get("sections") or [])
                if s.get("label") or s.get("heading")
            ]
            # Append action items as a dedicated section if present
            action_items = _str_items(extracted.get("action_items"))
            if action_items:
                sections.append(AnswerSection(label="Action Items", section_type="action", content="", items=action_items))

        chart: ChartSpec | None = None
        if response_type in _CHART_WORTHY_TYPES:
            # For tool-backed responses use tool results; for conversational use the response text itself
            chart_source = tr if (tr and _has_numeric_data(tr)) else ({"text": text} if _has_numeric_data({"text": text}) else None)
            if chart_source:
                chart = self._extract_chart(chart_source, response_type)

        return AnswerPayload(
            title=extracted.get("title") or "",
            summary=extracted.get("one_liner") or text,
            sections=sections,
            chart=chart,
            follow_ups=follow_ups if response_type == "morning_brief" else [],
        ), trust, response_type

    def _extract_chart(
        self, tool_results: dict[str, str], response_type: str
    ) -> ChartSpec | None:
        """Second Haiku call: reads tool result JSON or response text → ChartSpec or None."""
        _PRIORITY = ["crm_deal_context", "get_company_state", "get_recent_signals",
                     "read_email_threads", "read_calendar_events", "get_live_context"]
        # "text" key signals a conversational response — use plain-text prompt
        if "text" in tool_results and len(tool_results) == 1:
            result_text = tool_results["text"]
            prompt = _CHART_PROMPT_TEXT
        else:
            result_text = next(
                (tool_results[name] for name in _PRIORITY if name in tool_results),
                next(iter(tool_results.values()), None),
            )
            prompt = _CHART_PROMPT
        if not result_text:
            return None
        try:
            response = self._client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=512,
                messages=[
                    {
                        "role": "user",
                        "content": prompt + result_text,
                    }
                ],
            )
            raw = response.content[0].text.strip()
            if raw == "null" or not raw:
                return None
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw)
            points = [
                ChartDataPoint(label=str(p["label"]), value=float(p["value"]))
                for p in data.get("data", [])
                if p.get("label") and p.get("value") is not None
            ]
            if len(points) < 2:
                return None
            return ChartSpec(
                type=data.get("type", "bar"),
                title=data.get("title", ""),
                subtitle=data.get("subtitle"),
                data=points,
                value_format=data.get("value_format", "number"),
                color_scheme=data.get("color_scheme", "neutral"),
            )
        except Exception as exc:
            logger.debug("Chart extraction failed: %s", exc)
            return None

    def _extract(self, text: str, response_type: str) -> dict[str, Any] | None:
        prompt = _EXTRACTION_PROMPTS.get(response_type, _EXTRACTION_PROMPTS["conversational"])
        # morning_brief outputs multiple arrays including follow_ups — needs more headroom
        max_tokens = 2048 if response_type == "morning_brief" else 1024
        try:
            response = self._client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=max_tokens,
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
            parsed = json.loads(raw)
            logger.info("formatter extraction ok (%s): keys=%s", response_type, list(parsed.keys()) if isinstance(parsed, dict) else type(parsed))
            return parsed
        except Exception as exc:
            logger.warning("formatter extraction FAILED (%s) stop_reason=%s: %s",
                           response_type,
                           getattr(response, 'stop_reason', '?') if 'response' in dir() else '?',
                           exc)
            return None


def _has_numeric_data(tool_results: dict[str, str]) -> bool:
    """Quick pre-check: skip chart Haiku call if results contain <3 numeric tokens."""
    import re
    combined = " ".join(tool_results.values())
    return len(re.findall(r'\b\d+(?:[.,]\d+)?(?:[KMB%])?\b', combined)) >= 3


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

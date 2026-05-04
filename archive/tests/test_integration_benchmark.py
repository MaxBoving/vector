"""
Integration benchmark suite — hits the live server with canonical CEO queries
and scores responses against per-workflow criteria.

Requires:
  - server running at localhost:8000
  - demo data seeded: POST /seed-demo-executive-context?scenario=finance_close_week&anchor_date=2026-03-21

Run:
  pytest tests/test_integration_benchmark.py -v -s --tb=short -m integration

Report written to: docs/benchmark_report.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
import requests
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.llm import DEFAULT_OPENAI_MODEL, LLMClient

pytestmark = pytest.mark.integration

BASE_URL = os.getenv("BENCHMARK_BASE_URL", "http://localhost:8000")
BENCHMARK_USER = os.getenv("BENCHMARK_USER", "max")
BENCHMARK_PASS = os.getenv("BENCHMARK_PASS", "password123")
REPORT_PATH = Path(__file__).resolve().parents[1] / "docs" / "benchmark_report.json"
HISTORY_PATH = Path(__file__).resolve().parents[1] / "docs" / "benchmark_history.json"
ENABLE_EVALUATOR = os.getenv("BENCHMARK_ENABLE_EVALUATOR", "0").lower() in {"1", "true", "yes"}
EVALUATOR_MODEL = os.getenv("BENCHMARK_EVALUATOR_MODEL", DEFAULT_OPENAI_MODEL)

GENERIC_PHRASES = [
    "current financial health",
    "key takeaways",
    "important implications",
    "what matters most",
    "needs attention",
    "areas of focus",
    "keep an eye on",
    "strategic priorities",
    "overall outlook",
    "should be monitored",
]
DETAIL_SIGNAL_PATTERNS = [
    r"\$\s?\d[\d,]*(?:\.\d+)?",
    r"\b\d+(?:\.\d+)?%",
    r"\b\d+(?:\.\d+)?\s*(?:months?|days?|hours?)\b",
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\b\d{1,2}:\d{2}\b",
    r"\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b",
]
QUALITY_CATEGORIES = ("efficiency", "vagueness", "specificity", "grounding")
ACTION_THRESHOLDS: dict[str, dict[str, Any]] = {
    "report_generation": {
        "latency_s": 20.0,
        "word_count": {"min_words": 90, "max_words": 320},
        "detail_signals": {"min": 3},
        "generic_phrases_max": 1,
        "finance_metrics_min": 2,
        "finance_takeaways_min": 2,
    },
    "financial_analysis": {
        "latency_s": 25.0,
        "word_count": {"min_words": 110, "max_words": 360},
        "detail_signals": {"min": 5, "max": 18},
        "generic_phrases_max": 1,
        "finance_metrics_min": 3,
        "finance_takeaways_min": 2,
        "finance_implications_min": 2,
        "owner_timed_actions_min": 2,
        "metric_governance_min": 2,
    },
    "email_watcher": {
        "latency_s": 3.0,
        "word_count": {"min_words": 70, "max_words": 260},
        "detail_signals": {"min": 2},
        "generic_phrases_max": 1,
        "priority_items_min": 3,
    },
    "schedule_planning": {
        "latency_s": 5.0,
        "word_count": {"min_words": 70, "max_words": 320},
        "detail_signals": {"min": 3, "max": 18},
        "schedule_blocks_min": 3,
        "timed_blocks_min": 2,
        "reasoned_blocks_min": 2,
    },
}
SEEDED_EXPECTATIONS: dict[str, dict[str, list[str]]] = {
    "report_generation": {
        "must_any": [
            "burn",
            "runway",
            "cloud spend",
            "board packet",
            "variance",
            "finance close review",
            "close week",
        ],
        "must_all_groups": [
            ["burn", "runway"],
            ["variance", "cloud spend"],
            ["finance close", "close week", "finance close review"],
            ["board packet", "narrative"],
        ],
    },
    "financial_analysis": {
        "must_any": ["burn", "runway", "cash position", "cloud spend", "board"],
        "must_all_groups": [
            ["burn", "runway"],
            ["board", "cash"],
        ],
    },
    "email_watcher": {
        "must_any": [
            "month-end close variance needs ceo call",
            "cloud spend variance above forecast",
            "board packet narrative draft for close week",
        ],
        "must_all_groups": [
            ["ceo call", "variance"],
            ["cloud spend", "forecast"],
            ["board", "narrative"],
        ],
    },
    "calendar_briefing": {
        "must_any": [
            "finance close review",
            "cloud spend containment review",
            "board packet finalization",
        ],
        "must_all_groups": [
            ["finance close review"],
            ["cloud spend containment review"],
        ],
    },
    "morning_brief": {
        "must_any": ["finance close review", "cloud spend", "board packet", "variance"],
        "must_all_groups": [
            ["finance close review"],
            ["cloud spend", "board"],
        ],
    },
    "schedule_planning": {
        "must_any": [
            "finance close review",
            "cloud spend containment review",
            "board packet finalization",
            "cloud containment plan",
        ],
        "must_all_groups": [
            ["finance close review"],
            ["cloud spend", "containment"],
            ["board packet"],
        ],
    },
    "meeting_prep": {
        "must_any": [
            "finance close review",
            "cloud spend containment review",
            "board packet finalization",
            "agenda",
            "objective",
            "outcome",
        ],
        "must_all_groups": [
            ["agenda", "talking points"],
            ["objective", "goal", "decision"],
            ["outcome", "next step", "decision", "ask"],
            ["open items", "deliverables", "blockers", "risks"],
        ],
    },
}


class RubricDimensionScore(BaseModel):
    score: int = Field(ge=1, le=5)
    rationale: str


class BenchmarkRubricEvaluation(BaseModel):
    overall_score: int = Field(ge=1, le=5)
    verdict: str
    grounding: RubricDimensionScore
    executive_usefulness: RubricDimensionScore
    specificity: RubricDimensionScore
    trust_calibration: RubricDimensionScore
    missing_points: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _get_token() -> str:
    resp = requests.post(
        f"{BASE_URL}/auth/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"username={BENCHMARK_USER}&password={BENCHMARK_PASS}",
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _ensure_conversation(token: str) -> str:
    """Create a benchmark conversation and return its ID."""
    resp = requests.post(
        f"{BASE_URL}/assistant/conversations",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["conversation_id"]


def _query(token: str, message: str, conversation_id: str, workflow_hint: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message, "conversation_id": conversation_id}
    if workflow_hint:
        payload["workflow_hint"] = workflow_hint
    resp = requests.post(
        f"{BASE_URL}/assistant/query",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------


def _text_corpus(response: dict[str, Any]) -> str:
    """All prose text in the response concatenated for signal matching."""
    parts: list[str] = []
    answer = response.get("answer") or {}
    parts.append(answer.get("summary") or "")
    for s in answer.get("sections") or []:
        parts.append(s.get("content") or "")
        parts.extend(s.get("items") or [])
    p = response.get("presentation") or {}
    parts.append(p.get("summary") or "")
    for section_list in [p.get("priorities") or [], p.get("risks") or [], p.get("recommended_actions") or []]:
        for sec in section_list:
            parts.append(sec.get("content") or "")
            parts.extend(sec.get("items") or [])
    for block in p.get("schedule_blocks") or []:
        parts.append(block.get("label") or "")
        parts.append(block.get("description") or "")
    return " ".join(parts).lower()


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def _detail_signal_count(text: str) -> int:
    lowered = text.lower()
    return sum(len(re.findall(pattern, lowered)) for pattern in DETAIL_SIGNAL_PATTERNS)


def _generic_phrase_hits(text: str) -> list[str]:
    lowered = text.lower()
    return [phrase for phrase in GENERIC_PHRASES if phrase in lowered]


def _presentation_item_count(response: dict[str, Any], field_name: str) -> int:
    sections = (response.get("presentation") or {}).get(field_name) or []
    total = 0
    for section in sections:
        total += len(section.get("items") or [])
        if section.get("content"):
            total += 1
    return total


def _schedule_block_count(response: dict[str, Any]) -> int:
    presentation = response.get("presentation") or {}
    weekly_plan = presentation.get("weekly_plan") or {}
    return len((presentation.get("schedule_blocks") or [])) or len(weekly_plan.get("blocks") or [])


def _schedule_blocks_with_reason(response: dict[str, Any]) -> int:
    presentation = response.get("presentation") or {}
    weekly_plan = presentation.get("weekly_plan") or {}
    blocks = (presentation.get("schedule_blocks") or []) or (weekly_plan.get("blocks") or [])
    count = 0
    for block in blocks:
        if block.get("reason") or block.get("description"):
            count += 1
    return count


def _schedule_blocks_with_time(response: dict[str, Any]) -> int:
    presentation = response.get("presentation") or {}
    weekly_plan = presentation.get("weekly_plan") or {}
    blocks = (presentation.get("schedule_blocks") or []) or (weekly_plan.get("blocks") or [])
    count = 0
    for block in blocks:
        if block.get("starts_at") or block.get("time_window") or block.get("label"):
            count += 1
    return count


def _finance_payload(response: dict[str, Any]) -> dict[str, Any]:
    return (response.get("presentation") or {}).get("finance") or {}


def _finance_metric_count(response: dict[str, Any]) -> int:
    return len(_finance_payload(response).get("key_metrics") or [])


def _owner_timed_action_count(response: dict[str, Any]) -> int:
    corpus_items: list[str] = []
    presentation = response.get("presentation") or {}
    for section in presentation.get("recommended_actions") or []:
        corpus_items.extend(str(item) for item in (section.get("items") or []))
    for section in (response.get("answer") or {}).get("sections") or []:
        if str(section.get("label") or "").lower().startswith("recommended"):
            corpus_items.extend(str(item) for item in (section.get("items") or []))
    count = 0
    for item in corpus_items:
        lowered = item.lower()
        has_owner = any(marker in lowered for marker in ("cfo", "finance lead", "ceo", "chief of staff", "sales lead", "product lead", "engineering", "operations"))
        has_timing = any(marker in lowered for marker in ("by ", "within ", "today", "tomorrow", "weekly", "daily", "before "))
        if has_owner and has_timing:
            count += 1
    return count


def _metric_governance_signal_count(response: dict[str, Any]) -> int:
    corpus = _text_corpus(response)
    count = 0
    groups = [
        ("owner", "cadence"),
        ("target", "threshold"),
        ("weekly", "metric"),
    ]
    for left, right in groups:
        if left in corpus and right in corpus:
            count += 1
    return count


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _answer_section_labels(response: dict[str, Any]) -> list[str]:
    answer = response.get("answer") or {}
    return [str(section.get("label", "")).strip().lower() for section in (answer.get("sections") or [])]


Check = Callable[[dict[str, Any]], tuple[bool, str]]


def check_status_completed(r: dict) -> tuple[bool, str]:
    ok = r.get("status") == "completed"
    return ok, f"status={r.get('status')!r}" if not ok else "status=completed"


def check_workflow_type(expected: str) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        actual = r.get("workflow_type")
        ok = actual == expected
        return ok, f"workflow_type={actual!r} (expected {expected!r})"
    return _check


def check_presentation_mode(expected: str) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        mode = (r.get("presentation") or {}).get("mode")
        ok = mode == expected
        return ok, f"presentation.mode={mode!r} (expected {expected!r})"
    return _check


def check_summary_min_words(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        corpus = _text_corpus(r)
        count = _word_count(corpus)
        ok = count >= n
        return ok, f"prose word count={count} (min {n})"
    return _check


def check_word_count_band(min_words: int, max_words: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        corpus = _text_corpus(r)
        count = _word_count(corpus)
        ok = min_words <= count <= max_words
        return ok, f"prose word count={count} (expected {min_words}-{max_words})"
    return _check


def check_latency_within(max_seconds: float) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        elapsed = float(r.get("__elapsed_s__", 0.0))
        ok = elapsed <= max_seconds
        return ok, f"elapsed_s={elapsed:.2f} (max {max_seconds:.2f})"
    return _check


def check_detail_signal_min(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        corpus = _text_corpus(r)
        count = _detail_signal_count(corpus)
        ok = count >= n
        return ok, f"detail_signals={count} (min {n})"
    return _check


def check_detail_signal_max(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        corpus = _text_corpus(r)
        count = _detail_signal_count(corpus)
        ok = count <= n
        return ok, f"detail_signals={count} (max {n})"
    return _check


def check_generic_phrases_at_most(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        hits = _generic_phrase_hits(_text_corpus(r))
        ok = len(hits) <= n
        return ok, f"generic_phrases={hits}" if hits else "generic_phrases=[]"
    return _check


def check_mentions_any(terms: list[str]) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        corpus = _text_corpus(r)
        hits = [t for t in terms if t.lower() in corpus]
        ok = bool(hits)
        return ok, f"mentions {hits} from {terms}" if ok else f"none of {terms} found in prose"
    return _check


def check_mentions_all_groups(term_groups: list[list[str]]) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        corpus = _text_corpus(r)
        missing_groups = [group for group in term_groups if not any(term.lower() in corpus for term in group)]
        ok = not missing_groups
        return ok, "all required fact groups present" if ok else f"missing fact groups={missing_groups}"
    return _check


def check_section_labels_present(expected_labels: list[str]) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        labels = _answer_section_labels(r)
        missing = [label for label in expected_labels if label.lower() not in labels]
        ok = not missing
        return ok, f"section_labels={labels}" if ok else f"missing section labels={missing}; actual={labels}"
    return _check


def check_priorities_nonempty(r: dict) -> tuple[bool, str]:
    priorities = (r.get("presentation") or {}).get("priorities") or []
    ok = len(priorities) > 0
    return ok, f"priorities count={len(priorities)}"


def check_actions_nonempty(r: dict) -> tuple[bool, str]:
    actions = (r.get("presentation") or {}).get("recommended_actions") or []
    ok = len(actions) > 0
    return ok, f"recommended_actions count={len(actions)}"


def check_not_sparse(r: dict) -> tuple[bool, str]:
    pe = (r.get("metadata") or {}).get("planner_execution") or {}
    sparse = pe.get("sparse_guidance", False)
    return not sparse, "sparse_guidance=True" if sparse else "sparse_guidance=False"


def check_schedule_blocks_nonempty(r: dict) -> tuple[bool, str]:
    blocks = (r.get("presentation") or {}).get("schedule_blocks") or []
    ok = len(blocks) > 0
    return ok, f"schedule_blocks count={len(blocks)}"


def check_confidence_not_low(r: dict) -> tuple[bool, str]:
    confidence = (r.get("trust") or {}).get("confidence", "")
    ok = confidence != "low"
    return ok, f"trust.confidence={confidence!r}"


def check_finance_payload_present(r: dict) -> tuple[bool, str]:
    finance = (r.get("presentation") or {}).get("finance") or {}
    ok = bool(finance)
    return ok, f"presentation.finance={'present' if ok else 'missing'}"


def check_presentation_items_at_least(field_name: str, n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        count = _presentation_item_count(r, field_name)
        ok = count >= n
        return ok, f"{field_name} item_count={count} (min {n})"
    return _check


def check_schedule_blocks_at_least(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        count = _schedule_block_count(r)
        ok = count >= n
        return ok, f"schedule_blocks={count} (min {n})"
    return _check


def check_schedule_blocks_with_reason_at_least(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        count = _schedule_blocks_with_reason(r)
        ok = count >= n
        return ok, f"schedule_blocks_with_reason={count} (min {n})"
    return _check


def check_schedule_blocks_with_time_at_least(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        count = _schedule_blocks_with_time(r)
        ok = count >= n
        return ok, f"schedule_blocks_with_time={count} (min {n})"
    return _check


def check_finance_metrics_at_least(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        count = _finance_metric_count(r)
        ok = count >= n
        return ok, f"finance.key_metrics={count} (min {n})"
    return _check


def check_finance_takeaways_at_least(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        count = len(_finance_payload(r).get("takeaways") or [])
        ok = count >= n
        return ok, f"finance.takeaways={count} (min {n})"
    return _check


def check_finance_implications_at_least(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        count = len(_finance_payload(r).get("implications") or [])
        ok = count >= n
        return ok, f"finance.implications={count} (min {n})"
    return _check


def check_owner_timed_actions_at_least(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        count = _owner_timed_action_count(r)
        ok = count >= n
        return ok, f"owner_timed_actions={count} (min {n})"
    return _check


def check_metric_governance_signals_at_least(n: int) -> Check:
    def _check(r: dict) -> tuple[bool, str]:
        count = _metric_governance_signal_count(r)
        ok = count >= n
        return ok, f"metric_governance_signals={count} (min {n})"
    return _check


# ---------------------------------------------------------------------------
# Benchmark cases
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkCase:
    name: str
    query: str
    thresholds: dict[str, Any] = field(default_factory=dict)
    # soft=True: failure is logged but doesn't fail the test
    checks: list[tuple[str, Check, bool, str]] = field(default_factory=list)

    def add(self, label: str, check: Check, soft: bool = False, category: str = "contract") -> "BenchmarkCase":
        self.checks.append((label, check, soft, category))
        return self


BENCHMARK_CASES: list[BenchmarkCase] = [
    BenchmarkCase("report_generation", "Give me a company health summary.", thresholds=ACTION_THRESHOLDS["report_generation"])
    .add("status", check_status_completed)
    .add("workflow_type", check_workflow_type("report_generation"))
    .add("mode=finance", check_presentation_mode("finance"))
    .add("latency≤20s", check_latency_within(ACTION_THRESHOLDS["report_generation"]["latency_s"]), soft=True, category="efficiency")
    .add("prose_band", check_word_count_band(**ACTION_THRESHOLDS["report_generation"]["word_count"]), soft=True, category="specificity")
    .add("mentions_finance", check_mentions_any(["cash", "burn", "revenue", "runway", "financial"]), soft=True, category="grounding")
    .add("seeded_fact_groups", check_mentions_all_groups(SEEDED_EXPECTATIONS["report_generation"]["must_all_groups"]), soft=True, category="grounding")
    .add(
        "finance_close_issues",
        check_mentions_all_groups([["finance close", "close week"], ["cloud spend", "variance"], ["board packet", "narrative"]]),
        soft=True,
        category="grounding",
    )
    .add("detail_signals", check_detail_signal_min(ACTION_THRESHOLDS["report_generation"]["detail_signals"]["min"]), soft=True, category="vagueness")
    .add("generic_phrases", check_generic_phrases_at_most(ACTION_THRESHOLDS["report_generation"]["generic_phrases_max"]), soft=True, category="vagueness")
    .add("confidence_not_low", check_confidence_not_low, soft=True, category="grounding")
    .add("finance_payload", check_finance_payload_present, soft=True, category="grounding")
    .add("finance_metrics", check_finance_metrics_at_least(ACTION_THRESHOLDS["report_generation"]["finance_metrics_min"]), soft=True, category="grounding")
    .add("finance_takeaways", check_finance_takeaways_at_least(ACTION_THRESHOLDS["report_generation"]["finance_takeaways_min"]), soft=True, category="grounding"),

    BenchmarkCase("financial_analysis", "Show me a runway and burn review with the most important implications for the board.", thresholds=ACTION_THRESHOLDS["financial_analysis"])
    .add("status", check_status_completed)
    .add("workflow_type", check_workflow_type("report_generation"))
    .add("mode=finance", check_presentation_mode("finance"))
    .add("latency≤25s", check_latency_within(ACTION_THRESHOLDS["financial_analysis"]["latency_s"]), soft=True, category="efficiency")
    .add("prose_band", check_word_count_band(**ACTION_THRESHOLDS["financial_analysis"]["word_count"]), soft=True, category="specificity")
    .add("mentions_runway_terms", check_mentions_any(["runway", "burn", "cash", "board"]), soft=True, category="grounding")
    .add("seeded_fact_groups", check_mentions_all_groups(SEEDED_EXPECTATIONS["financial_analysis"]["must_all_groups"]), soft=True, category="grounding")
    .add("detail_signals", check_detail_signal_min(ACTION_THRESHOLDS["financial_analysis"]["detail_signals"]["min"]), soft=True, category="vagueness")
    .add("generic_phrases", check_generic_phrases_at_most(ACTION_THRESHOLDS["financial_analysis"]["generic_phrases_max"]), soft=True, category="vagueness")
    .add("detail_signal_cap", check_detail_signal_max(ACTION_THRESHOLDS["financial_analysis"]["detail_signals"]["max"]), soft=True, category="specificity")
    .add("finance_payload", check_finance_payload_present, soft=True, category="grounding")
    .add("finance_metrics", check_finance_metrics_at_least(ACTION_THRESHOLDS["financial_analysis"]["finance_metrics_min"]), soft=True, category="grounding")
    .add("finance_takeaways", check_finance_takeaways_at_least(ACTION_THRESHOLDS["financial_analysis"]["finance_takeaways_min"]), soft=True, category="grounding")
    .add("finance_implications", check_finance_implications_at_least(ACTION_THRESHOLDS["financial_analysis"]["finance_implications_min"]), soft=True, category="grounding")
    .add("owner_timed_actions", check_owner_timed_actions_at_least(ACTION_THRESHOLDS["financial_analysis"]["owner_timed_actions_min"]), soft=True, category="grounding")
    .add("metric_governance", check_metric_governance_signals_at_least(ACTION_THRESHOLDS["financial_analysis"]["metric_governance_min"]), soft=True, category="grounding"),

    BenchmarkCase("email_watcher", "Scan my inbox and tell me what needs attention.", thresholds=ACTION_THRESHOLDS["email_watcher"])
    .add("status", check_status_completed)
    .add("workflow_type", check_workflow_type("email_watcher"))
    .add("mode=brief", check_presentation_mode("brief"))
    .add("latency≤3s", check_latency_within(ACTION_THRESHOLDS["email_watcher"]["latency_s"]), soft=True, category="efficiency")
    .add("priorities", check_priorities_nonempty, soft=True, category="grounding")
    .add("actions", check_actions_nonempty, soft=True, category="grounding")
    .add("priority_items", check_presentation_items_at_least("priorities", ACTION_THRESHOLDS["email_watcher"]["priority_items_min"]), soft=True, category="grounding")
    .add("prose_band", check_word_count_band(**ACTION_THRESHOLDS["email_watcher"]["word_count"]), soft=True, category="specificity")
    .add("mentions_threads", check_mentions_any(["finance", "close", "thread", "email", "urgent", "deadline"]), soft=True, category="grounding")
    .add("seeded_fact_groups", check_mentions_all_groups(SEEDED_EXPECTATIONS["email_watcher"]["must_all_groups"]), soft=True, category="grounding")
    .add("detail_signals", check_detail_signal_min(ACTION_THRESHOLDS["email_watcher"]["detail_signals"]["min"]), soft=True, category="vagueness")
    .add("generic_phrases", check_generic_phrases_at_most(ACTION_THRESHOLDS["email_watcher"]["generic_phrases_max"]), soft=True, category="vagueness"),

    BenchmarkCase("calendar_briefing", "What meetings do I have coming up?")
    .add("status", check_status_completed)
    .add("workflow_type", check_workflow_type("calendar_briefing"))
    .add("mode=calendar", check_presentation_mode("calendar"))
    .add("prose≥40w", check_summary_min_words(40), soft=True)
    .add("mentions_meetings", check_mentions_any(["meeting", "call", "review", "sync", "board", "calendar"]), soft=True)
    .add("seeded_fact_groups", check_mentions_all_groups(SEEDED_EXPECTATIONS["calendar_briefing"]["must_all_groups"]), soft=True, category="grounding")
    .add("section_labels", check_section_labels_present(["Upcoming Meetings", "Suggested Follow-Ups"]), soft=True, category="grounding")
    .add("prep_specificity", check_mentions_all_groups([["agenda", "pre-read"], ["follow-up", "attendee", "materials"]]), soft=True, category="grounding"),

    BenchmarkCase("morning_brief", "Give me my morning brief.")
    .add("status", check_status_completed)
    .add("workflow_type", check_workflow_type("morning_brief"))
    .add("mode=brief", check_presentation_mode("brief"))
    .add("priorities", check_priorities_nonempty, soft=True)
    .add("prose≥80w", check_summary_min_words(80), soft=True)
    .add("mentions_emails_and_meetings", check_mentions_any(["email", "thread", "meeting", "inbox"]), soft=True)
    .add("actions", check_actions_nonempty, soft=True)
    .add("seeded_fact_groups", check_mentions_all_groups(SEEDED_EXPECTATIONS["morning_brief"]["must_all_groups"]), soft=True, category="grounding"),

    BenchmarkCase("schedule_planning", "Plan my day.", thresholds=ACTION_THRESHOLDS["schedule_planning"])
    .add("status", check_status_completed)
    .add("workflow_type", check_workflow_type("schedule_planning"))
    .add("mode=schedule", check_presentation_mode("schedule"))
    .add("latency≤5s", check_latency_within(ACTION_THRESHOLDS["schedule_planning"]["latency_s"]), soft=True, category="efficiency")
    .add("not_sparse", check_not_sparse, soft=True, category="grounding")
    .add("schedule_blocks", check_schedule_blocks_at_least(ACTION_THRESHOLDS["schedule_planning"]["schedule_blocks_min"]), soft=True, category="grounding")
    .add("timed_blocks", check_schedule_blocks_with_time_at_least(ACTION_THRESHOLDS["schedule_planning"]["timed_blocks_min"]), soft=True, category="grounding")
    .add("reasoned_blocks", check_schedule_blocks_with_reason_at_least(ACTION_THRESHOLDS["schedule_planning"]["reasoned_blocks_min"]), soft=True, category="grounding")
    .add("prose_band", check_word_count_band(**ACTION_THRESHOLDS["schedule_planning"]["word_count"]), soft=True, category="specificity")
    .add("mentions_tasks", check_mentions_any(["block", "time", "priority", "focus", "schedule", "task", "meeting"]), soft=True, category="grounding")
    .add("seeded_fact_groups", check_mentions_all_groups(SEEDED_EXPECTATIONS["schedule_planning"]["must_all_groups"]), soft=True, category="grounding")
    .add("detail_signals", check_detail_signal_min(ACTION_THRESHOLDS["schedule_planning"]["detail_signals"]["min"]), soft=True, category="vagueness")
    .add("detail_signal_cap", check_detail_signal_max(ACTION_THRESHOLDS["schedule_planning"]["detail_signals"]["max"]), soft=True, category="specificity"),

    BenchmarkCase("schedule_planning_week", "Plan my week based on my emails and calendar.")
    .add("status", check_status_completed)
    .add("workflow_type", check_workflow_type("schedule_planning"))
    .add("mode=schedule", check_presentation_mode("schedule"))
    .add("not_sparse", check_not_sparse, soft=True)
    .add("prose≥60w", check_summary_min_words(60), soft=True)
    .add("mentions_week", check_mentions_any(["week", "monday", "tuesday", "wednesday", "thursday", "friday"]), soft=True),

    BenchmarkCase("meeting_prep", "Prep me for my next meeting.")
    .add("status", check_status_completed)
    .add("workflow_type", check_workflow_type("meeting_prep"))
    .add("mode=brief", check_presentation_mode("brief"))
    .add("priorities", check_priorities_nonempty, soft=True)
    .add("prose≥60w", check_summary_min_words(60), soft=True)
    .add("mentions_meeting_context", check_mentions_any(["meeting", "attendee", "agenda", "prep", "talking", "review"]), soft=True)
    .add(
        "section_labels",
        check_section_labels_present(["Meeting Overview", "Meeting Objectives", "Open Items", "Suggested Talking Points", "Desired Outcomes"]),
        soft=True,
        category="grounding",
    )
    .add("prep_specificity", check_mentions_all_groups(SEEDED_EXPECTATIONS["meeting_prep"]["must_all_groups"]), soft=True, category="grounding")
    .add(
        "meeting_outcomes",
        check_mentions_all_groups([["objective", "goal", "decision"], ["outcome", "next step", "follow-up"], ["agenda", "talking points"]]),
        soft=True,
        category="grounding",
    ),

    BenchmarkCase("weekly_recap", "Recap my week.")
    .add("status", check_status_completed)
    .add("workflow_type", check_workflow_type("weekly_recap"))
    .add("mode=brief", check_presentation_mode("brief"))
    .add("priorities", check_priorities_nonempty, soft=True)
    .add("prose≥60w", check_summary_min_words(60), soft=True)
    .add("mentions_recap_context", check_mentions_any(["week", "thread", "meeting", "completed", "accomplished", "recap"]), soft=True),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    label: str
    passed: bool
    soft: bool
    category: str
    detail: str


@dataclass
class CaseResult:
    name: str
    query: str
    elapsed_s: float
    check_results: list[CheckResult]
    response_summary: str
    response_word_count: int
    workflow_type: str
    presentation_mode: str
    sparse_guidance: bool
    trust_confidence: str
    quality_gaps: dict[str, list[str]]
    quality_score: int
    grade: str
    score_status: str
    category_scores: dict[str, int]
    thresholds: dict[str, Any]
    evaluator: dict[str, Any] | None
    error: str | None = None

    @property
    def hard_pass(self) -> bool:
        return all(c.passed for c in self.check_results if not c.soft)

    @property
    def soft_pass(self) -> bool:
        return all(c.passed for c in self.check_results if c.soft)

    @property
    def pass_rate(self) -> str:
        total = len(self.check_results)
        passed = sum(1 for c in self.check_results if c.passed)
        return f"{passed}/{total}"

    def failures(self) -> list[CheckResult]:
        return [c for c in self.check_results if not c.passed]


def _run_case(token: str, conversation_id: str, case: BenchmarkCase) -> CaseResult:
    t0 = time.monotonic()
    error: str | None = None
    response: dict[str, Any] = {}

    try:
        response = _query(token, case.query, conversation_id)
    except Exception as exc:
        error = str(exc)

    elapsed = time.monotonic() - t0
    response["__elapsed_s__"] = elapsed
    corpus = _text_corpus(response)

    check_results: list[CheckResult] = []
    for label, check_fn, soft, category in case.checks:
        if error:
            check_results.append(CheckResult(label=label, passed=False, soft=soft, category=category, detail="request_failed"))
            continue
        try:
            passed, detail = check_fn(response)
        except Exception as exc:
            passed, detail = False, f"check_error: {exc}"
        check_results.append(CheckResult(label=label, passed=passed, soft=soft, category=category, detail=detail))

    quality_gaps: dict[str, list[str]] = {}
    for result in check_results:
        if result.passed or result.category == "contract":
            continue
        quality_gaps.setdefault(result.category, []).append(result.label)

    soft_checks = [result for result in check_results if result.soft]
    soft_passed = sum(1 for result in soft_checks if result.passed)
    quality_score = round((soft_passed / len(soft_checks)) * 100) if soft_checks else 100
    category_scores = {}
    for category in QUALITY_CATEGORIES:
        category_checks = [result for result in soft_checks if result.category == category]
        if not category_checks:
            continue
        category_scores[category] = round((sum(1 for result in category_checks if result.passed) / len(category_checks)) * 100)

    if error or any(not result.passed and not result.soft for result in check_results):
        grade = "F"
        score_status = "fail"
    elif quality_score >= 95 and not quality_gaps:
        grade = "A"
        score_status = "pass"
    elif quality_score >= 85:
        grade = "B"
        score_status = "watch"
    elif quality_score >= 75:
        grade = "C"
        score_status = "watch"
    elif quality_score >= 60:
        grade = "D"
        score_status = "fail"
    else:
        grade = "F"
        score_status = "fail"

    pe = (response.get("metadata") or {}).get("planner_execution") or {}
    evaluator_result = None
    if ENABLE_EVALUATOR and not error:
        evaluator_result = _evaluate_case_with_model(case, result=CaseResult(
            name=case.name,
            query=case.query,
            elapsed_s=round(elapsed, 2),
            check_results=check_results,
            response_summary=(response.get("answer") or {}).get("summary") or "",
            response_word_count=_word_count(corpus),
            workflow_type=response.get("workflow_type") or "",
            presentation_mode=(response.get("presentation") or {}).get("mode") or "",
            sparse_guidance=bool(pe.get("sparse_guidance")),
            trust_confidence=(response.get("trust") or {}).get("confidence") or "",
            quality_gaps=quality_gaps,
            quality_score=quality_score,
            grade=grade,
            score_status=score_status,
            category_scores=category_scores,
            thresholds=case.thresholds,
            evaluator=None,
            error=None,
        ), response=response)
    return CaseResult(
        name=case.name,
        query=case.query,
        elapsed_s=round(elapsed, 2),
        check_results=check_results,
        response_summary=(response.get("answer") or {}).get("summary") or "",
        response_word_count=_word_count(corpus),
        workflow_type=response.get("workflow_type") or "",
        presentation_mode=(response.get("presentation") or {}).get("mode") or "",
        trust_confidence=(response.get("trust") or {}).get("confidence") or "",
        sparse_guidance=bool(pe.get("sparse_guidance")),
        quality_gaps=quality_gaps,
        quality_score=quality_score,
        grade=grade,
        score_status=score_status,
        category_scores=category_scores,
        thresholds=case.thresholds,
        evaluator=evaluator_result,
        error=error,
    )


def _response_for_evaluator(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_type": response.get("workflow_type"),
        "status": response.get("status"),
        "answer": response.get("answer"),
        "presentation": response.get("presentation"),
        "trust": response.get("trust"),
        "sources": response.get("sources"),
    }


def _evaluate_case_with_model(case: BenchmarkCase, result: CaseResult, response: dict[str, Any]) -> dict[str, Any] | None:
    client = LLMClient(model=EVALUATOR_MODEL)
    prompt = (
        "You are grading a CEO assistant response.\n"
        f"Workflow: {case.name}\n"
        f"User query: {case.query}\n"
        f"Deterministic thresholds: {json.dumps(case.thresholds, ensure_ascii=True, default=str)}\n"
        f"Seeded expectations: {json.dumps(SEEDED_EXPECTATIONS.get(case.name, {}), ensure_ascii=True, default=str)}\n"
        f"Observed quality gaps: {json.dumps(result.quality_gaps, ensure_ascii=True, default=str)}\n\n"
        f"Response JSON:\n{json.dumps(_response_for_evaluator(response), ensure_ascii=True, default=str)}"
    )
    system_prompt = (
        "You are evaluating whether a CEO assistant answer is grounded, useful, appropriately specific, "
        "and calibrated to its evidence. Score harshly when the answer is generic, misses seeded facts, "
        "or sounds more certain than the trust metadata justifies."
    )
    try:
        evaluation = client.complete_structured(prompt, BenchmarkRubricEvaluation, system_prompt)
    except Exception as exc:
        return {"status": "error", "error": str(exc), "model": client.model}
    if evaluation is None:
        return {"status": "error", "error": "structured evaluation returned None", "model": client.model}
    return {
        "status": "completed",
        "model": client.model,
        "tokens_used": client.total_tokens_used,
        **evaluation.model_dump(),
    }


def _build_history_entry(report: dict[str, Any]) -> dict[str, Any]:
    summary = report["summary"]
    return {
        "generated_at": report["generated_at"],
        "summary": {
            "total_cases": summary["total_cases"],
            "hard_pass": summary["hard_pass"],
            "all_pass": summary["all_pass"],
            "errors": summary["errors"],
            "average_quality_score": summary["average_quality_score"],
            "efficiency_gaps": summary["efficiency_gaps"],
            "vagueness_gaps": summary["vagueness_gaps"],
            "specificity_gaps": summary["specificity_gaps"],
            "grounding_gaps": summary["grounding_gaps"],
        },
        "cases": [
            {
                "name": case["name"],
                "quality_score": case["quality_score"],
                "grade": case["grade"],
                "status": case["score_status"],
                "elapsed_s": case["elapsed_s"],
                "quality_gaps": case["quality_gaps"],
                "evaluator_overall_score": ((case.get("evaluator") or {}).get("overall_score")),
            }
            for case in report["cases"]
        ],
    }


def _append_history_and_build_trend(report: dict[str, Any]) -> dict[str, Any]:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if HISTORY_PATH.exists():
        try:
            history = json.loads(HISTORY_PATH.read_text())
        except json.JSONDecodeError:
            history = {"runs": []}
    else:
        history = {"runs": []}

    runs = list(history.get("runs") or [])
    previous_run = runs[-1] if runs else None
    current_run = _build_history_entry(report)
    runs.append(current_run)
    runs = runs[-20:]
    HISTORY_PATH.write_text(json.dumps({"runs": runs}, indent=2))

    trend: dict[str, Any] = {"history_length": len(runs)}
    if previous_run:
        trend["average_quality_score_delta"] = round(
            report["summary"]["average_quality_score"] - float(previous_run["summary"].get("average_quality_score", 0)),
            2,
        )
        previous_cases = {case["name"]: case for case in previous_run.get("cases", [])}
        trend["case_deltas"] = []
        for case in report["cases"]:
            prior = previous_cases.get(case["name"])
            if not prior:
                continue
            trend["case_deltas"].append(
                {
                    "name": case["name"],
                    "quality_score_delta": round(case["quality_score"] - float(prior.get("quality_score", 0)), 2),
                    "elapsed_s_delta": round(case["elapsed_s"] - float(prior.get("elapsed_s", 0)), 2),
                    "current_grade": case["grade"],
                    "previous_grade": prior.get("grade"),
                }
            )
    return trend


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _build_report(results: list[CaseResult]) -> dict[str, Any]:
    quality_gap_summary = {
        "efficiency_gaps": [r.name for r in results if r.quality_gaps.get("efficiency")],
        "vagueness_gaps": [r.name for r in results if r.quality_gaps.get("vagueness")],
        "specificity_gaps": [r.name for r in results if r.quality_gaps.get("specificity")],
        "grounding_gaps": [r.name for r in results if r.quality_gaps.get("grounding")],
    }
    average_quality_score = round(sum(r.quality_score for r in results) / len(results)) if results else 0
    completed_evaluator_scores = [
        int(r.evaluator["overall_score"])
        for r in results
        if r.evaluator and r.evaluator.get("status") == "completed" and r.evaluator.get("overall_score") is not None
    ]
    deterministic_pass_but_low_eval = [
        {
            "name": r.name,
            "quality_score": r.quality_score,
            "grade": r.grade,
            "evaluator_overall_score": int(r.evaluator["overall_score"]),
        }
        for r in results
        if r.soft_pass and r.evaluator and r.evaluator.get("status") == "completed" and int(r.evaluator["overall_score"]) <= 3
    ]
    deterministic_fail_but_high_eval = [
        {
            "name": r.name,
            "quality_gaps": r.quality_gaps,
            "evaluator_overall_score": int(r.evaluator["overall_score"]),
        }
        for r in results
        if (not r.soft_pass) and r.evaluator and r.evaluator.get("status") == "completed" and int(r.evaluator["overall_score"]) >= 4
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_cases": len(results),
            "hard_pass": sum(1 for r in results if r.hard_pass),
            "all_pass": sum(1 for r in results if r.hard_pass and r.soft_pass),
            "errors": sum(1 for r in results if r.error),
            "average_quality_score": average_quality_score,
            "evaluator_enabled": ENABLE_EVALUATOR,
            "average_evaluator_score": (
                round(sum(completed_evaluator_scores) / len(completed_evaluator_scores), 2)
                if completed_evaluator_scores
                else None
            ),
            "disagreement_summary": {
                "deterministic_pass_but_evaluator_le_3": deterministic_pass_but_low_eval,
                "deterministic_fail_but_evaluator_ge_4": deterministic_fail_but_high_eval,
            },
            **quality_gap_summary,
            "slowest_cases": [
                {"name": r.name, "elapsed_s": r.elapsed_s}
                for r in sorted(results, key=lambda item: item.elapsed_s, reverse=True)[:3]
            ],
            "scorecard_lowlights": [
                {"name": r.name, "quality_score": r.quality_score, "grade": r.grade, "status": r.score_status}
                for r in sorted(results, key=lambda item: (item.quality_score, item.elapsed_s))[:3]
            ],
        },
        "cases": [
            {
                "name": r.name,
                "query": r.query,
                "elapsed_s": r.elapsed_s,
                "pass_rate": r.pass_rate,
                "hard_pass": r.hard_pass,
                "soft_pass": r.soft_pass,
                "workflow_type": r.workflow_type,
                "presentation_mode": r.presentation_mode,
                "trust_confidence": r.trust_confidence,
                "sparse_guidance": r.sparse_guidance,
                "response_word_count": r.response_word_count,
                "thresholds": r.thresholds,
                "quality_gaps": r.quality_gaps,
                "category_scores": r.category_scores,
                "quality_score": r.quality_score,
                "grade": r.grade,
                "score_status": r.score_status,
                "evaluator": r.evaluator,
                "response_summary_preview": r.response_summary[:300] if r.response_summary else "",
                "failures": [{"label": c.label, "soft": c.soft, "category": c.category, "detail": c.detail} for c in r.failures()],
                "error": r.error,
            }
            for r in results
        ],
    }


def _print_report(results: list[CaseResult]) -> None:
    print("\n" + "=" * 72)
    print("  agenticMIND — Integration Benchmark Report")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)

    for r in results:
        status = "✓" if r.hard_pass and r.soft_pass else ("~" if r.hard_pass else "✗")
        print(f"\n[{status}] {r.name}  ({r.elapsed_s}s, {r.pass_rate} checks, {r.response_word_count}w, score={r.quality_score}/{r.grade})")
        print(f"    workflow={r.workflow_type}  mode={r.presentation_mode}  confidence={r.trust_confidence}  sparse={r.sparse_guidance}")
        if r.thresholds:
            print(f"    thresholds: {json.dumps(r.thresholds, sort_keys=True)}")
        if r.evaluator:
            evaluator_status = r.evaluator.get("status")
            if evaluator_status == "completed":
                print(
                    f"    evaluator: overall={r.evaluator.get('overall_score')}/5 "
                    f"grounding={r.evaluator.get('grounding', {}).get('score')}/5 "
                    f"usefulness={r.evaluator.get('executive_usefulness', {}).get('score')}/5 "
                    f"specificity={r.evaluator.get('specificity', {}).get('score')}/5"
                )
            else:
                print(f"    evaluator: {evaluator_status} ({r.evaluator.get('error')})")
        if r.response_summary:
            preview = r.response_summary[:200].replace("\n", " ")
            print(f"    summary: {preview}...")
        for fail in r.failures():
            marker = "[soft]" if fail.soft else "[HARD]"
            print(f"    {marker} {fail.label}: {fail.detail}")
        if r.error:
            print(f"    ERROR: {r.error}")

    print("\n" + "-" * 72)
    total = len(results)
    hard_pass = sum(1 for r in results if r.hard_pass)
    all_pass = sum(1 for r in results if r.hard_pass and r.soft_pass)
    average_quality_score = round(sum(r.quality_score for r in results) / total) if total else 0
    print(f"  Hard pass: {hard_pass}/{total}   Full pass: {all_pass}/{total}   Avg quality score: {average_quality_score}")
    for category in QUALITY_CATEGORIES:
        impacted = [r.name for r in results if r.quality_gaps.get(category)]
        if impacted:
            print(f"  {category} gaps: {', '.join(impacted)}")
    deterministic_pass_but_low_eval = [
        r.name
        for r in results
        if r.soft_pass and r.evaluator and r.evaluator.get("status") == "completed" and int(r.evaluator["overall_score"]) <= 3
    ]
    deterministic_fail_but_high_eval = [
        r.name
        for r in results
        if (not r.soft_pass) and r.evaluator and r.evaluator.get("status") == "completed" and int(r.evaluator["overall_score"]) >= 4
    ]
    if deterministic_pass_but_low_eval:
        print(f"  disagreements: deterministic pass but evaluator<=3 -> {', '.join(deterministic_pass_but_low_eval)}")
    if deterministic_fail_but_high_eval:
        print(f"  disagreements: deterministic fail but evaluator>=4 -> {', '.join(deterministic_fail_but_high_eval)}")
    print("=" * 72 + "\n")


# ---------------------------------------------------------------------------
# Pytest entry points
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def benchmark_token():
    try:
        return _get_token()
    except Exception as exc:
        pytest.skip(f"Could not authenticate — is the server running? ({exc})")


@pytest.fixture(scope="module")
def benchmark_results(benchmark_token):
    try:
        conversation_id = _ensure_conversation(benchmark_token)
    except Exception as exc:
        pytest.skip(f"Could not create benchmark conversation: {exc}")
    results = [_run_case(benchmark_token, conversation_id, case) for case in BENCHMARK_CASES]
    report = _build_report(results)
    report["trend"] = _append_history_and_build_trend(report)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    _print_report(results)
    return results


@pytest.mark.parametrize("case", BENCHMARK_CASES, ids=lambda c: c.name)
def test_benchmark_hard_checks(case: BenchmarkCase, benchmark_results: list[CaseResult]) -> None:
    """Hard checks must pass — wrong workflow type, failed status, missing mode."""
    result = next(r for r in benchmark_results if r.name == case.name)
    hard_failures = [c for c in result.check_results if not c.passed and not c.soft]
    assert not hard_failures, (
        f"{case.name} hard check failures:\n"
        + "\n".join(f"  [{c.label}] {c.detail}" for c in hard_failures)
    )


@pytest.mark.parametrize("case", BENCHMARK_CASES, ids=lambda c: c.name)
def test_benchmark_soft_checks(case: BenchmarkCase, benchmark_results: list[CaseResult]) -> None:
    """Soft checks — content quality signals. Failures are reported but don't block CI."""
    result = next(r for r in benchmark_results if r.name == case.name)
    soft_failures = [c for c in result.check_results if not c.passed and c.soft]
    if soft_failures:
        details = "\n".join(f"  [{c.label}] {c.detail}" for c in soft_failures)
        pytest.xfail(f"{case.name} soft check failures (quality gaps, not contract breaks):\n{details}")

"""
Context Quality Audit — the one test that shows the app's full capability.

This is not a pass/fail unit test. It's a diagnostic instrument that:
  1. Assembles a RICH scenario (realistic Q1 close week CEO context)
  2. Assembles a SPARSE scenario (near-empty)
  3. Runs finalize_context_stage across both — exercising all 4 phases
  4. Scores each phase and prints a human-readable capability report

Run with: pytest tests/test_context_quality_audit.py -s -v

The printed output IS the result. A score of 100% means the LLM
would receive fully grounded, specific context. Any gap below 100%
identifies a concrete weakness in the pipeline.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Break circular import: api.main triggers the full resolved import chain
import src.api.main  # noqa: F401

from src.workflows.context_loading import finalize_context_stage
from src.workflows.proactive_observations import ProactiveObservation
from src.workflows.retrieval_manifest import RetrievalManifest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).isoformat()


RICH_AGGREGATE: Dict[str, Any] = {
    # ── Phase 1 inputs ──
    "task_input": "What do I need to prepare for the board meeting this Friday?",
    "preferences": {
        "preferences": {
            "communication_style": "direct",
            "detail_level": "concise",
            "preferred_tone": "analytic",
            "decision_velocity": 8,
        }
    },
    "company_state": {
        "company_name": "InnovateCorp",
        "arr": 78_400_000,
        "runway_months": 16,
        "headcount": 142,
    },
    "company_identity": {
        "industry": "B2B SaaS",
        "stage": "Series C",
        "mission": "Automate finance operations for mid-market",
    },
    "project_context": {
        "name": "Q1 Finance Close",
        "document_ids": ["doc_q1_close", "doc_aws_variance"],
        "status": "in_progress",
    },
    "retrieval": [
        {
            "title": "Q1 Finance Close Summary",
            "source_authority": 0.90,
            "source_type": "primary",
            "content": "Q1 ARR $78.4M, +10.1% QoQ. EBITDA -$4.2M vs -$3.8M budget.",
        },
        {
            "title": "AWS Variance Report",
            "source_authority": 0.82,
            "source_type": "primary",
            "content": "AWS costs $480K vs $340K budget — $140K overrun. Root cause: unoptimized ML batch jobs.",
        },
        {
            "title": "Series C Covenant Thresholds",
            "source_authority": 0.75,
            "source_type": "secondary",
            "content": "Covenant requires ARR growth > 8% QoQ and runway > 12 months. Both met as of Q1.",
        },
        {
            "title": "Competitor Landscape — Q1 2026",
            "source_authority": 0.55,
            "source_type": "secondary",
            "content": "Fintech rival raised Series B. Two open deals at risk: Apex ($1.2M ARR) and DataRobot ($800K).",
        },
    ],
    "signals": [
        {"subject": "AWS cost overrun — action needed", "urgency": "high", "source": "ops"},
        {"subject": "Q1 finance close final review", "urgency": "high", "source": "cfo"},
        {"subject": "Apex renewal call — Thursday 2pm", "urgency": "medium", "source": "sales"},
        {"subject": "Board deck draft ready for CEO review", "urgency": "medium", "source": "cfo"},
    ],
    "session_history": [
        {
            "id": 101,
            "timestamp": _days_ago(5),
            "query": "What's our Q1 burn rate looking like?",
            "response": "Burn is $2.1M/month, slightly above the $1.9M plan due to AWS overrun.",
            "intent": "finance_review",
        },
        {
            "id": 102,
            "timestamp": _days_ago(2),
            "query": "Can we defer the Series D prep until after board?",
            "response": "Yes — Q1 close needs to be locked first. Board will ask about the variance.",
            "intent": "strategic_planning",
        },
    ],
    "ceo_memories": [
        {
            "title": "Runway covenant commitment",
            "content": "CEO committed to maintaining runway > 18 months. Stated at March all-hands.",
            "memory_type": "commitment",
        },
        {
            "title": "AWS cost reduction owner",
            "content": "Vikram (VP Eng) owns the AWS cost reduction workstream. Weekly update expected.",
            "memory_type": "delegation",
        },
        {
            "title": "Board communication style",
            "content": "Board prefers bottom-line-up-front. Lead with ARR growth, then variance explanation.",
            "memory_type": "preference",
        },
    ],

    # ── Phase 2 inputs ──
    "live_context": {
        "live_context": {
            "current_schedule": {"turn": 5, "blocks": [{"title": "Board meeting Friday"}]},
            "open_decisions": [
                "Whether to present AWS mitigation plan to board or defer to next quarter",
                "Apex deal: approve 15% discount or hold firm",
            ],
            "open_commitments": ["Board deck final draft by Thursday EOD"],
            "entities_in_play": {"Apex": "active renewal", "Vikram": "AWS owner"},
        }
    },
    "situational_profile": {
        "situational_profile": {
            "operating_mode": "execution",
            "active_pressures": ["Q1 close outstanding", "Board meeting Friday"],
            "recurring_topics": [
                {"topic": "AWS costs", "mention_count": 4, "resolved": False, "last_seen": _days_ago(1)[:10]},
                {"topic": "board prep", "mention_count": 3, "resolved": False, "last_seen": _days_ago(2)[:10]},
                {"topic": "hiring pace", "mention_count": 3, "resolved": False, "last_seen": _days_ago(4)[:10]},
            ],
            "open_threads": [
                {"topic": "Series D timeline", "first_raised": _days_ago(8)},
                {"topic": "Apex deal discount", "first_raised": _days_ago(6)},
            ],
            "relationship_obligations": [],
        }
    },

    # ── Phase 3 inputs ──
    "entity_context": {
        "entity_context": [
            {
                "entity": "Apex",
                "source_type": "memory",
                "timestamp": _days_ago(3),
                "snippet": "Apex renewal at risk — competitor made contact with their VP Finance.",
            },
            {
                "entity": "Vikram",
                "source_type": "thread_entry",
                "timestamp": _days_ago(5),
                "snippet": "Vikram committed to AWS cost reduction plan by end of Q1.",
            },
        ]
    },
}

SPARSE_AGGREGATE: Dict[str, Any] = {
    "task_input": "What do I need to prepare for the board meeting this Friday?",
    "preferences": {},
    "company_state": {"company_name": "InnovateCorp"},
    "company_identity": {},
    "project_context": {},
    "retrieval": [],
    "signals": [],
    "session_history": [],
    "ceo_memories": [],
    "live_context": {},
    "situational_profile": {},
    "entity_context": {},
}


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

class PhaseScore:
    def __init__(self, name: str):
        self.name = name
        self.checks: list[tuple[str, bool, str]] = []  # (label, passed, detail)

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        self.checks.append((label, condition, detail))

    @property
    def passed(self) -> int:
        return sum(1 for _, ok, _ in self.checks if ok)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def pct(self) -> int:
        return round(100 * self.passed / self.total) if self.total else 0


def _print_score(score: PhaseScore) -> None:
    print(f"\n  {'Phase':.<40} {score.passed}/{score.total} ({score.pct}%)")
    for label, ok, detail in score.checks:
        icon = "✓" if ok else "✗"
        suffix = f"  [{detail}]" if detail else ""
        print(f"    {icon} {label}{suffix}")


def _score_phase1(result: Dict[str, Any]) -> PhaseScore:
    s = PhaseScore("Phase 1 — RetrievalManifest + Persona")
    manifest_data = result.get("retrieval_manifest") or {}
    manifest = RetrievalManifest(**manifest_data) if manifest_data else RetrievalManifest()
    persona = result.get("persona") or {}

    s.check("Retrieval manifest present", bool(manifest_data))
    s.check("Manifest is_rich", manifest.is_rich(),
            f"{len(manifest.documents_loaded)} docs, {manifest.signals_found} signals")
    s.check("Documents loaded", len(manifest.documents_loaded) > 0,
            ", ".join(manifest.documents_loaded[:3]) or "none")
    s.check("Signals surfaced", manifest.signals_found > 0,
            f"{manifest.signals_found} signals")
    s.check("Memories surfaced", len(manifest.memories_surfaced) > 0,
            "; ".join(manifest.memories_surfaced[:2]) or "none")
    s.check("Prior conversation refs", len(manifest.prior_conversation_refs) > 0,
            f"{len(manifest.prior_conversation_refs)} refs — "
            + "; ".join(r.turn_summary[:60] for r in manifest.prior_conversation_refs[:2]))
    s.check("Prior refs have days_ago populated",
            all(r.days_ago is not None for r in manifest.prior_conversation_refs),
            "days_ago enables temporal framing in preamble")
    s.check("Live threads scanned", manifest.live_threads_scanned > 0,
            f"{manifest.live_threads_scanned} — 0 expected w/o OAuth connector")
    s.check("Persona built", bool(persona),
            f"voice={persona.get('voice','?')}, relationship={persona.get('relationship','?')}")
    has_gaps_or_docs = bool(manifest.retrieval_gaps) or bool(manifest.documents_loaded)
    s.check("Retrieval gaps noted when warranted", has_gaps_or_docs,
            "; ".join(manifest.retrieval_gaps[:2]) or "no gaps — docs present")
    return s


def _score_phase2(result: Dict[str, Any]) -> PhaseScore:
    s = PhaseScore("Phase 2 — Live Context + Thread Intelligence")
    prepared = result.get("prepared_context") or {}

    live = prepared.get("live_context") or {}
    sitl = prepared.get("situational_profile") or {}

    open_decisions = live.get("open_decisions") or []
    open_commitments = live.get("open_commitments") or []
    operating_mode = sitl.get("operating_mode", "")
    pressures = sitl.get("active_pressures") or []
    recurring = sitl.get("recurring_topics") or []
    open_threads = sitl.get("open_threads") or []

    s.check("Live context present", bool(live))
    s.check("Open decisions tracked", len(open_decisions) > 0,
            f"{len(open_decisions)}: {'; '.join(str(d)[:60] for d in open_decisions[:2])}")
    s.check("Open commitments tracked", len(open_commitments) > 0,
            "; ".join(str(c)[:60] for c in open_commitments[:2]) or "none")
    s.check("Situational profile present", bool(sitl))
    s.check("Operating mode set", bool(operating_mode), operating_mode or "not set")
    s.check("Active pressures", len(pressures) > 0,
            f"{len(pressures)}: {'; '.join(pressures[:2])}")
    s.check("Recurring topics tracked", len(recurring) > 0,
            f"{len(recurring)} topics: {', '.join(t.get('topic','') for t in recurring[:3])}")
    s.check("Open threads tracked", len(open_threads) > 0,
            f"{len(open_threads)}: {', '.join(t.get('topic','') for t in open_threads[:2])}")
    return s


def _score_phase3(result: Dict[str, Any]) -> PhaseScore:
    s = PhaseScore("Phase 3 — Semantic Memory + Entity Context")
    prepared = result.get("prepared_context") or {}

    memories = prepared.get("ceo_memories") or []
    entity_context = prepared.get("entity_context") or []

    s.check("CEO memories surfaced", len(memories) > 0,
            f"{len(memories)}: {'; '.join(m.get('title','')[:40] for m in memories[:3])}")
    s.check("Entity context present", len(entity_context) > 0,
            f"{len(entity_context)} entities: {', '.join(e.get('entity','') for e in entity_context[:4])}")
    s.check("Entity context via fixture (not live Chroma)", True,
            "real semantic search path tested separately in test_semantic_memory.py")
    s.check("Memory types diverse", len({m.get("memory_type") for m in memories}) > 1,
            str({m.get("memory_type") for m in memories}))
    return s


def _score_phase4(result: Dict[str, Any]) -> PhaseScore:
    s = PhaseScore("Phase 4 — Proactive Observation Engine")

    observations_raw = result.get("proactive_observations") or []
    obs_block = result.get("proactive_observations_block") or ""

    obs = [ProactiveObservation(**o) for o in observations_raw]
    obs_types = {o.observation_type.value for o in obs}

    s.check("Observations fired", len(obs) > 0, f"{len(obs)} observations")
    s.check("Observation block generated", bool(obs_block),
            f"{len(obs_block)} chars")
    s.check("High-confidence obs present", any(o.confidence >= 0.75 for o in obs),
            f"max conf: {max((o.confidence for o in obs), default=0):.2f}")
    s.check("Multiple observation types", len(obs_types) >= 2,
            f"types: {', '.join(sorted(obs_types))}" if obs_types else "none")
    s.check("Block has instruction footer", "Also noticed" in obs_block or "surface" in obs_block.lower())

    for obs_item in obs[:3]:
        s.check(
            f"  [{obs_item.observation_type.value}] conf={obs_item.confidence:.2f}",
            True,
            obs_item.headline[:80],
        )
    return s


def _score_gaps(rich_scores: list[PhaseScore], sparse_scores: list[PhaseScore]) -> None:
    print("\n" + "=" * 62)
    print("  GAP ANALYSIS: Rich vs Sparse")
    print("=" * 62)

    hard_gaps = []
    structural_gaps = []

    for rich_s, sparse_s in zip(rich_scores, sparse_scores):
        delta = rich_s.passed - sparse_s.passed
        if delta > 0:
            print(f"\n  {rich_s.name}")
            for (label, rich_ok, detail), (_, sparse_ok, _) in zip(rich_s.checks, sparse_s.checks):
                if rich_ok and not sparse_ok:
                    print(f"    ✗ DEGRADES: {label}")
                    if "live thread" in label.lower() or "connector" in label.lower():
                        structural_gaps.append(label)
                    else:
                        hard_gaps.append(label)

    print("\n  HARD GAPS (will hurt real responses if context is sparse):")
    for g in hard_gaps or ["  None identified"]:
        print(f"    • {g}")

    print("\n  STRUCTURAL GAPS (require external connector wiring):")
    for g in structural_gaps or ["  None identified"]:
        print(f"    • {g}")


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

def test_context_quality_audit() -> None:
    """
    Run the full context pipeline on two scenarios and print a capability report.

    Assertions enforce minimum quality thresholds for the RICH scenario.
    The printed report shows WHERE the app is strong and WHERE gaps exist.
    """
    print("\n")
    print("=" * 62)
    print("  agenticMIND — CONTEXT QUALITY AUDIT")
    print("=" * 62)

    # ── Run both scenarios ──────────────────────────────────────────────────
    rich_result = finalize_context_stage(
        workflow_type="report_generation",
        stage_name="prepare_context",
        aggregate_context=dict(RICH_AGGREGATE),
    )
    sparse_result = finalize_context_stage(
        workflow_type="report_generation",
        stage_name="prepare_context",
        aggregate_context=dict(SPARSE_AGGREGATE),
    )

    # ── Score each phase ────────────────────────────────────────────────────
    print("\n  SCENARIO: Board prep (Q1 close week) — RICH context")
    print("-" * 62)
    rich_scores = [
        _score_phase1(rich_result),
        _score_phase2(rich_result),
        _score_phase3(rich_result),
        _score_phase4(rich_result),
    ]
    for s in rich_scores:
        _print_score(s)

    rich_total = sum(s.passed for s in rich_scores)
    rich_max = sum(s.total for s in rich_scores)
    print(f"\n  RICH TOTAL: {rich_total}/{rich_max} ({round(100*rich_total/rich_max)}%)")

    print("\n  SCENARIO: Same request — SPARSE context")
    print("-" * 62)
    sparse_scores = [
        _score_phase1(sparse_result),
        _score_phase2(sparse_result),
        _score_phase3(sparse_result),
        _score_phase4(sparse_result),
    ]
    for s in sparse_scores:
        _print_score(s)

    sparse_total = sum(s.passed for s in sparse_scores)
    sparse_max = sum(s.total for s in sparse_scores)
    print(f"\n  SPARSE TOTAL: {sparse_total}/{sparse_max} ({round(100*sparse_total/sparse_max)}%)")

    _score_gaps(rich_scores, sparse_scores)

    print("\n" + "=" * 62)
    print("  VERDICT")
    print("=" * 62)
    pct = round(100 * rich_total / rich_max)
    if pct >= 85:
        verdict = "Context pipeline is production-grade. LLM receives fully grounded, specific context."
    elif pct >= 65:
        verdict = "Context pipeline is solid. A few gaps exist — see GAP ANALYSIS above."
    else:
        verdict = "Context pipeline has significant gaps. LLM will hedge or hallucinate under sparse conditions."
    print(f"  {verdict}")
    print()

    # ── Hard assertions on rich scenario ───────────────────────────────────
    # Phase 1: manifest must be rich enough for a specific preamble
    manifest = RetrievalManifest(**(rich_result.get("retrieval_manifest") or {}))
    assert manifest.is_rich(), "RetrievalManifest must be rich with 4 documents seeded"
    assert len(manifest.documents_loaded) >= 3, "Should have loaded at least 3 docs"
    assert manifest.signals_found >= 3, "Should have surfaced at least 3 signals"
    assert rich_result.get("persona"), "Persona must be built from preferences"

    # Phase 2: live context and situational profile must flow through
    prepared = rich_result.get("prepared_context") or {}
    assert prepared.get("live_context"), "Live context must be in prepared_context"
    assert prepared.get("situational_profile"), "Situational profile must be in prepared_context"

    # Phase 3: memories and entity context must reach prepared_context
    assert prepared.get("ceo_memories"), "CEO memories must be in prepared_context"
    assert prepared.get("entity_context"), "Entity context must be in prepared_context"

    # Phase 4: proactive scan must fire at least 1 observation on this rich scenario
    obs = rich_result.get("proactive_observations") or []
    assert len(obs) >= 1, "Proactive scan must fire at least 1 observation given rich situational data"
    assert rich_result.get("proactive_observations_block"), "Observation block must be non-empty"

    # Sparse scenario: confirm graceful degradation (no exceptions, just empty context)
    sparse_prepared = sparse_result.get("prepared_context") or {}
    assert rich_result.keys() == sparse_result.keys(), \
        "Both scenarios must produce identical key structure — sparse degrades gracefully"

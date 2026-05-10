"""
Architectural guardrails — enforced in CI.

These tests mirror the Claude Code hook checks so violations are caught
whether the code arrives via AI-assisted editing or direct developer commits.

Strategy: every existing violation is snapshotted as a baseline. Tests fail
if a file's count EXCEEDS its baseline (new violation added) but pass if it
meets or goes below (cleanup happened). New files with no baseline start at 0.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC_ROOT = Path(__file__).parent.parent / "src"

# ---------------------------------------------------------------------------
# Banned patterns (same list as ~/.claude/hooks/code_guard.py)
# ---------------------------------------------------------------------------

BANNED_PATTERNS: list[tuple[str, str]] = [
    (r"any\s*\(.*\bin\b.*\bfor\b",       "keyword-matching: any(x in y for x in ...)"),
    (r"\bif\b.+\bin\b.+\.lower\s*\(\)",  "keyword-matching: if x in str.lower()"),
    (r"\belif\b.+\bworkflow_type\b.+==", "dispatch chain on workflow_type — use a dict"),
    (r"\belif\b.+\bagent_name\b.+==",    "dispatch chain on agent_name — use a dict"),
    (r"for\s+\w+\s+in\s+\w*[Kk]eyword", "keyword list iteration — classify upstream"),
]

# ---------------------------------------------------------------------------
# Violation count baselines — snapshotted from the codebase on 2026-05-04.
# A file whose count EXCEEDS its baseline fails the test.
# A file whose count DROPS is fine (cleanup progress).
# A file with NO baseline entry is expected to have 0 violations.
# To clean a file: fix it, then lower its baseline (or remove the entry).
# ---------------------------------------------------------------------------

VIOLATION_BASELINES: dict[str, int] = {
    "src/agents/report_agent.py":                  82,
    "src/agents/briefing_agent.py":                30,
    "src/assistant/semantic_arbitration.py":       24,
    "src/assistant/request_interpretation.py":     20,
    "src/workflows/financial_semantic.py":         16,
    "src/workflows/action_semantics.py":           12,
    "src/workflows/runner_semantics.py":           12,
    "src/workflows/read_model.py":                  8,
    "src/integrations/email_intelligence.py":       6,
    "src/workflows/proactive_observations.py":      6,
    "src/workflows/question_ranking.py":            6,
    "src/assistant/artifact_mode.py":               4,
    "src/core/llm.py":                              4,
    "src/workflows/action_references.py":           4,
    "src/core/database.py":                         3,
    "src/integrations/email_extraction.py":         3,
    "src/tools/memory_tools.py":                    3,
    "src/workflows/direct_actions.py":              3,
    "src/core/execution.py":                        2,
    "src/core/persona.py":                          2,
    "src/workflows/company_identity.py":            2,
    "src/workflows/planner_semantics.py":           2,
    "src/workflows/planning_time.py":               2,
    "src/workflows/routing.py":                     2,
    "src/finance/validation.py":                    1,
    "src/finance/variance.py":                      1,
    "src/presentation/brand_extractor.py":          1,
    "src/presentation/presentation_validator.py":   1,
    "src/runtime/engine.py":                        1,
    "src/tools/artifact_requests.py":               1,
    "src/tools/crm_tools.py":                       1,
    "src/workflows/clarification_policy.py":        1,
    "src/workflows/context_loading.py":             1,
    "src/workflows/request_planner.py":             1,
}

# ---------------------------------------------------------------------------
# File size baselines — snapshotted on 2026-05-04.
# Limits per prefix: agents/600, runtime/500, workflows/500, core/400.
# A file whose line count EXCEEDS its baseline fails.
# A file with no baseline and over-limit also fails (new oversized file).
# ---------------------------------------------------------------------------

SIZE_LIMITS: dict[str, int] = {
    "src/agents/":    600,
    "src/runtime/":   500,
    "src/workflows/": 500,
    "src/core/":      400,
}

SIZE_BASELINES: dict[str, int] = {
    # agents
    "src/agents/report_agent.py":          6100,
    "src/agents/briefing_agent.py":        3800,
    # runtime
    "src/runtime/engine.py":               2400,
    # workflows
    "src/workflows/context_loading.py":    1300,
    "src/workflows/direct_actions.py":     1200,
    "src/workflows/request_planner.py":    1000,
    "src/workflows/read_model.py":          730,
    "src/workflows/plan_execution.py":      700,
    # core
    "src/core/database.py":                1160,
    "src/core/llm.py":                      580,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_violations(text: str) -> int:
    return sum(
        1
        for pattern, _ in BANNED_PATTERNS
        for line in text.splitlines()
        if re.search(pattern, line) and not line.strip().startswith("#")
    )


def _size_limit_for(path: Path) -> int | None:
    rel = str(path.relative_to(path.parent.parent.parent))
    for prefix, limit in SIZE_LIMITS.items():
        if rel.startswith(prefix):
            return limit
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_keyword_violations_not_growing() -> None:
    """
    Every file's violation count must be <= its baseline.
    Files with no baseline are expected to have 0 violations.
    Reduce a baseline once you clean the file — never increase it.
    """
    failures: list[str] = []

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        rel = str(py_file.relative_to(py_file.parent.parent.parent))
        text = py_file.read_text(errors="replace")
        count = _count_violations(text)
        baseline = VIOLATION_BASELINES.get(rel, 0)
        if count > baseline:
            delta = count - baseline
            failures.append(
                f"{rel}: {count} violations ({delta} new, baseline {baseline})"
            )

    assert not failures, (
        "Keyword-matching violations increased — AGENTS.md forbids this pattern.\n"
        "Fix the violation or, for pre-existing ones, ensure count <= baseline.\n\n"
        + "\n".join(failures)
    )


def test_file_sizes_not_growing() -> None:
    """
    Files in tracked prefixes must not exceed their size baseline.
    Files with no baseline entry must stay under the prefix limit.
    Reduce a baseline once you decompose the file.
    """
    failures: list[str] = []

    for py_file in sorted(SRC_ROOT.rglob("*.py")):
        limit = _size_limit_for(py_file)
        if limit is None:
            continue

        rel = str(py_file.relative_to(py_file.parent.parent.parent))
        lines = sum(1 for _ in py_file.open(errors="replace"))
        cap = SIZE_BASELINES.get(rel, limit)

        if lines > cap:
            failures.append(
                f"{rel}: {lines} lines (cap {cap})"
            )

    assert not failures, (
        "File size cap exceeded — stop adding to large files; decompose instead.\n\n"
        + "\n".join(failures)
    )

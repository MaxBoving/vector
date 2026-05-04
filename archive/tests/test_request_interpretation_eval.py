from __future__ import annotations

import asyncio
from pathlib import Path

from scripts import run_request_interpretation_eval as eval_mod
from src.assistant.request_interpretation import CandidateWorkflow, InterpretationStep, RequestInterpretation
from src.workflows.types import WorkflowType


def test_eval_failure_debug_contains_required_fields(monkeypatch) -> None:
    async def _mock_interpret_case(case, repeats: int, run_id: str):  # noqa: ARG001
        return [
            RequestInterpretation(
                request_id="req-1",
                user_goal="summarize inbox",
                job_to_be_done="summarize inbox",
                mode="single",
                steps=[
                    InterpretationStep(
                        step_id="s1",
                        intent=WorkflowType.EMAIL_WATCHER,
                        kind="watch",
                        requires=["email_read"],
                        approval_required=False,
                    )
                ],
                candidate_workflows=[CandidateWorkflow(name=WorkflowType.EMAIL_WATCHER, confidence=0.88)],
                needs_clarification=False,
                risk_flags=[],
                explanation="watch inbox",
                provenance={
                    "source": "canonical_interpretation_llm",
                    "raw_interpretation": {"candidate_workflows": [{"name": "email_watcher", "confidence": 0.88}]},
                },
            )
        ]

    monkeypatch.setattr(
        eval_mod,
        "_load_eval_pack",
        lambda _path: [
            {
                "id": "case-1",
                "category": "cat",
                "prompt": "help with inbox",
                "expected_primary_workflow": "report_generation",
                "expected_route_family": "REPORT",
                "expected_act": False,
            }
        ],
    )
    monkeypatch.setattr(eval_mod, "_interpret_case", _mock_interpret_case)

    report = asyncio.run(eval_mod.run_eval(Path("unused.jsonl"), repeats=1))
    assert report["failures"], "Expected at least one failure record"
    failure = report["failures"][0]
    assert failure["prompt"] == "help with inbox"
    assert "raw_interpretation" in failure
    assert "normalized_interpretation" in failure
    assert "needs_clarification" in failure
    assert "candidate_workflows" in failure
    assert "selected_workflow" in failure
    assert "expected_workflow" in failure
    assert "expected_route_family" in failure

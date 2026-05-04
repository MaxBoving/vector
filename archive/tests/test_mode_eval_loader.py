from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_mode_evals import load_stored_response


def test_load_stored_response_ignores_canonical_interaction_summary() -> None:
    interaction = SimpleNamespace(
        response=json.dumps(
            {
                "conversation_id": "conv:test",
                "workflow_type": "report_generation",
                "response_type": "report",
                "status": "completed",
                "answer": {"title": "Title", "summary": "Summary"},
                "artifacts": [],
                "trust": {"question_options": []},
                "metadata": {"envelope_version": 2, "semantic_source": "assistant_service"},
            }
        )
    )

    assert load_stored_response(interaction, workflow_run=None) is None


def test_load_stored_response_accepts_legacy_full_envelope() -> None:
    interaction = SimpleNamespace(
        response=json.dumps(
            {
                "conversation_id": "conv:test",
                "message_id": "msg:test",
                "workflow_type": "report_generation",
                "response_type": "report",
                "status": "completed",
                "answer": {"title": "Title", "summary": "Summary", "sections": []},
                "trust": {
                    "confidence": "medium",
                    "confidence_score": 0.5,
                    "assumptions": [],
                    "open_questions": [],
                    "data_quality": "medium",
                    "calculation_used": False,
                    "missing_context": [],
                    "question_options": [],
                },
                "sources": [],
                "artifacts": [],
                "presentation": {"mode": "report"},
                "metadata": {},
            }
        )
    )

    response = load_stored_response(interaction, workflow_run=None)

    assert response is not None
    assert response.message_id == "msg:test"
    assert response.presentation is not None
    assert response.presentation.mode == "report"

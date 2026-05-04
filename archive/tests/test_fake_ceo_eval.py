import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.schemas import (
    AnswerPayload,
    AnswerSection,
    AssistantMessageResponse,
    MessagePresentation,
    PresentationSection,
    TrustMetadata,
)
from scripts.run_fake_ceo_eval import _scenario_leaderboard, analyze_transcript_failures, serialize_response, transcript_for_model


def _response() -> AssistantMessageResponse:
    return AssistantMessageResponse(
        conversation_id="conv_test",
        message_id="msg_test",
        workflow_type="report_generation",
        response_type="report",
        status="completed",
        answer=AnswerPayload(
            title="Executive Brief",
            summary="A concise summary.",
            sections=[
                AnswerSection(label="Recommended Actions", items=["CEO: reply today."]),
            ],
        ),
        trust=TrustMetadata(),
        sources=[],
        artifacts=[],
        presentation=MessagePresentation(
            mode="report",
            recommended_actions=[PresentationSection(title="Recommended Actions", items=["CEO: reply today."])],
        ),
        metadata={},
    )


def test_serialize_response_includes_synthesizer_artifact_excerpt(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.run_fake_ceo_eval.read_stage_artifact",
        lambda interaction_id, ceo_id, stage: "# Executive Summary\n\nBlock 1\n\nBlock 2\n",
    )

    serialized = serialize_response(_response(), interaction_id=12, ceo_id="ceo_test")

    assert "Block 1" in serialized["artifact_excerpt"]
    assert "Artifact:" in serialized["content_excerpt"]


def test_transcript_for_model_carries_artifact_excerpt() -> None:
    transcript = transcript_for_model(
        [
            {
                "turn": 1,
                "user_message": "What matters today?",
                "assistant": {
                    "summary": "A concise summary.",
                    "sections": [],
                    "recommended_actions": [],
                    "content_excerpt": "A concise summary. || Artifact: Block 1",
                    "artifact_excerpt": "Block 1",
                    "workflow_type": "schedule_planning",
                    "presentation_mode": "schedule",
                    "trust": {},
                },
            }
        ]
    )

    assert transcript[0]["assistant_artifact_excerpt"] == "Block 1"


def test_scenario_leaderboard_surfaces_weakest_stable_first() -> None:
    leaderboard = _scenario_leaderboard(
        {
            "per_scenario": {
                "schedule": {
                    "history_count": 2,
                    "rolling_average_overall_score": 4.0,
                    "delta_vs_rolling_average": 1.0,
                    "movement_signal": "material_shift",
                },
                "finance": {
                    "history_count": 2,
                    "rolling_average_overall_score": 3.0,
                    "delta_vs_rolling_average": 0.0,
                    "movement_signal": "likely_noise",
                },
                "email": {
                    "history_count": 1,
                    "rolling_average_overall_score": 3.0,
                    "delta_vs_rolling_average": 0.0,
                    "movement_signal": "baseline",
                },
            }
        }
    )

    assert leaderboard[0]["scenario_name"] == "finance"
    assert leaderboard[0]["stable"] is True
    assert leaderboard[-1]["scenario_name"] == "email"
    assert leaderboard[-1]["stability_label"] == "emerging"


def test_analyze_transcript_failures_flags_shallow_finance_followups() -> None:
    diagnostics = analyze_transcript_failures(
        [
            {
                "turn": 1,
                "user_message": "What metrics should we establish to track success?",
                "assistant": {
                    "workflow_type": "report_generation",
                    "summary": "General strategy summary.",
                    "content_excerpt": "Business implications: improved collaboration can help execution.",
                },
                "simulator_assessment": {"last_answer_satisfaction": 4},
            }
        ]
    )

    flag_names = {flag["flag"] for flag in diagnostics["failure_flags"]}
    assert "shallow_finance_followup" in flag_names

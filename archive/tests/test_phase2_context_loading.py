import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agents.briefing_agent import BriefingAgent  # noqa: F401
from src.workflows.context_loading import (
    EVENT_BRIEFING_CONTEXT_STAGES,
    REPORT_GENERATION_CONTEXT_STAGES,
    build_context_stage_actions,
    prepare_briefing_context,
    prepare_report_context,
)


def test_phase2_context_stages_include_live_thread_and_situational_profile() -> None:
    assert "load_conversation_thread" in EVENT_BRIEFING_CONTEXT_STAGES
    assert "load_situational_profile" in EVENT_BRIEFING_CONTEXT_STAGES
    assert "load_conversation_thread" in REPORT_GENERATION_CONTEXT_STAGES
    assert "load_situational_profile" in REPORT_GENERATION_CONTEXT_STAGES


def test_build_context_stage_actions_loads_live_thread_and_situational_profile() -> None:
    thread_actions = build_context_stage_actions(
        workflow_type="report_generation",
        stage_name="load_conversation_thread",
        task_input="Build slides from the schedule",
        workflow_metadata={},
    )
    situational_actions = build_context_stage_actions(
        workflow_type="report_generation",
        stage_name="load_situational_profile",
        task_input="Build slides from the schedule",
        workflow_metadata={},
    )

    assert thread_actions[0].target == "get_live_context"
    assert situational_actions[0].target == "get_situational_profile"


def test_prepare_contexts_preserve_live_context_and_situational_profile() -> None:
    shared = {
        "live_context": {
            "live_context": {
                "current_schedule": {"turn": 3, "blocks": [{"title": "Board prep"}]},
                "open_decisions": ["Cloud containment option"],
            }
        },
        "situational_profile": {
            "situational_profile": {
                "operating_mode": "execution",
                "active_pressures": ["Board-related pressure raised Mar 30"],
            }
        },
    }

    report_prepared = prepare_report_context(shared)
    briefing_prepared = prepare_briefing_context(shared, attachments=[])

    assert report_prepared.live_context["current_schedule"]["turn"] == 3
    assert briefing_prepared.live_context["open_decisions"] == ["Cloud containment option"]
    assert report_prepared.situational_profile["operating_mode"] == "execution"
    assert briefing_prepared.situational_profile["active_pressures"][0].startswith("Board-related pressure")

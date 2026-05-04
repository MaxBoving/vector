import sys
from pathlib import Path
import json
import asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agents.report_agent import ReportAgent
from src.api.schemas import AnswerPayload, AssistantMessageResponse, AssistantQueryRequest, TrustMetadata
from src.core.database import get_or_create_live_context, init_db
from src.core.models import SessionInteraction, User
from src.tools.base import ToolContext
from src.tools.registry import ToolRegistry
from src.tools.thread_tools import WriteThreadEntryTool
from src.workflows.report_generation import REPORT_GENERATION_WORKFLOW
from src.workflows.read_model import build_assistant_message_response
from src.workflows.runner import AssistantWorkflowRunner


def test_schedule_turn_persists_current_schedule_and_followup_report_prompt_reads_it() -> None:
    init_db()
    ceo_id = "ceo_phase2_handoff"
    conversation_id = "conv:ceo_phase2_handoff:test"

    WriteThreadEntryTool().invoke(
        ToolContext(ceo_id=ceo_id, interaction_id=501, metadata={"conversation_id": conversation_id}),
        entry_type="schedule",
        actor="briefing_agent",
        content="Built next week's executive schedule.",
        structured_payload={
            "blocks": [
                {"title": "Board prep", "time_window": "9:00-10:00 AM"},
                {"title": "Investor sync", "time_window": "2:00-2:30 PM"},
            ],
            "meetings": [{"title": "Series D financial model review", "starts_at": "2026-04-03T14:00:00-07:00"}],
            "deadlines": ["Board packet CEO markup by 2026-04-03T12:00:00-07:00."],
        },
        entities=["Board Pack"],
        turn=2,
        workflow_type="schedule_planning",
    )

    live_context = get_or_create_live_context(ceo_id, conversation_id)
    assert live_context.current_schedule is not None
    assert live_context.current_schedule["blocks"][0]["title"] == "Board prep"

    report_agent = ReportAgent(ToolRegistry())
    prompt = report_agent._report_prompt(  # type: ignore[attr-defined]
        task_input="Please turn that schedule into presentation slides in pptx",
        company_state={},
        company_identity={},
        preferences={},
        project_context={},
        session_history=[],
        signals=[],
        retrieval=[],
        live_context=live_context.model_dump(),
        situational_profile={},
    )

    assert "Most recent schedule" in prompt
    assert "Board prep" in prompt
    assert "Investor sync" in prompt
    assert "Series D financial model review" in prompt


def test_report_generation_workflow_executes_live_thread_context_before_synthesis() -> None:
    step_names = [step.name for step in REPORT_GENERATION_WORKFLOW.steps]

    assert "load_conversation_thread" in step_names
    assert "load_situational_profile" in step_names
    assert step_names.index("load_conversation_thread") < step_names.index("prepare_context")
    assert step_names.index("load_situational_profile") < step_names.index("prepare_context")


def test_runner_enriches_deictic_artifact_followup_from_live_context(monkeypatch) -> None:
    runner = AssistantWorkflowRunner(tools=ToolRegistry())
    monkeypatch.setattr(
        "src.workflows.runner.get_or_create_live_context",
        lambda ceo_id, conversation_id: type(
            "LiveContext",
            (),
            {
                "model_dump": lambda self: {
                    "current_schedule": {
                        "turn": 2,
                        "blocks": [{"title": "Board prep", "time_window": "9:00-10:00 AM"}],
                        "meetings": [{"title": "Series D review", "starts_at": "2026-04-03T14:00:00-07:00"}],
                        "deadlines": ["Board packet markup by 2026-04-03T12:00:00-07:00."],
                    },
                    "open_decisions": ["Cloud containment option"],
                    "last_agent_contributions": [{"actor": "briefing_agent", "turn": 2, "content_summary": "Built next week's schedule"}],
                }
            },
        )(),
    )

    payload = AssistantQueryRequest(
        message="Turn that into slides in pptx",
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=10, ceo_id="ceo_test", query=payload.message)
    user = User(username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")

    enriched = runner._maybe_enrich_live_context_followup(  # type: ignore[attr-defined]
        payload=payload,
        interaction=interaction,
        current_user=user,
    )

    assert enriched.workflow_hint == "report_generation"
    assert "Conversation live context" in enriched.message
    assert "Board prep" in enriched.message
    assert "Cloud containment option" in enriched.message


def test_runner_enriches_schedule_conversion_followup_without_deictic(monkeypatch) -> None:
    runner = AssistantWorkflowRunner(tools=ToolRegistry())
    monkeypatch.setattr(
        "src.workflows.runner.get_or_create_live_context",
        lambda ceo_id, conversation_id: type(
            "LiveContext",
            (),
            {
                "model_dump": lambda self: {
                    "current_schedule": {
                        "turn": 2,
                        "blocks": [{"title": "Board prep", "time_window": "9:00-10:00 AM"}],
                        "meetings": [{"title": "Series D review", "starts_at": "2026-04-03T14:00:00-07:00"}],
                        "deadlines": ["Board packet markup by 2026-04-03T12:00:00-07:00."],
                    },
                    "open_decisions": ["Cloud containment option"],
                    "last_agent_contributions": [{"actor": "briefing_agent", "turn": 2, "content_summary": "Built next week's schedule"}],
                }
            },
        )(),
    )

    payload = AssistantQueryRequest(
        message="Make a memo from the schedule",
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=11, ceo_id="ceo_test", query=payload.message)
    user = User(username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")

    enriched = runner._maybe_enrich_live_context_followup(  # type: ignore[attr-defined]
        payload=payload,
        interaction=interaction,
        current_user=user,
    )

    assert enriched.workflow_hint == "report_generation"
    assert "Conversation live context" in enriched.message
    assert "Board prep" in enriched.message
    assert "CEO follow-up: Make a memo from the schedule" in enriched.message


def test_report_prompt_uses_live_thread_followup_mode_for_schedule_conversion() -> None:
    report_agent = ReportAgent(ToolRegistry())
    prompt = report_agent._report_prompt(  # type: ignore[attr-defined]
        task_input="Make a memo from the schedule",
        company_state={},
        company_identity={},
        preferences={},
        project_context={},
        session_history=[{"query": "Plan my week around the board review"}],
        signals=[],
        retrieval=[],
        live_context={
            "current_schedule": {
                "turn": 2,
                "blocks": [{"title": "Board prep", "time_window": "9:00-10:00 AM"}],
                "meetings": [{"title": "Series D review", "starts_at": "2026-04-03T14:00:00-07:00"}],
                "deadlines": ["Board packet markup by 2026-04-03T12:00:00-07:00."],
            },
            "open_decisions": ["Cloud containment option"],
            "open_commitments": ["Board packet markup"],
            "last_agent_contributions": [{"actor": "briefing_agent", "turn": 2, "content_summary": "Built next week's schedule"}],
        },
        situational_profile={},
    )

    assert "=== LIVE THREAD FOLLOW-UP MODE ===" in prompt
    assert "Likely output conversion target: docx" in prompt
    assert "Convert its blocks, meetings, and deadlines into the requested artifact" in prompt
    assert "Keep the open decisions visible" in prompt


def test_read_model_exposes_compact_phase2_context_metadata(monkeypatch) -> None:
    interaction = SessionInteraction(
        id=77,
        ceo_id="ceo_test",
        query="Turn that into slides",
        response='{"conversation_id":"conv_test"}',
        status="COMPLETED",
    )
    user = User(username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")

    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts", lambda _id, _ceo: {})
    monkeypatch.setattr(
        "src.workflows.read_model.get_or_create_live_context",
        lambda ceo_id, conversation_id: type(
            "LiveContext",
            (),
            {
                "turn_count": 4,
                "open_decisions": ["Cloud containment option"],
                "open_commitments": ["Board packet markup"],
                "entities_in_play": {"Board Pack": "Active", "Series D": "Active"},
                "current_schedule": {
                    "turn": 3,
                    "blocks": [{"title": "Board prep"}, {"title": "Investor sync"}],
                    "meetings": [{"title": "Series D review"}],
                    "deadlines": ["Board packet markup by 2026-04-03T12:00:00-07:00."],
                },
                "updated_at": "2026-03-30T09:00:00",
            },
        )(),
    )
    monkeypatch.setattr(
        "src.workflows.read_model.get_or_create_situational_profile",
        lambda ceo_id: type(
            "SituationalProfile",
            (),
            {
                "operating_mode": "execution",
                "active_pressures": ["Board review today"],
                "recurring_topics": [{"topic": "cloud spend"}, {"topic": "Series D"}],
                "open_threads": [{"thread": "Board packet"}, {"thread": "Hiring"}],
                "relationship_obligations": ["Reply to Apex today"],
                "updated_at": "2026-03-30T09:05:00",
            },
        )(),
    )

    response = build_assistant_message_response(interaction, user, conversation_id="conv_test")

    assert response.metadata["live_context"]["turn_count"] == 4
    assert response.metadata["live_context"]["current_schedule"]["block_titles"] == ["Board prep", "Investor sync"]
    assert response.metadata["live_context"]["current_schedule"]["meeting_titles"] == ["Series D review"]
    assert response.metadata["situational_profile"]["operating_mode"] == "execution"


def test_read_model_sanitizes_context_wrapped_query_for_frontend(monkeypatch) -> None:
    interaction = SessionInteraction(
        id=78,
        ceo_id="ceo_test",
        query=(
            "[Context: Prior question: Make me a schedule for next week | Prior response: next week Schedule Proposal]\n\n"
            'Follow-up action: Build a brief on the at-risk initiative "Northstar Health Renewal".'
        ),
        response='{"conversation_id":"conv_test"}',
        status="COMPLETED",
    )
    user = User(username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")

    monkeypatch.setattr("src.workflows.read_model._load_workflow_run", lambda _: None)
    monkeypatch.setattr("src.workflows.read_model.hydrate_stage_artifacts", lambda _id, _ceo: {})
    monkeypatch.setattr(
        "src.workflows.read_model.get_or_create_live_context",
        lambda ceo_id, conversation_id: type(
            "LiveContext",
            (),
            {
                "turn_count": 1,
                "open_decisions": [],
                "open_commitments": [],
                "entities_in_play": {},
                "current_schedule": {},
                "updated_at": "2026-03-30T09:00:00",
            },
        )(),
    )
    monkeypatch.setattr(
        "src.workflows.read_model.get_or_create_situational_profile",
        lambda ceo_id: type(
            "SituationalProfile",
            (),
            {
                "operating_mode": "execution",
                "active_pressures": [],
                "recurring_topics": [],
                "open_threads": [],
                "relationship_obligations": [],
                "updated_at": "2026-03-30T09:05:00",
            },
        )(),
    )

    response = build_assistant_message_response(interaction, user, conversation_id="conv_test")

    assert response.metadata["query"] == 'Build a brief on the at-risk initiative "Northstar Health Renewal".'


def test_runner_keeps_board_packet_correction_on_report_path_despite_live_schedule(monkeypatch) -> None:
    runner = AssistantWorkflowRunner(tools=ToolRegistry())

    previous_response = {
        "workflow_type": "report_generation",
        "response_type": "report",
        "answer": {
            "title": "Q3 2024 Board Packet",
            "summary": "Board packet draft with recommended actions.",
        },
        "artifacts": [
            {"artifact_type": "report_docx", "artifact_id": "interaction:40:report_docx", "label": "Report Docx"},
        ],
    }
    previous_interaction = SessionInteraction(
        id=40,
        ceo_id="ceo_test",
        query="I need the board packet ready by 8 AM tomorrow.",
        response=json.dumps(previous_response),
        status="COMPLETED",
    )

    monkeypatch.setattr(
        "src.workflows.runner.get_previous_conversation_interaction",
        lambda ceo_id, conversation_id, interaction_id: previous_interaction,
    )
    monkeypatch.setattr(
        "src.workflows.runner.get_or_create_live_context",
        lambda ceo_id, conversation_id: type(
            "LiveContext",
            (),
            {
                "model_dump": lambda self, mode=None: {
                    "current_schedule": {
                        "blocks": [{"title": "Board prep", "time_window": "9:00-10:00 AM"}],
                        "meetings": [{"title": "AWS spend review", "starts_at": "2026-04-03T14:00:00-07:00"}],
                    },
                    "open_decisions": ["Hiring freeze framing"],
                    "pending_actions": [],
                    "last_agent_contributions": [],
                }
            },
        )(),
    )
    monkeypatch.setattr("src.workflows.runner.get_ceo_preferences", lambda ceo_id: {})
    monkeypatch.setattr("src.workflows.runner.get_ceo_memories", lambda ceo_id, limit=12: [])
    monkeypatch.setattr("src.workflows.runner.get_recent_signals", lambda ceo_id, limit=6: [])
    monkeypatch.setattr("src.workflows.runner.persist_latest_intent_state", lambda **kwargs: None)
    monkeypatch.setattr("src.workflows.runner.persist_latest_unified_memory", lambda **kwargs: None)
    monkeypatch.setattr("src.workflows.runner.update_live_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_build_conversation_history", lambda **kwargs: [])
    monkeypatch.setattr(runner, "_persist_pending_actions", lambda **kwargs: None)

    async def _no_llm_route(**kwargs):
        return None

    async def _fake_runtime_run(*, definition, payload, interaction, current_user, routing_decision, extra_metadata):
        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=definition.workflow_type,
            response_type="report",
            status="completed",
            answer=AnswerPayload(
                title="Updated Board Packet",
                summary="Revised the board packet directly.",
                sections=[],
            ),
            trust=TrustMetadata(),
            sources=[],
            artifacts=[],
            metadata=extra_metadata,
        )

    monkeypatch.setattr(runner.llm_router, "classify", _no_llm_route)
    monkeypatch.setattr(runner.runtime, "run", _fake_runtime_run)

    payload = AssistantQueryRequest(
        message=(
            "You're still giving me action items instead of doing the work. "
            "Look - I don't want to see 'CEO | Today' in the document. "
            "Fix the board packet and incorporate the AWS spend language."
        ),
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=41, ceo_id="ceo_test", query=payload.message, status="PENDING")
    user = User(username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")

    response = asyncio.run(runner.run(payload, interaction, user))

    assert response.workflow_type == "report_generation"
    assert response.answer.title == "Updated Board Packet"
    assert response.metadata["intent_state"]["mode"] in {"revision", "correction"}


def test_runner_keeps_customer_analysis_then_outreach_request_on_report_path(monkeypatch) -> None:
    runner = AssistantWorkflowRunner(tools=ToolRegistry())

    monkeypatch.setattr(
        "src.workflows.runner.get_or_create_live_context",
        lambda ceo_id, conversation_id: type(
            "LiveContext",
            (),
            {
                "model_dump": lambda self, mode=None: {
                    "current_schedule": {},
                    "open_decisions": [],
                    "pending_actions": [],
                    "last_agent_contributions": [],
                }
            },
        )(),
    )
    monkeypatch.setattr("src.workflows.runner.get_ceo_preferences", lambda ceo_id: {})
    monkeypatch.setattr("src.workflows.runner.get_ceo_memories", lambda ceo_id, limit=12: [])
    monkeypatch.setattr("src.workflows.runner.get_recent_signals", lambda ceo_id, limit=6: [])
    monkeypatch.setattr("src.workflows.runner.persist_latest_intent_state", lambda **kwargs: None)
    monkeypatch.setattr("src.workflows.runner.persist_latest_unified_memory", lambda **kwargs: None)
    monkeypatch.setattr("src.workflows.runner.update_live_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_build_conversation_history", lambda **kwargs: [])
    monkeypatch.setattr(runner, "_persist_pending_actions", lambda **kwargs: None)

    async def _no_llm_route(**kwargs):
        return None

    async def _fake_runtime_run(*, definition, payload, interaction, current_user, routing_decision, extra_metadata):
        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=f"msg_{interaction.id}",
            workflow_type=definition.workflow_type,
            response_type="report",
            status="completed",
            answer=AnswerPayload(
                title="Customer Risk Brief",
                summary="Identified the highest-risk customer and prepared the outreach draft.",
                sections=[],
            ),
            trust=TrustMetadata(),
            sources=[],
            artifacts=[],
            metadata=extra_metadata,
        )

    monkeypatch.setattr(runner.llm_router, "classify", _no_llm_route)
    monkeypatch.setattr(runner.runtime, "run", _fake_runtime_run)

    payload = AssistantQueryRequest(
        message=(
            "I need you to identify our highest-risk customer situation right now and draft an immediate outreach email. "
            "Then send it, copy the account owner, and schedule a follow-up call for tomorrow."
        ),
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=52, ceo_id="ceo_test", query=payload.message, status="PENDING")
    user = User(username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")

    response = asyncio.run(runner.run(payload, interaction, user))

    assert response.workflow_type == "report_generation"
    assert response.answer.title == "Customer Risk Brief"

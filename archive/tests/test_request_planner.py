import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.schemas import AnswerPayload, AnswerSection, AssistantMessageResponse, AssistantQueryRequest, TrustMetadata
from src.agents.briefing_agent import BriefingAgent
from src.agents.planner_agent import PlannerAgent
from src.agents import TaskIntent
from src.agents.schemas import AgentOutput
from src.core.models import SessionInteraction, User
from src.integrations.email_intelligence import select_primary_thread
from src.workflows.day_schedule_planning import DAY_SCHEDULE_PLANNING_WORKFLOW
from src.workflows.plan_execution import build_planning_window
from src.assistant.classification import classify_request_intent_async
from src.workflows.request_planner import plan_request
from src.workflows.runner import AssistantWorkflowRunner
from src.assistant.service import AssistantService
from src.workflows.routing import RouteFamily
from src.workflows.types import WorkflowType
from src.runtime.engine import RuntimeEngine
from src.runtime.bootstrap import DEFAULT_STAGE_HANDLER_CONFIG, build_default_stage_handler_registry
from src.runtime.config import RuntimeStageHandlerProvider
from src.runtime.stage_handlers import StageFamily, StageHandlerRegistry
from src.workflows.types import WorkflowStepDefinition
from scripts.run_fake_ceo_eval import analyze_transcript_failures


def test_fake_ceo_transcript_flags_detect_repeated_misroutes() -> None:
    turns = [
        {
            "turn": 1,
            "user_message": "What are the key financial metrics I should review before our finance close meeting?",
            "assistant": {"workflow_type": "calendar_briefing", "summary": "3 meetings on your calendar this week."},
            "simulator_assessment": {"last_answer_satisfaction": 2},
        },
        {
            "turn": 2,
            "user_message": "Can you provide a detailed list of financial metrics and known issues?",
            "assistant": {"workflow_type": "calendar_briefing", "summary": "3 meetings on your calendar this week."},
            "simulator_assessment": {"last_answer_satisfaction": 1},
        },
        {
            "turn": 3,
            "user_message": "I need revenue, expenses, cash flow, and any known issues before finance close.",
            "assistant": {"workflow_type": "calendar_briefing", "summary": "3 meetings on your calendar this week."},
            "simulator_assessment": {"last_answer_satisfaction": 1},
        },
    ]

    diagnostics = analyze_transcript_failures(turns)
    flag_names = {flag["flag"] for flag in diagnostics["failure_flags"]}

    assert "workflow_mismatch" in flag_names
    assert "repeated_workflow_after_dissatisfaction" in flag_names
    assert "repeated_summary_after_dissatisfaction" in flag_names
    assert "persistent_low_satisfaction" in flag_names


def test_next_week_planning_window_uses_next_week_workdays() -> None:
    reference_dt = datetime.fromisoformat("2026-03-17T09:00:00-07:00")

    window = build_planning_window("next_week", reference_dt=reference_dt)

    assert str(window.start_date) == "2026-03-23"
    assert str(window.end_date) == "2026-03-27"


def test_this_week_planning_window_uses_remaining_workdays() -> None:
    reference_dt = datetime.fromisoformat("2026-03-17T09:00:00-07:00")

    window = build_planning_window("this_week", reference_dt=reference_dt)

    assert str(window.start_date) == "2026-03-17"
    assert str(window.end_date) == "2026-03-20"


def test_this_week_planning_window_rolls_to_upcoming_workweek_on_weekend() -> None:
    reference_dt = datetime.fromisoformat("2026-03-29T09:00:00-07:00")

    window = build_planning_window("this_week", reference_dt=reference_dt)

    assert str(window.start_date) == "2026-03-30"
    assert str(window.end_date) == "2026-04-03"


def test_next_week_planning_window_skips_upcoming_workweek_on_weekend() -> None:
    reference_dt = datetime.fromisoformat("2026-03-29T09:00:00-07:00")

    window = build_planning_window("next_week", reference_dt=reference_dt)

    assert str(window.start_date) == "2026-04-06"
    assert str(window.end_date) == "2026-04-10"


def test_simple_document_request_still_selects_document_explanation() -> None:
    runner = AssistantWorkflowRunner()
    service = AssistantService(runner)
    payload = AssistantQueryRequest(
        message="Explain this attached memo.",
        conversation_id="conv_test",
        attachments=[{"document_id": "doc_1", "filename": "memo.pdf"}],
    )
    class _UnusedRouter:
        async def classify(self, **kwargs):
            raise AssertionError("llm router should be skipped for attachment requests")

    _, intent = asyncio.run(classify_request_intent_async(payload, llm_router=_UnusedRouter()))

    from src.agents.schemas import RoutingDecision
    routing_decision = RoutingDecision(
        intent=TaskIntent.FACT_FINDING,
        specialist_required="briefing_agent",
        relevant_state_keys=[],
        requires_approval=False,
        rationale="",
    )
    workflow_type = service._select_workflow_type(payload, intent, routing_decision)

    assert intent.route_family == RouteFamily.REPORT
    assert workflow_type == WorkflowType.DOCUMENT_EXPLANATION


def test_day_schedule_planning_workflow_includes_explicit_planner_runtime_stages() -> None:
    stage_names = [step.name for step in DAY_SCHEDULE_PLANNING_WORKFLOW.steps]
    planner_stage_agents = {
        step.name: step.agent_name
        for step in DAY_SCHEDULE_PLANNING_WORKFLOW.steps
        if step.name in {"gather_email", "gather_calendar", "gather_documents", "build_candidates", "place_schedule", "synthesize_response"}
    }

    assert stage_names == [
        "load_company_state",
        "load_preferences",
        "load_conversation_thread",
        "load_situational_profile",
        "load_session_history",
        "load_signals",
        "retrieve_documents",
        "prepare_context",
        "gather_email",
        "gather_calendar",
        "gather_documents",
        "build_candidates",
        "place_schedule",
        "synthesize_response",
        "synthesizer",
        "complete",
        "failed",
    ]
    assert set(planner_stage_agents.values()) == {"planner_agent"}


def test_runtime_uses_first_class_stage_families() -> None:
    engine = RuntimeEngine()

    assert engine.stage_handlers.classify(WorkflowStepDefinition(name="route", agent_name="router_agent")) == StageFamily.ROUTER
    assert engine.stage_handlers.classify(WorkflowStepDefinition(name="context", metadata={"context_stage": "load_company_state"})) == StageFamily.CONTEXT
    assert engine.stage_handlers.classify(WorkflowStepDefinition(name="planner", agent_name="planner_agent", metadata={"planner_stage": "gather_email"})) == StageFamily.AGENT
    assert engine.stage_handlers.classify(WorkflowStepDefinition(name="noop")) == StageFamily.NOOP


def test_stage_handler_registry_is_reusable_outside_engine() -> None:
    registry = StageHandlerRegistry()
    called: list[str] = []

    async def noop_handler(**kwargs):
        called.append("noop")
        return {"completed_without_agent_output": True}

    registry.register(StageFamily.NOOP, noop_handler)

    assert registry.classify(WorkflowStepDefinition(name="noop")) == StageFamily.NOOP
    handler = registry.handler_for(StageFamily.NOOP)
    result = asyncio.run(handler())

    assert called == ["noop"]
    assert result["completed_without_agent_output"] is True


def test_runtime_bootstrap_builds_default_stage_handler_registry() -> None:
    engine = RuntimeEngine(stage_handlers=StageHandlerRegistry())
    registry = build_default_stage_handler_registry(handlers=engine.runtime_stage_handlers)

    assert registry.handler_for(StageFamily.ROUTER) == engine.runtime_stage_handlers.handle_router_stage
    assert registry.handler_for(StageFamily.CONTEXT) == engine.runtime_stage_handlers.handle_context_stage
    assert registry.handler_for(StageFamily.AGENT) == engine.runtime_stage_handlers.handle_agent_stage
    assert registry.handler_for(StageFamily.NOOP) == engine.runtime_stage_handlers.handle_noop_stage
    assert all(not binding.handler_name.startswith("_") for binding in DEFAULT_STAGE_HANDLER_CONFIG.bindings)


def test_runtime_stage_handler_provider_builds_registry_from_declarative_config() -> None:
    provider = RuntimeStageHandlerProvider()

    class HandlerStub:
        async def handle_router_stage(self, **kwargs):
            return {"family": "router"}

        async def handle_context_stage(self, **kwargs):
            return {"family": "context"}

        async def handle_agent_stage(self, **kwargs):
            return {"family": "agent"}

        async def handle_noop_stage(self, **kwargs):
            return {"family": "noop"}

    handlers = HandlerStub()
    registry = provider.build_registry(handlers=handlers, config=DEFAULT_STAGE_HANDLER_CONFIG)

    assert registry.handler_for(StageFamily.ROUTER) == handlers.handle_router_stage
    assert registry.handler_for(StageFamily.CONTEXT) == handlers.handle_context_stage
    assert registry.handler_for(StageFamily.AGENT) == handlers.handle_agent_stage
    assert registry.handler_for(StageFamily.NOOP) == handlers.handle_noop_stage


def test_planner_agent_emits_dedicated_planning_artifact_action() -> None:
    agent = PlannerAgent()
    route_plan = plan_request("Scan my inbox and generate me a schedule plan for next week.")
    workflow_state = type(
        "WorkflowStateStub",
        (),
        {
            "metadata": {
                "request_plan": route_plan.model_dump(),
                "event_payload": {
                    "email_watch": {"ranked_threads": [], "structured_watch": {"deadlines": [], "asks": []}},
                    "calendar_watch": {"upcoming_events": []},
                    "document_context": {},
                },
                "planner_execution": {},
            },
        },
    )()
    agent_input = type(
        "AgentInputStub",
        (),
        {
            "workflow_state": workflow_state,
            "stage": "gather_email",
            "metadata": {"planner_stage": "gather_email"},
        },
    )()

    output = asyncio.run(agent.run(agent_input))  # type: ignore[arg-type]

    assert output.agent_name == "planner_agent"
    assert any(action.args.get("stage") == "planning" for action in output.actions)
    assert output.metadata["planner_execution"]["executed_plan_steps"][0]["key"] == "gather_email"


def test_promotional_thread_does_not_dominate_primary_selection() -> None:
    ranked_threads = [
        {
            "subject": "Limited time bonus offer",
            "latest_sender": "Deals <promo@studentbonus-mailer.com>",
            "importance_level": "medium",
            "importance_score": 40,
            "suppressed": True,
            "category": "promotional",
        },
        {
            "subject": "Board prep for next week",
            "latest_sender": "Chair <board@company.com>",
            "importance_level": "low",
            "importance_score": 12,
            "suppressed": False,
            "category": "board",
        },
    ]

    primary = select_primary_thread(ranked_threads)

    assert primary is not None
    assert primary["subject"] == "Board prep for next week"


def test_runner_uses_planner_led_day_schedule_path_for_weekly_request() -> None:
    runner = AssistantWorkflowRunner()
    runner.runtime._execute_context_stage = lambda **kwargs: None  # type: ignore[method-assign]

    async def fake_briefing_run(agent_input, **kwargs):
        return AgentOutput(
            agent_name="briefing_agent",
            stage=agent_input.stage,
            success=True,
            summary="ok",
            structured_output={
                "answer": {"title": "ok", "summary": "ok", "sections": []},
                "trust": TrustMetadata().model_dump(),
                "sources": [],
            },
            metadata={},
        )

    runner.runtime.agents["briefing_agent"].run = fake_briefing_run  # type: ignore[method-assign]
    runner._safe_fetch_email_event = lambda current_user: {  # type: ignore[method-assign]
        "subject": "Board prep",
        "ranked_threads": [
            {
                "subject": "Board prep",
                "latest_sender": "Chair <board@company.com>",
                "importance_level": "high",
                "importance_score": 88,
                "suppressed": False,
            }
        ],
        "structured_watch": {"deadlines": [{"deadline": "Board memo due Tuesday"}], "asks": []},
    }
    runner._safe_fetch_calendar_event = lambda current_user: {  # type: ignore[method-assign]
        "title": "Next week schedule",
        "upcoming_events": [{"title": "Board meeting", "starts_at": "2026-03-24T09:00:00"}],
        "related_threads": [],
    }

    payload = AssistantQueryRequest(
        message="Scan my inbox and generate me a schedule plan for next week.",
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=1, ceo_id="ceo_test", query=payload.message)
    user = User(
        id=1,
        username="ceo",
        hashed_password="x",
        ceo_id="ceo_test",
        company_name="Agentic Mind",
    )

    response = asyncio.run(runner.run(payload, interaction, user))

    assert response.workflow_type == WorkflowType.SCHEDULE_PLANNING
    assert response.status == "completed"


def test_direct_tomorrow_planning_uses_shared_execution_module() -> None:
    runner = AssistantWorkflowRunner()
    runner.runtime._execute_context_stage = lambda **kwargs: None  # type: ignore[method-assign]

    async def fake_briefing_run(agent_input, **kwargs):
        return AgentOutput(
            agent_name="briefing_agent",
            stage=agent_input.stage,
            success=True,
            summary="ok",
            structured_output={
                "answer": {"title": "ok", "summary": "ok", "sections": []},
                "trust": TrustMetadata().model_dump(),
                "sources": [],
            },
            metadata={},
        )

    runner.runtime.agents["briefing_agent"].run = fake_briefing_run  # type: ignore[method-assign]
    runner._safe_fetch_email_event = lambda current_user: {"ranked_threads": [], "structured_watch": {"deadlines": [], "asks": []}}  # type: ignore[method-assign]
    runner._safe_fetch_calendar_event = lambda current_user: {"upcoming_events": []}  # type: ignore[method-assign]

    payload = AssistantQueryRequest(
        message="Plan my day for tomorrow.",
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=2, ceo_id="ceo_test", query=payload.message)
    user = User(
        id=1,
        username="ceo",
        hashed_password="x",
        ceo_id="ceo_test",
        company_name="Agentic Mind",
    )

    response = asyncio.run(runner.run(payload, interaction, user))

    assert response.workflow_type == WorkflowType.SCHEDULE_PLANNING
    assert response.status == "completed"


def test_briefing_trust_reflects_strong_vs_weak_planner_evidence() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]

    strong_trust = agent._derive_trust(  # type: ignore[attr-defined]
        "schedule_planning",
        {
            "ranked_threads": [
                {"suppressed": False, "importance_level": "high"},
                {"suppressed": False, "importance_level": "medium"},
            ],
            "structured_watch": {"deadlines": [{"deadline": "Tuesday"}, {"deadline": "Thursday"}]},
            "upcoming_events": [{"title": "Board"}, {"title": "Ops review"}],
            "document_context": {"attachments": [{"document_id": "doc_1"}]},
            "planning_context": {
                "mode": "compound_plan",
                "execution_steps": [
                    {"key": "gather_email", "status": "completed"},
                    {"key": "gather_calendar", "status": "completed"},
                    {"key": "synthesize_response", "status": "completed"},
                ],
                "evidence_summary": {"context_source_count": 3},
            },
        },
        {"signals": [{"subject": "Signal"}]},
    )
    weak_trust = agent._derive_trust(  # type: ignore[attr-defined]
        "schedule_planning",
        {
            "ranked_threads": [{"suppressed": True, "category": "promotional", "importance_level": "medium"}],
            "structured_watch": {"deadlines": []},
            "upcoming_events": [],
            "planning_context": {
                "mode": "compound_plan",
                "execution_steps": [{"key": "gather_email", "status": "completed"}],
                "evidence_summary": {"context_source_count": 1},
            },
        },
        {"signals": []},
    )

    assert strong_trust.confidence_score > weak_trust.confidence_score
    assert strong_trust.confidence in {"medium", "high"}
    assert weak_trust.confidence == "low"


def test_sparse_planner_execution_reduces_trust() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]

    trust = agent._derive_trust(  # type: ignore[attr-defined]
        "schedule_planning",
        {
            "ranked_threads": [],
            "structured_watch": {"deadlines": []},
            "upcoming_events": [],
            "planning_context": {
                "mode": "compound_plan",
                "execution_steps": [
                    {"key": "gather_email", "status": "completed"},
                    {"key": "gather_calendar", "status": "completed"},
                    {"key": "synthesize_response", "status": "completed"},
                ],
                "evidence_summary": {"context_source_count": 1},
            },
            "plan_execution": {
                "sparse_guidance": True,
                "evidence_summary": {
                    "placed_candidate_count": 0,
                    "candidate_count": 0,
                },
            },
        },
        {"signals": []},
    )

    assert trust.confidence == "low"
    assert any("sparse guidance" in item.lower() for item in trust.missing_context)


def test_next_week_planning_copy_does_not_use_today_language() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "structured_watch": {"deadlines": []},
        "upcoming_events": [],
        "planning_context": {"time_horizon": "next_week", "mode": "compound_plan"},
    }

    sections = agent._build_sections(  # type: ignore[attr-defined]
        workflow_type="schedule_planning",
        items=[],
        ranked_threads=[],
        structured_watch=event_payload["structured_watch"],
        event_payload=event_payload,
    )

    rendered = " ".join(
        item
        for section in sections
        for item in section.items
    ).lower()
    assert "today" not in rendered
    assert "next week" in rendered


def test_suppressed_promotional_threads_are_excluded_from_schedule_blocks() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    blocks = agent._schedule_blocks(  # type: ignore[attr-defined]
        ranked_threads=[
            {
                "subject": "Limited time offer",
                "latest_sender": "promo@sofi.com",
                "importance_score": 90,
                "suppressed": True,
                "category": "promotional",
            },
            {
                "subject": "Board prep",
                "latest_sender": "board@company.com",
                "importance_score": 80,
                "suppressed": False,
                "category": "board",
            },
        ],
        structured_watch={"asks": [], "deadlines": [], "implied_docs": []},
        event_payload={
            "upcoming_events": [{"title": "Board meeting", "starts_at": "2026-03-24T09:00:00"}],
            "planning_context": {"time_horizon": "next_week", "mode": "compound_plan"},
        },
    )

    combined = " ".join(blocks)
    assert "Limited time offer" not in combined
    assert "Board prep" in combined


def test_zero_actionable_evidence_returns_sparse_honest_planning_output() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {"subject": "Promo", "suppressed": True, "category": "promotional", "importance_score": 90}
        ],
        "structured_watch": {"asks": [], "deadlines": [], "implied_docs": []},
        "upcoming_events": [],
        "planning_context": {"time_horizon": "next_week", "mode": "compound_plan"},
    }

    blocks = agent._schedule_blocks(  # type: ignore[attr-defined]
        ranked_threads=event_payload["ranked_threads"],
        structured_watch=event_payload["structured_watch"],
        event_payload=event_payload,
    )
    summary = agent._day_schedule_summary(event_payload)  # type: ignore[attr-defined]
    trust = agent._derive_trust("schedule_planning", event_payload, {"signals": []})  # type: ignore[attr-defined]

    assert len(blocks) == 1
    assert "not enough actionable inbox or calendar evidence" in blocks[0].lower()
    assert "not enough actionable inbox and calendar evidence" in summary.lower()
    assert trust.confidence == "low"


def test_briefing_agent_renders_precomputed_schedule_results() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {
                "subject": "Promo",
                "latest_sender": "promo@mailer.com",
                "suppressed": True,
                "category": "promotional",
                "importance_score": 90,
            }
        ],
        "structured_watch": {"asks": [], "deadlines": [], "implied_docs": []},
        "upcoming_events": [],
        "planning_context": {
            "time_horizon": "next_week",
            "mode": "compound_plan",
            "evidence_summary": {"context_source_count": 2},
        },
        "plan_execution": {
            "schedule_blocks": [
                "Mon Mar 23 8:30 AM-9:00 AM: Prepare for Board meeting at 9:00 AM."
            ],
            "planning_window": {"horizon": "next_week"},
            "evidence_summary": {"placed_candidate_count": 1, "candidate_count": 1},
            "sparse_guidance": False,
        },
    }

    sections = agent._build_sections(  # type: ignore[attr-defined]
        workflow_type="schedule_planning",
        items=[],
        ranked_threads=event_payload["ranked_threads"],
        structured_watch=event_payload["structured_watch"],
        event_payload=event_payload,
    )

    schedule_section = next(section for section in sections if section.label == "Schedule Proposal")
    assert schedule_section.items == ["8:30 AM-9:00 AM: Prep for Board meeting"]


def test_schedule_followup_request_produces_task_breakdown_with_owners_and_deadlines() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {
                "subject": "Northstar escalation",
                "latest_sender": "northstar@client.com",
                "importance_score": 92,
                "importance_level": "high",
                "suppressed": False,
                "category": "customer",
            }
        ],
        "structured_watch": {
            "asks": [{"ask": "Send recovery plan to Northstar"}],
            "deadlines": [{"deadline": "Today 5 PM"}],
            "implied_docs": [{"document": "Recovery plan memo"}],
        },
        "upcoming_events": [{"title": "Customer escalation review", "starts_at": "2026-03-24T10:00:00-07:00"}],
        "planning_context": {"time_horizon": "today", "mode": "compound_plan"},
    }

    sections = agent._build_sections(  # type: ignore[attr-defined]
        workflow_type="schedule_planning",
        items=[],
        ranked_threads=event_payload["ranked_threads"],
        structured_watch=event_payload["structured_watch"],
        event_payload=event_payload,
        task_input="Be more specific. Give me a prioritized task breakdown with owners and deadlines.",
        history=[{"query": "Plan my day for today."}],
    )

    planning = next(section for section in sections if section.label == "Planning Inputs")
    proposal = next(section for section in sections if section.label == "Schedule Proposal")
    follow_ups = next(section for section in sections if section.label == "Suggested Follow-Ups")

    assert any("Detailed task breakdown requested" in item for item in planning.items)
    assert any("P1 CEO" in item for item in proposal.items)
    assert any("delegate" in item.lower() or "owner" in item.lower() for item in follow_ups.items)


def test_schedule_followup_summary_changes_from_generic_schedule_copy() -> None:
    agent = BriefingAgent(tools=None)  # type: ignore[arg-type]
    event_payload = {
        "ranked_threads": [
            {
                "subject": "Investor update",
                "latest_sender": "investor@fund.com",
                "importance_score": 88,
                "importance_level": "high",
                "suppressed": False,
                "category": "investor",
            }
        ],
        "structured_watch": {
            "asks": [{"ask": "Confirm hiring pace assumption"}],
            "deadlines": [{"deadline": "Today 3 PM"}],
            "implied_docs": [],
        },
        "upcoming_events": [{"title": "Investor check-in", "starts_at": "2026-03-24T16:00:00-07:00"}],
        "planning_context": {"time_horizon": "today", "mode": "compound_plan"},
    }

    payload = agent._generate_payload(  # type: ignore[attr-defined]
        workflow_type="schedule_planning",
        event_payload=event_payload,
        prepared_context={"history": [{"query": "Plan my day for today."}]},
        completion=None,
        task_input="Be more specific about priorities, owners, and deadlines.",
    )

    assert "Top priority:" in payload.answer.summary
    assert "Hard stop:" in payload.answer.summary

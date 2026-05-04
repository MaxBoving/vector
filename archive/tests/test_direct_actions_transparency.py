import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.integrations import providers
from src.agents.report_agent import ReportAgent, ReportAnswer, ReportPayload, ReportSection, ReportTrust
from src.api.schemas import AssistantQueryRequest
from src.core.models import SessionInteraction, User
from src.runtime.engine import RuntimeEngine
from src.tools.registry import ToolRegistry
from src.workflows.direct_actions import maybe_handle_direct_action_request


def _user() -> User:
    return User(id=1, username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")


def test_direct_actions_reports_execution_limit_on_first_turn(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.workflows.direct_actions.get_integration_statuses",
        lambda ceo_id: [
            {"service": "gmail", "connected": False},
            {"service": "outlook_mail", "connected": False},
        ],
    )

    payload = AssistantQueryRequest(
        message="Send that email to Rachel Lim right now and copy James Okafor.",
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=77, ceo_id="ceo_test", query=payload.message, status="PENDING")
    unified_memory = {
        "working_memory": {
            "resolved_action_reference": {
                "action_type": "send_email",
                "proposal": {
                    "to": "",
                    "subject": "Executive Commitment on AI Platform Timeline",
                    "body": "Hi Rachel,\n\nI am reaching out directly regarding the launch delay.",
                    "cc": ["james@redwood.example"],
                },
            },
            "write_action_requested": True,
            "deliverable": {"kind": "report"},
            "must_not_do": [],
            "workflow_preference": "report_generation",
            "mode": "new_request",
        },
        "session_memory": {},
    }

    response = asyncio.run(
        maybe_handle_direct_action_request(
            payload=payload,
            interaction=interaction,
            current_user=_user(),
            history=[],
            unified_memory=unified_memory,
        )
    )

    assert response is not None
    assert response.answer.title == "Execution Not Available Here"
    assert "cannot execute the email write action" in response.answer.summary.lower()
    assert response.metadata["execution_unavailable"]["channel"] == "email"
    assert "No writable email provider" in response.metadata["execution_unavailable"]["reason"]


def test_direct_actions_email_unavailable_offers_connectors(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.workflows.direct_actions.get_integration_statuses",
        lambda ceo_id: [
            {"service": "gmail", "connected": False},
            {"service": "outlook_mail", "connected": False},
        ],
    )

    payload = AssistantQueryRequest(
        message="Draft an email to jane@company.com subject 'Board follow-up' saying 'We should meet next week.'",
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=90, ceo_id="ceo_test", query=payload.message, status="PENDING")

    response = asyncio.run(
        maybe_handle_direct_action_request(
            payload=payload,
            interaction=interaction,
            current_user=_user(),
            history=[],
            unified_memory={"working_memory": {}, "session_memory": {}},
        )
    )

    assert response is not None
    assert response.presentation.preamble == "Connect an email provider to send this directly."
    assert response.trust.question_options[0].options[0].value == "connect_google_workspace"
    assert response.trust.question_options[0].options[1].value == "connect_outlook_workspace"


def test_direct_actions_email_pending_flow_uses_send_and_discard(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.workflows.direct_actions.get_integration_statuses",
        lambda ceo_id: [
            {"service": "gmail", "connected": True},
        ],
    )

    payload = AssistantQueryRequest(
        message="Draft an email to jane@company.com subject 'Board follow-up' saying 'We should meet next week.'",
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=91, ceo_id="ceo_test", query=payload.message, status="PENDING")

    response = asyncio.run(
        maybe_handle_direct_action_request(
            payload=payload,
            interaction=interaction,
            current_user=_user(),
            history=[],
            unified_memory={"working_memory": {}, "session_memory": {}},
        )
    )

    assert response is not None
    assert response.answer.title == "Email Draft Ready"
    assert response.presentation.mode == "draft"
    assert response.presentation.draft.status == "ready_to_send"
    assert [option["label"] for option in response.metadata["gate"]["options"]] == ["Send", "Discard"]


def test_runtime_question_option_guards_drop_invalid_entries() -> None:
    engine = RuntimeEngine(ToolRegistry())

    trust = engine._normalize_trust_payload(
        {
            "confidence": "high",
            "question_options": [
                {
                    "question": "Choose one",
                    "offer_type": "clarification",
                    "options": [{"label": "Only one", "value": "one", "apply_text": "one"}],
                },
                {
                    "question": "Valid binary choice",
                    "offer_type": "clarification",
                    "options": [
                        {"label": "Option A", "value": "a", "apply_text": "A"},
                        {"label": "Option B", "value": "b", "apply_text": "B"},
                    ],
                },
                {
                    "question": "Empty offer",
                    "offer_type": "action_offer",
                    "options": [],
                },
                {
                    "question": "Valid binary choice",
                    "offer_type": "clarification",
                    "options": [
                        {"label": "Duplicate A", "value": "a2", "apply_text": "A2"},
                        {"label": "Duplicate B", "value": "b2", "apply_text": "B2"},
                    ],
                },
            ],
        }
    )

    assert trust["question_options"] == [
        {
            "question": "Valid binary choice",
            "offer_type": "clarification",
            "priority_score": 5.0,
            "options": [
                {"label": "Option A", "value": "a", "apply_text": "A", "description": None},
                {"label": "Option B", "value": "b", "apply_text": "B", "description": None},
            ],
        }
    ]


def test_direct_actions_skips_generic_email_path_for_customer_analysis_request() -> None:
    payload = AssistantQueryRequest(
        message=(
            "I need you to identify our highest-risk customer situation right now and draft an immediate outreach email. "
            "Then send it, copy the account owner, and schedule a follow-up call for tomorrow."
        ),
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=88, ceo_id="ceo_test", query=payload.message, status="PENDING")
    unified_memory = {
        "working_memory": {
            "write_action_requested": True,
            "deliverable": {"kind": "email"},
            "must_not_do": [],
            "workflow_preference": "email_ingestion",
            "mode": "new_request",
            "task_topic": "customer_escalation",
        },
        "session_memory": {},
    }

    response = asyncio.run(
        maybe_handle_direct_action_request(
            payload=payload,
            interaction=interaction,
            current_user=_user(),
            history=[],
            unified_memory=unified_memory,
        )
    )

    assert response is None


def test_direct_actions_do_not_treat_context_wrapped_brief_followup_as_calendar_request() -> None:
    payload = AssistantQueryRequest(
        message=(
            "[Context: Prior question: Make me a schedule for next week | Prior response: next week Schedule Proposal]\n\n"
            'Follow-up action: Build a brief on the at-risk initiative "Northstar Health Renewal". '
            "Include current status, blockers, and recovery owners."
        ),
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=89, ceo_id="ceo_test", query=payload.message, status="PENDING")
    unified_memory = {
        "working_memory": {
            "write_action_requested": False,
            "deliverable": {"kind": "report"},
            "must_not_do": [],
            "workflow_preference": "schedule_planning",
            "mode": "continuation",
            "task_topic": "customer_escalation",
        },
        "session_memory": {
            "previous_response_title": "next week Schedule Proposal",
        },
    }

    response = asyncio.run(
        maybe_handle_direct_action_request(
            payload=payload,
            interaction=interaction,
            current_user=_user(),
            history=[],
            unified_memory=unified_memory,
        )
    )

    assert response is None


def test_direct_actions_do_not_treat_generic_schedule_request_as_calendar_write() -> None:
    payload = AssistantQueryRequest(
        message="Make me a schedule",
        conversation_id="conv_test",
    )
    interaction = SessionInteraction(id=95, ceo_id="ceo_test", query=payload.message, status="PENDING")
    unified_memory = {
        "working_memory": {
            "write_action_requested": False,
            "deliverable": {"kind": "report"},
            "must_not_do": [],
            "workflow_preference": "schedule_planning",
            "mode": "new_request",
        },
        "session_memory": {},
    }

    response = asyncio.run(
        maybe_handle_direct_action_request(
            payload=payload,
            interaction=interaction,
            current_user=_user(),
            history=[],
            unified_memory=unified_memory,
        )
    )

    assert response is None


def test_provider_statuses_hide_demo_writes_when_eval_env_disables_them(monkeypatch) -> None:
    monkeypatch.setenv("AGENTICMIND_DISABLE_WRITES_FOR_CEO_IDS", "smart-eval-ceo")
    monkeypatch.setattr(
        "src.integrations.providers._ensure_demo_account",
        lambda ceo_id, service: type(
            "DemoAccount",
            (),
            {"account_email": f"{ceo_id}.demo@agenticmind.local", "expires_at": None},
        )(),
    )
    monkeypatch.setattr("src.integrations.providers.get_connected_accounts", lambda ceo_id: [])

    statuses = providers.get_integration_statuses("smart-eval-ceo")
    by_service = {item["service"]: item for item in statuses}

    assert by_service["gmail"]["connected"] is False
    assert by_service["google_calendar"]["connected"] is False
    assert by_service["google_drive"]["connected"] is True


def test_report_agent_suppresses_generic_finance_cut_offer_for_pricing_followup() -> None:
    agent = ReportAgent(ToolRegistry())
    payload = ReportPayload(
        answer=ReportAnswer(
            title="AlphaSystems Pricing Analysis",
            summary="Competitive pricing pressure in DACH requires a response.",
            sections=[ReportSection(label="Financial Snapshot", items=["AlphaSystems undercutting in DACH"])],
        ),
        trust=ReportTrust(
            confidence="medium",
            confidence_score=0.6,
            data_quality="medium",
        ),
    )

    offers = agent._build_action_offers(  # type: ignore[attr-defined]
        "Open the pricing workbook and show me AlphaSystems price points in DACH.",
        payload,
        intent_state={},
    )

    assert offers == []


def test_report_agent_uses_concrete_next_offer_after_customer_rescue_package() -> None:
    agent = ReportAgent(ToolRegistry())
    payload = ReportPayload(
        answer=ReportAnswer(
            title="Redwood Systems $420K ARR Rescue Package",
            summary="The Redwood rescue plan is ready and the next customer-facing assets should be prepared now.",
            sections=[
                ReportSection(label="Call Script", items=["Opening script for Rachel Lim"]),
                ReportSection(label="Apex Health Recovery Package", items=["Draft the apology letter with service credit"]),
            ],
        ),
        trust=ReportTrust(
            confidence="medium",
            confidence_score=0.7,
            data_quality="medium",
        ),
    )

    offers = agent._build_action_offers(  # type: ignore[attr-defined]
        "Build the Redwood rescue package first - I need a complete package I can review and send within the hour.",
        payload,
        intent_state={},
    )

    assert len(offers) == 1
    offer = offers[0]
    assert offer["question"] == "I can draft the Apex apology letter now, or prepare the Rachel Lim follow-up note now. Which should I build first?"
    assert {option["label"] for option in offer["options"]} == {"Apex apology letter", "Rachel follow-up note"}

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import agents package first to avoid circular init via context_loading.py
from src.agents.briefing_agent import BriefingAgent  # noqa: F401
from src.workflows.planning_types import RequestPlan

from src.workflows.context_loading import (
    build_context_stage_actions,
    prepare_briefing_context,
    prepare_report_context,
)


def test_report_context_actions_request_drive_search_with_content_hydration() -> None:
    actions = build_context_stage_actions(
        workflow_type="report_generation",
        stage_name="retrieve_documents",
        task_input="Give me a finance close summary",
        workflow_metadata={},
    )

    drive_action = next(action for action in actions if action.target == "google_drive_search")
    assert drive_action.args["read_contents_limit"] == 2


def test_prepare_report_context_merges_drive_content_into_retrieval_documents() -> None:
    context = {
        "company_state": {"capital_position": {"Cash at Bank": 100}},
        "retrieval": [{"title": "Primary memo", "content": "Primary content", "purpose": "reference"}],
        "drive_retrieval": {
            "files": [
                {
                    "file_id": "drive_1",
                    "name": "Board Notes",
                    "type": "Google Doc",
                    "mime_type": "application/vnd.google-apps.document",
                    "modified_at": "2026-03-24T10:00:00Z",
                    "exportable": True,
                    "content_excerpt": "Drive board notes with corrective actions and finance close narrative.",
                }
            ]
        },
    }

    prepared = prepare_report_context(context)
    titles = [doc.title for doc in prepared.retrieved_documents]
    drive_doc = next(doc for doc in prepared.retrieved_documents if doc.title == "Board Notes")

    assert "Board Notes" in titles
    assert "finance close narrative" in drive_doc.content
    assert drive_doc.source_type == "drive_document"


def test_prepare_briefing_context_includes_crm_deals() -> None:
    prepared = prepare_briefing_context(
        {
            "crm_deals": {
                "deals": [
                    {
                        "name": "Expansion - Apex Health",
                        "account_name": "Apex Health",
                        "stage": "Proposal",
                        "amount": 250000,
                    }
                ]
            }
        },
        attachments=[],
    )

    assert prepared.crm_deals[0]["account_name"] == "Apex Health"


def test_prepare_briefing_context_promotes_request_plan_into_event_payload() -> None:
    prepared = prepare_briefing_context(
        {
            "request_plan": RequestPlan(
                mode="direct_workflow",
                target_workflow="weekly_recap",
                direct_workflow="weekly_recap",
                time_horizon="this_week",
                target_date=None,
                target_label="This Week",
                needed_context_sources=["email", "calendar"],
            ).model_dump(mode="json"),
            "ranked_threads": [{"subject": "Board packet follow-up", "importance_level": "high"}],
            "upcoming_events": [{"title": "Exec review", "starts_at": "2026-05-10T09:00:00-07:00"}],
            "task_input": "Prepare a weekly recap",
        },
        attachments=[],
    )

    planning_context = prepared.event_payload["planning_context"]
    assert planning_context["time_horizon"] == "this_week"
    assert planning_context["query"] == "Prepare a weekly recap"
    assert planning_context["evidence_summary"]["actionable_thread_count"] == 1
    assert planning_context["evidence_summary"]["meeting_count"] == 1
    assert planning_context["retrieval_plan"]["sources"][0]["source"] == "email"
    assert prepared.request_plan["target_workflow"] == "weekly_recap"
    assert prepared.ranked_threads[0]["subject"] == "Board packet follow-up"
    assert prepared.upcoming_events[0]["title"] == "Exec review"
    assert prepared.structured_watch["asks"] == []


def test_prepare_briefing_context_promotes_live_connector_lists() -> None:
    prepared = prepare_briefing_context(
        {
            "live_threads": [{"subject": "Live inbox thread", "importance_level": "high"}],
            "live_events": [{"title": "Live calendar item", "start_time": "2026-05-11T09:00:00-07:00", "end_time": "2026-05-11T09:30:00-07:00"}],
            "task_input": "Prepare a morning brief",
        },
        attachments=[],
    )

    assert prepared.ranked_threads[0]["subject"] == "Live inbox thread"
    assert prepared.upcoming_events[0]["title"] == "Live calendar item"
    assert prepared.upcoming_events[0]["starts_at"] == "2026-05-11T09:00:00-07:00"
    assert prepared.upcoming_events[0]["ends_at"] == "2026-05-11T09:30:00-07:00"
    assert prepared.event_payload["live_threads"][0]["subject"] == "Live inbox thread"
    assert prepared.event_payload["live_events"][0]["title"] == "Live calendar item"
    assert prepared.event_payload["live_events"][0]["starts_at"] == "2026-05-11T09:00:00-07:00"


def test_prepare_report_context_builds_finance_context_bundle() -> None:
    prepared = prepare_report_context(
        {
            "company_state": {
                "revenue_segmentation": {"Europe revenue": 3600000},
                "cost_structure": {"AWS cost": 420000},
                "capital_position": {"Cash runway": 19.2},
            },
            "retrieval": [{"title": "Board Packet Draft", "content": "Finance close variance narrative", "purpose": "audited_finance_doc"}],
            "signals": [{"title": "Cloud spend variance above forecast", "content": "AWS cost spike this week", "domains": ["finance"], "_source_hint": "finance"}],
            "session_history": [{"query": "Give me a runway and burn review"}],
        }
    )

    assert any(item["name"] == "Europe revenue" for item in prepared.finance_context["current_metrics"])
    assert "Board Packet Draft" in prepared.finance_context["board_materials"]
    assert any("variance" in item.get("title", "").lower() for item in prepared.finance_context["variance_signals"])
    assert prepared.quantitative_evidence.numeric_series
    assert any(row["metric"] == "Europe revenue" for row in prepared.quantitative_evidence.numeric_series)
    assert prepared.quantitative_evidence.available_fields == ["value"]

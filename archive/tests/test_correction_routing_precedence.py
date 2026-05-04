from src.assistant.service import decide_correction_route
from src.workflows.action_semantics import ActionSemanticSignals
from src.workflows.intent_state import IntentState
from src.workflows.runner_semantics import RunnerSemanticSignals, SharedTurnSemanticBundle
from src.workflows.types import WorkflowType


def _semantic_bundle(*, explicit_execution_request: bool, channels: tuple[str, ...]) -> SharedTurnSemanticBundle:
    runner_signals = RunnerSemanticSignals(
        visible_message="x",
        request_plan_direct_workflow=None,
        explicit_execution_request=explicit_execution_request,
        direct_capability_question=False,
        integration_setup_question=False,
        requested_channels=channels,
        report_followup=False,
        live_context_followup=False,
    )
    action_signals = ActionSemanticSignals(
        visible_message="x",
        email_action="email" in channels,
        calendar_action="calendar" in channels,
        explicit_execution_request=explicit_execution_request,
        direct_capability_question=False,
        integration_setup_question=False,
        external_delivery_requested=False,
        requested_channels=channels,
        requires_analysis_before_action=False,
    )
    return SharedTurnSemanticBundle(
        visible_message="x",
        request_plan=None,
        write_intent=(False, None),
        runner_signals=runner_signals,
        action_signals=action_signals,
    )


def test_decide_correction_route_table() -> None:
    cases = [
        {
            "name": "correction_explicit_execution_email",
            "intent_mode": "correction",
            "bundle": _semantic_bundle(explicit_execution_request=True, channels=("email",)),
            "offer_accepted": True,
            "has_workflow_hint": False,
            "force_direct_action": True,
            "direct_workflow_type": WorkflowType.EMAIL_INGESTION,
            "pin_offer_execution": False,
        },
        {
            "name": "correction_offer_acceptance_pins_report",
            "intent_mode": "correction",
            "bundle": _semantic_bundle(explicit_execution_request=False, channels=tuple()),
            "offer_accepted": True,
            "has_workflow_hint": False,
            "force_direct_action": False,
            "direct_workflow_type": None,
            "pin_offer_execution": True,
        },
        {
            "name": "non_correction_no_override",
            "intent_mode": "new_request",
            "bundle": _semantic_bundle(explicit_execution_request=True, channels=("email",)),
            "offer_accepted": True,
            "has_workflow_hint": False,
            "force_direct_action": False,
            "direct_workflow_type": None,
            "pin_offer_execution": False,
        },
    ]

    for case in cases:
        resolved_intent = IntentState(mode=case["intent_mode"])
        decision = decide_correction_route(
            resolved_intent=resolved_intent,
            semantic_bundle=case["bundle"],
            action_offer_accepted=case["offer_accepted"],
            has_workflow_hint=case["has_workflow_hint"],
        )
        assert decision.force_direct_action == case["force_direct_action"], case["name"]
        assert decision.direct_workflow_type == case["direct_workflow_type"], case["name"]
        assert decision.pin_offer_execution == case["pin_offer_execution"], case["name"]


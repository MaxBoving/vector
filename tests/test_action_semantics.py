from __future__ import annotations

from src.workflows.action_semantics import classify_action_semantics


def test_structured_action_reference_drives_email_delivery() -> None:
    signals = classify_action_semantics(
        message="do the thing",
        resolved_action_reference={"action_type": "send_email"},
    )

    assert signals.email_action is True
    assert signals.explicit_execution_request is True
    assert signals.external_delivery_requested is True


def test_structured_write_intent_drives_calendar_delivery() -> None:
    signals = classify_action_semantics(
        message="do the thing",
        precomputed_write_intent=(True, "calendar"),
    )

    assert signals.calendar_action is True
    assert signals.explicit_execution_request is True
    assert signals.external_delivery_requested is True

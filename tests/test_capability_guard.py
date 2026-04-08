from src.api.schemas import QuestionOption
from src.agents.capability_guard import CapabilityGuard


def _make_option(label: str, capability_requires: list[str] | None = None) -> dict:
    return {
        "question": f"Do you want to {label}?",
        "offer_type": "action_offer",
        "options": [
            QuestionOption(
                label=label,
                value=label.lower().replace(" ", "_"),
                apply_text=f"proceed with {label}",
                capability_requires=capability_requires or [],
            ).model_dump()
        ],
    }


def _make_clarification(label: str) -> dict:
    return {
        "question": "Which format do you prefer?",
        "offer_type": "clarification",
        "options": [
            QuestionOption(label="Brief", value="brief", apply_text="give me a brief").model_dump(),
            QuestionOption(label="Full report", value="full", apply_text="give me the full report").model_dump(),
        ],
    }


def test_question_option_has_capability_requires_default():
    opt = QuestionOption(label="Yes", value="yes", apply_text="do it")
    assert opt.capability_requires == []


def test_question_option_capability_requires_set():
    opt = QuestionOption(
        label="Send email",
        value="send",
        apply_text="send the email",
        capability_requires=["email_send"],
    )
    assert opt.capability_requires == ["email_send"]


def test_guard_strips_email_offer_when_no_providers_connected():
    guard = CapabilityGuard()
    options = [_make_option("Send email", ["email_send"])]
    result = guard.strip(options, connected_providers=set())
    assert result == []


def test_guard_keeps_email_offer_when_gmail_connected():
    guard = CapabilityGuard()
    options = [_make_option("Send email", ["email_send"])]
    result = guard.strip(options, connected_providers={"gmail"})
    assert len(result) == 1


def test_guard_keeps_options_without_capability_requires():
    guard = CapabilityGuard()
    options = [_make_option("Draft email", [])]
    result = guard.strip(options, connected_providers=set())
    assert len(result) == 1


def test_guard_never_strips_clarification_offers():
    guard = CapabilityGuard()
    options = [_make_clarification("format")]
    result = guard.strip(options, connected_providers=set())
    assert len(result) == 1


def test_guard_strips_question_when_all_options_stripped():
    guard = CapabilityGuard()
    options = [_make_option("Send email", ["email_send"])]
    result = guard.strip(options, connected_providers=set())
    assert result == []


def test_guard_keeps_question_when_some_options_remain():
    """A question with two options where only one needs email_send — keep the question, strip the option."""
    guard = CapabilityGuard()
    question = {
        "question": "How do you want to proceed?",
        "offer_type": "action_offer",
        "options": [
            QuestionOption(
                label="Send now",
                value="send",
                apply_text="send the email",
                capability_requires=["email_send"],
            ).model_dump(),
            QuestionOption(
                label="Save draft",
                value="draft",
                apply_text="save as draft",
                capability_requires=[],
            ).model_dump(),
        ],
    }
    result = guard.strip([question], connected_providers=set())
    assert len(result) == 1
    assert len(result[0]["options"]) == 1
    assert result[0]["options"][0]["label"] == "Save draft"


def test_guard_maps_email_send_to_gmail_and_outlook():
    guard = CapabilityGuard()
    options = [_make_option("Send email", ["email_send"])]
    assert guard.strip(options, connected_providers={"gmail"}) != []
    assert guard.strip(options, connected_providers={"outlook"}) != []
    assert guard.strip(options, connected_providers=set()) == []


def test_guard_maps_calendar_write_to_google_and_outlook_calendar():
    guard = CapabilityGuard()
    options = [_make_option("Schedule call", ["calendar_write"])]
    assert guard.strip(options, connected_providers={"google_calendar"}) != []
    assert guard.strip(options, connected_providers={"outlook_calendar"}) != []
    assert guard.strip(options, connected_providers=set()) == []

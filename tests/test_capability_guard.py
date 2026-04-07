from src.api.schemas import QuestionOption


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

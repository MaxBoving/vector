# tests/test_report_prompt_plan_block.py
from src.agents.report_agent import ReportAgent


def _make_agent() -> ReportAgent:
    return ReportAgent(tools=None)  # type: ignore[arg-type]


def test_report_prompt_contains_composition_plan_instructions():
    agent = _make_agent()
    prompt = agent._report_prompt(
        task_input="How are we positioned against competitors on pricing?",
        company_state={},
        company_identity={},
        preferences={},
        project_context={},
        session_history=[],
        signals=[],
        retrieval=[],
    )
    assert "CompositionPlan" in prompt
    assert "section_labels" in prompt


def test_report_prompt_no_longer_contains_finance_template_block():
    agent = _make_agent()
    prompt = agent._report_prompt(
        task_input="Board financial review",
        company_state={},
        company_identity={},
        preferences={},
        project_context={},
        session_history=[],
        signals=[],
        retrieval=[],
    )
    assert "Finance template:" not in prompt
    assert "Preferred section labels:" not in prompt

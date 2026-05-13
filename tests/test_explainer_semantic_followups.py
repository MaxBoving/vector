from __future__ import annotations

from types import SimpleNamespace

from src.agents.explainer_agent import ExplainerAgent


def test_explainer_agent_attaches_semantic_followups_to_fallback_payload() -> None:
    agent = ExplainerAgent(SimpleNamespace())

    payload = agent._generate_explanation_payload(  # type: ignore[attr-defined]
        task_input="Explain this contract",
        retrieval=[],
        attachments=[{"document_id": "doc-1", "filename": "Contract.pdf"}],
        completion=None,
    )

    assert payload.trust.semantic_context is not None
    assert payload.trust.semantic_context.topic == "Business Implication Brief: Contract.pdf"
    assert payload.trust.question_options
    assert payload.trust.question_options[0]["offer_type"] == "clarification"
    assert "Business Implication Brief: Contract.pdf" in payload.trust.question_options[0]["question"]

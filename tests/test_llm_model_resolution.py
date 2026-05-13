from __future__ import annotations

import importlib


def test_llm_client_defaults_to_env_anthropic_model(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    monkeypatch.delenv("LLM_DEFAULT_MODEL", raising=False)

    llm = importlib.import_module("src.core.llm")
    llm = importlib.reload(llm)
    client = llm.LLMClient()
    assert client.model == "claude-sonnet-4-20250514"


def test_intent_classifier_uses_simple_model_override(monkeypatch) -> None:
    monkeypatch.delenv("INTENT_CLASSIFIER_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_SIMPLE_MODEL", "claude-3-haiku-20240307")

    module = importlib.import_module("src.assistant.intent_classifier")
    module = importlib.reload(module)
    assert module.CLASSIFIER_MODEL == "claude-3-haiku-20240307"

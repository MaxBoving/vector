from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import anthropic
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.llm import LLMClient


def _connection_error() -> anthropic.APIConnectionError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(message="Connection error.", request=request)


def test_anthropic_connection_error_falls_back_to_openai_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(model="claude-sonnet-4-20250514")

    def fail(_prompt: str, _system_prompt: str) -> str:
        raise _connection_error()

    def fallback(prompt: str, system_prompt: str) -> str:
        assert prompt == "hello"
        assert system_prompt == "system"
        return "fallback-ok"

    monkeypatch.setattr(client, "_complete_anthropic_with_fallbacks", fail)
    monkeypatch.setattr(client, "_fallback_to_openai", fallback)

    assert client._complete_anthropic("hello", "system") == "fallback-ok"


def test_anthropic_connection_error_falls_back_to_openai_async(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(model="claude-sonnet-4-20250514")

    async def fail(_prompt: str, _system_prompt: str) -> str:
        raise _connection_error()

    async def fallback(prompt: str, system_prompt: str) -> str:
        assert prompt == "hello"
        assert system_prompt == "system"
        return "fallback-ok"

    monkeypatch.setattr(client, "_complete_anthropic_async_with_fallbacks", fail)
    monkeypatch.setattr(client, "_fallback_to_openai_async", fallback)

    result = asyncio.run(client._complete_anthropic_async("hello", "system"))

    assert result == "fallback-ok"

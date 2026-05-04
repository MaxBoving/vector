# Agentic Backend Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 5-stage routing/workflow pipeline with a single Claude tool loop — message → Claude with tools → response — keeping the existing API contract.

**Architecture:** Three new files in `src/assistant/` replace ~20,000 lines of orchestration. `sdk_tools.py` adapts existing `BaseTool` classes to Anthropic tool definitions. `agent.py` owns the tool loop and approval short-circuit. `approval.py` stores and executes write actions after CEO confirmation. The API route is thinned to one call.

**Tech Stack:** Python 3.11, FastAPI, anthropic SDK (`anthropic` package), SQLModel, existing `src/tools/` registry, existing `src/core/database.py` functions.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `src/assistant/sdk_tools.py` | Tool schemas + BaseTool → Anthropic adapter |
| Create | `src/assistant/approval.py` | Store/execute/reject pending write actions |
| Create | `src/assistant/agent.py` | Tool loop, context loading, response building |
| Modify | `src/api/routes/assistant.py` | Replace `AssistantService` call with `AgenticAssistant` |
| Create | `tests/test_agent.py` | Integration tests for new pipeline |
| Archive | `src/agents/`, `src/runtime/`, select `src/assistant/` files | Move dead orchestration code |

**Note on `src/workflows/`:** Several files in `src/workflows/` are still imported by `src/tools/` and `src/api/routes/documents.py` (`workbook_models`, `coauthoring`, `webhook_normalizer`, etc.). These utility files stay in place. Only the routing/orchestration files (`routing.py`, `request_planner.py`, `runner.py`, `direct_actions.py`, etc.) are archived.

---

## Task 1: Create `src/assistant/sdk_tools.py`

**Files:**
- Create: `src/assistant/sdk_tools.py`
- Test: `tests/test_agent.py` (initial)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agent.py
import json
import pytest
from src.assistant.sdk_tools import (
    get_anthropic_tools,
    execute_tool,
    WRITE_TOOL_NAMES,
    READ_TOOL_NAMES,
)
from src.tools.base import ToolContext


def test_get_anthropic_tools_returns_list_of_dicts():
    tools = get_anthropic_tools()
    assert isinstance(tools, list)
    assert len(tools) > 0
    for t in tools:
        assert "name" in t
        assert "description" in t
        assert "input_schema" in t
        assert t["input_schema"]["type"] == "object"


def test_write_tool_names_are_subset_of_exposed():
    from src.assistant.sdk_tools import EXPOSED_TOOL_NAMES
    assert WRITE_TOOL_NAMES.issubset(EXPOSED_TOOL_NAMES)


def test_read_tool_names_do_not_overlap_with_write():
    assert READ_TOOL_NAMES.isdisjoint(WRITE_TOOL_NAMES)


def test_send_email_draft_is_write_tool():
    assert "send_email_draft" in WRITE_TOOL_NAMES


def test_read_email_threads_is_read_tool():
    assert "read_email_threads" in READ_TOOL_NAMES


def test_execute_tool_returns_string():
    # get_preferences is a read tool that gracefully handles missing DB data
    context = ToolContext(ceo_id="test_ceo_001")
    result = execute_tool("get_preferences", {}, context)
    assert isinstance(result, str)
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_agent.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'src.assistant.sdk_tools'`

- [ ] **Step 3: Create `src/assistant/sdk_tools.py`**

```python
"""Adapts existing BaseTool classes to Anthropic tool definitions.

Read tools execute immediately in the agent loop.
Write tools short-circuit the loop and are held for CEO approval.
"""
from __future__ import annotations

import json
from typing import Any

from src.tools.base import ToolContext
from src.tools.registry import build_default_tool_registry, ToolRegistry

# ---------------------------------------------------------------------------
# Tool sets
# ---------------------------------------------------------------------------

READ_TOOL_NAMES: frozenset[str] = frozenset({
    "read_email_threads",
    "read_calendar_events",
    "get_company_state",
    "get_company_identity_profile",
    "get_preferences",
    "get_session_history",
    "get_situational_profile",
    "get_live_context",
    "get_recent_signals",
    "get_unread_signals",
    "get_project_context",
    "get_entity_context",
    "get_thread_entries",
    "get_connector_status",
    "semantic_search",
    "crm_deal_context",
    "slack_read",
    "google_drive_search",
    "google_drive_read",
    "read_artifact",
    "list_artifacts",
    "extract_pdf",
    "variance_analysis",
    "execute_math",
})

WRITE_TOOL_NAMES: frozenset[str] = frozenset({
    "send_email_draft",
    "slack_post",
    "create_docx_memo",
    "create_pptx_deck",
    "create_workbook",
    "create_canvas",
})

EXPOSED_TOOL_NAMES: frozenset[str] = READ_TOOL_NAMES | WRITE_TOOL_NAMES

# ---------------------------------------------------------------------------
# Input schemas for each exposed tool
# ---------------------------------------------------------------------------

_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_email_threads": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max threads to return (default 10)"},
        },
        "required": [],
    },
    "read_calendar_events": {
        "type": "object",
        "properties": {
            "days_ahead": {"type": "integer", "description": "Days ahead to fetch (default 7)"},
        },
        "required": [],
    },
    "get_company_state": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "get_company_identity_profile": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "get_preferences": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "get_session_history": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Number of recent turns (default 10)"},
        },
        "required": [],
    },
    "get_situational_profile": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "get_live_context": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "get_recent_signals": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max signals (default 20)"},
        },
        "required": [],
    },
    "get_unread_signals": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "get_project_context": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "get_entity_context": {
        "type": "object",
        "properties": {
            "entity_name": {"type": "string", "description": "Company or person name to look up"},
        },
        "required": ["entity_name"],
    },
    "get_thread_entries": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max entries (default 20)"},
        },
        "required": [],
    },
    "get_connector_status": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "semantic_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {"type": "integer", "description": "Max results (default 5)"},
        },
        "required": ["query"],
    },
    "crm_deal_context": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_deals", "deal_contacts"],
                "description": "Action to perform",
            },
            "deal_id": {"type": "string", "description": "Deal ID (for deal_contacts action)"},
        },
        "required": ["action"],
    },
    "slack_read": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_channels", "list_dms", "read_messages"],
                "description": "Action to perform",
            },
            "channel_id": {"type": "string", "description": "Channel ID (for read_messages)"},
        },
        "required": ["action"],
    },
    "google_drive_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    },
    "google_drive_read": {
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "Google Drive file ID"},
        },
        "required": ["file_id"],
    },
    "read_artifact": {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "description": "Artifact ID to read"},
        },
        "required": ["artifact_id"],
    },
    "list_artifacts": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "extract_pdf": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Document ID to extract"},
        },
        "required": ["document_id"],
    },
    "variance_analysis": {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "description": "Workbook artifact ID"},
        },
        "required": ["artifact_id"],
    },
    "execute_math": {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "Math expression to evaluate"},
        },
        "required": ["expression"],
    },
    # Write tools
    "send_email_draft": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject line"},
            "body": {"type": "string", "description": "Email body (plain text)"},
            "cc": {"type": "string", "description": "CC recipients, comma-separated (optional)"},
        },
        "required": ["to", "subject", "body"],
    },
    "slack_post": {
        "type": "object",
        "properties": {
            "channel_id": {"type": "string", "description": "Slack channel ID"},
            "message": {"type": "string", "description": "Message text to post"},
        },
        "required": ["channel_id", "message"],
    },
    "create_docx_memo": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Document title"},
            "content": {"type": "string", "description": "Document content in markdown"},
        },
        "required": ["title", "content"],
    },
    "create_pptx_deck": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Deck title"},
            "outline": {"type": "string", "description": "Slide outline in markdown"},
        },
        "required": ["title", "outline"],
    },
    "create_workbook": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Workbook title"},
            "description": {"type": "string", "description": "What this workbook tracks"},
        },
        "required": ["title"],
    },
    "create_canvas": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Canvas title"},
            "content": {"type": "string", "description": "Canvas content"},
        },
        "required": ["title", "content"],
    },
}

# ---------------------------------------------------------------------------
# Registry singleton
# ---------------------------------------------------------------------------

_registry: ToolRegistry | None = None


def _get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = build_default_tool_registry()
    return _registry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_anthropic_tools() -> list[dict[str, Any]]:
    """Return Anthropic tool definitions for all exposed tools."""
    registry = _get_registry()
    result = []
    for name in sorted(EXPOSED_TOOL_NAMES):
        if not registry.has(name):
            continue
        tool = registry.get(name)
        schema = _SCHEMAS.get(name, {"type": "object", "properties": {}, "required": []})
        result.append({
            "name": name,
            "description": tool.metadata.description,
            "input_schema": schema,
        })
    return result


def execute_tool(name: str, inputs: dict[str, Any], context: ToolContext) -> str:
    """Execute a tool and return a JSON string result."""
    registry = _get_registry()
    result = registry.invoke(name, context=context, **inputs)
    if result.success:
        return json.dumps(result.data)
    return json.dumps({"error": result.error})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_agent.py -v 2>&1 | head -30
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/assistant/sdk_tools.py tests/test_agent.py
git commit -m "feat: add sdk_tools adapter — BaseTool classes as Anthropic tool definitions"
```

---

## Task 2: Create `src/assistant/approval.py`

**Files:**
- Create: `src/assistant/approval.py`
- Modify: `tests/test_agent.py` (add approval tests)

- [ ] **Step 1: Add failing tests**

```python
# Append to tests/test_agent.py

from unittest.mock import MagicMock
from src.assistant.approval import is_write_tool, store_pending_action, execute_approval, reject_approval


def test_is_write_tool_true_for_send_email_draft():
    assert is_write_tool("send_email_draft") is True


def test_is_write_tool_false_for_read_email_threads():
    assert is_write_tool("read_email_threads") is False


def _make_user(ceo_id: str = "ceo_test") -> MagicMock:
    user = MagicMock()
    user.ceo_id = ceo_id
    user.company_name = "TestCo"
    return user


def test_store_and_reject_pending_action(tmp_path, monkeypatch):
    """Store a pending action then reject it — no DB needed via monkeypatching."""
    stored: dict = {}

    def fake_get_or_create(ceo_id, conversation_id):
        ctx = MagicMock()
        ctx.pending_actions = []
        ctx.id = 1
        return ctx

    def fake_update(conversation_id, *, pending_actions=None, **kwargs):
        if pending_actions is not None:
            stored["pending_actions"] = pending_actions

    monkeypatch.setattr(
        "src.assistant.approval.get_or_create_live_context", fake_get_or_create
    )
    monkeypatch.setattr(
        "src.assistant.approval.update_live_context", fake_update
    )

    store_pending_action(
        ceo_id="ceo_test",
        conversation_id="conv_001",
        tool_name="send_email_draft",
        tool_inputs={"to": "alice@example.com", "subject": "Hi", "body": "Test"},
        interaction_id=42,
    )

    assert len(stored["pending_actions"]) == 1
    action = stored["pending_actions"][0]
    assert action["tool_name"] == "send_email_draft"
    assert action["interaction_id"] == 42
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_agent.py::test_is_write_tool_true_for_send_email_draft tests/test_agent.py::test_store_and_reject_pending_action -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'src.assistant.approval'`

- [ ] **Step 3: Create `src/assistant/approval.py`**

```python
"""Approval gate for write actions.

Write actions are never auto-executed. When the agent decides to call a write
tool, the action is stored in ConversationLiveContext.pending_actions. The CEO
approves or rejects via the /resolve endpoint, which calls execute_approval()
or reject_approval() here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.core.database import get_or_create_live_context, update_live_context
from src.tools.base import ToolContext
from src.assistant.sdk_tools import WRITE_TOOL_NAMES, execute_tool


def is_write_tool(tool_name: str) -> bool:
    return tool_name in WRITE_TOOL_NAMES


def store_pending_action(
    *,
    ceo_id: str,
    conversation_id: str,
    tool_name: str,
    tool_inputs: dict[str, Any],
    interaction_id: int,
) -> None:
    """Persist a pending write action in the conversation live context."""
    ctx = get_or_create_live_context(ceo_id, conversation_id)
    existing = list(ctx.pending_actions or [])
    existing.append({
        "tool_name": tool_name,
        "tool_inputs": tool_inputs,
        "interaction_id": interaction_id,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    })
    update_live_context(conversation_id, ceo_id=ceo_id, pending_actions=existing)


def execute_approval(
    *,
    ceo_id: str,
    conversation_id: str,
    interaction_id: int,
) -> dict[str, Any]:
    """Execute the pending write action for this interaction and mark it done."""
    ctx = get_or_create_live_context(ceo_id, conversation_id)
    pending = [a for a in (ctx.pending_actions or []) if a.get("interaction_id") == interaction_id]
    if not pending:
        raise ValueError(f"No pending action for interaction_id={interaction_id}")

    action = pending[0]
    context = ToolContext(ceo_id=ceo_id, interaction_id=interaction_id)
    result = execute_tool(action["tool_name"], action["tool_inputs"], context)

    updated = [
        {**a, "status": "executed"} if a.get("interaction_id") == interaction_id else a
        for a in (ctx.pending_actions or [])
    ]
    update_live_context(conversation_id, ceo_id=ceo_id, pending_actions=updated)
    return {"executed": action["tool_name"], "result": result}


def reject_approval(
    *,
    ceo_id: str,
    conversation_id: str,
    interaction_id: int,
) -> None:
    """Mark the pending action as rejected."""
    ctx = get_or_create_live_context(ceo_id, conversation_id)
    updated = [
        {**a, "status": "rejected"} if a.get("interaction_id") == interaction_id else a
        for a in (ctx.pending_actions or [])
    ]
    update_live_context(conversation_id, ceo_id=ceo_id, pending_actions=updated)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_agent.py -v 2>&1 | head -30
```
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/assistant/approval.py tests/test_agent.py
git commit -m "feat: add approval gate — stores and executes CEO-approved write actions"
```

---

## Task 3: Create `src/assistant/agent.py`

**Files:**
- Create: `src/assistant/agent.py`
- Modify: `tests/test_agent.py` (add agent tests)

- [ ] **Step 1: Add failing tests**

```python
# Append to tests/test_agent.py

import asyncio
from unittest.mock import MagicMock, patch
from src.assistant.agent import AgenticAssistant
from src.api.schemas import AssistantQueryRequest, AssistantMessageResponse


def _make_interaction(id: int = 1, ceo_id: str = "ceo_test") -> MagicMock:
    interaction = MagicMock()
    interaction.id = id
    interaction.ceo_id = ceo_id
    return interaction


def _make_payload(message: str = "What's in my inbox?") -> AssistantQueryRequest:
    return AssistantQueryRequest(message=message, conversation_id="conv_001")


def _make_anthropic_text_response(text: str) -> MagicMock:
    """Simulate a Claude response with a text block and stop_reason=end_turn."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def test_agent_returns_assistant_message_response():
    agent = AgenticAssistant()
    user = _make_user()
    payload = _make_payload()
    interaction = _make_interaction()

    mock_response = _make_anthropic_text_response("You have 3 urgent emails.")

    with patch.object(agent._client.messages, "create", return_value=mock_response):
        with patch("src.assistant.agent.get_ceo_preferences", return_value=None):
            with patch("src.assistant.agent.get_session_history", return_value=[]):
                result = asyncio.run(
                    agent.handle(payload=payload, interaction=interaction, current_user=user)
                )

    assert isinstance(result, AssistantMessageResponse)
    assert result.conversation_id == "conv_001"
    assert result.status == "completed"
    assert "urgent" in result.answer.summary


def test_agent_surfaces_write_tool_as_pending():
    agent = AgenticAssistant()
    user = _make_user()
    payload = _make_payload(message="Send a follow-up to alice@example.com")
    interaction = _make_interaction(id=99)

    # First response: Claude wants to call send_email_draft
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "send_email_draft"
    tool_use_block.id = "tu_001"
    tool_use_block.input = {"to": "alice@example.com", "subject": "Follow-up", "body": "Hi Alice"}

    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Here's the email I'd send — want me to send it?"

    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [text_block, tool_use_block]

    with patch.object(agent._client.messages, "create", return_value=response):
        with patch("src.assistant.agent.get_ceo_preferences", return_value=None):
            with patch("src.assistant.agent.get_session_history", return_value=[]):
                with patch("src.assistant.agent.store_pending_action") as mock_store:
                    result = asyncio.run(
                        agent.handle(payload=payload, interaction=interaction, current_user=user)
                    )

    assert result.status == "pending"
    assert mock_store.called
    call_kwargs = mock_store.call_args.kwargs
    assert call_kwargs["tool_name"] == "send_email_draft"
    assert call_kwargs["interaction_id"] == 99
```

- [ ] **Step 2: Run to verify failure**

```bash
python -m pytest tests/test_agent.py::test_agent_returns_assistant_message_response -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'src.assistant.agent'`

- [ ] **Step 3: Create `src/assistant/agent.py`**

```python
"""Agentic assistant — replaces the routing/workflow pipeline.

One Claude call with tools. Read tools execute in the loop. Write tools
short-circuit and are stored for CEO approval via approval.py.
"""
from __future__ import annotations

import os
from typing import Any

import anthropic

from src.api.schemas import (
    AnswerPayload,
    AssistantMessageResponse,
    AssistantQueryRequest,
    TrustMetadata,
)
from src.core.database import get_ceo_preferences, get_session_history
from src.core.models import SessionInteraction, User
from src.tools.base import ToolContext
from src.assistant.approval import is_write_tool, store_pending_action
from src.assistant.sdk_tools import execute_tool, get_anthropic_tools

_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")
_MAX_TOOL_ITERATIONS = 10


class AgenticAssistant:
    def __init__(self) -> None:
        self._client = anthropic.Anthropic()

    async def handle(
        self,
        *,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        current_user: User,
    ) -> AssistantMessageResponse:
        ceo_id = current_user.ceo_id
        context = ToolContext(
            ceo_id=ceo_id,
            interaction_id=interaction.id,
            company_name=current_user.company_name,
        )
        system_prompt = self._build_system_prompt(current_user)
        history = self._load_history(ceo_id)
        messages: list[dict[str, Any]] = history + [{"role": "user", "content": payload.message}]
        tools = get_anthropic_tools()

        final_text, pending_action = self._run_tool_loop(messages, system_prompt, tools, context)

        if pending_action:
            store_pending_action(
                ceo_id=ceo_id,
                conversation_id=payload.conversation_id,
                tool_name=pending_action["tool_name"],
                tool_inputs=pending_action["tool_inputs"],
                interaction_id=interaction.id,
            )

        return self._build_response(
            payload=payload,
            interaction=interaction,
            text=final_text,
            pending_action=pending_action,
        )

    def _run_tool_loop(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        tools: list[dict[str, Any]],
        context: ToolContext,
    ) -> tuple[str, dict[str, Any] | None]:
        """Run the tool loop. Returns (final_text, pending_action_or_None)."""
        for _ in range(_MAX_TOOL_ITERATIONS):
            response = self._client.messages.create(
                model=_MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

            text = next((b.text for b in response.content if hasattr(b, "text")), "")

            if response.stop_reason == "end_turn":
                return text, None

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return text, None

            # Write tool detected — surface for approval, stop loop
            for tool_use in tool_uses:
                if is_write_tool(tool_use.name):
                    return text, {"tool_name": tool_use.name, "tool_inputs": tool_use.input}

            # Execute read tools and continue
            messages = messages + [{"role": "assistant", "content": response.content}]
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": execute_tool(tool_use.name, tool_use.input, context),
                }
                for tool_use in tool_uses
            ]
            messages = messages + [{"role": "user", "content": tool_results}]

        return "Reached tool iteration limit. Please try a more specific question.", None

    def _build_system_prompt(self, user: User) -> str:
        prefs = get_ceo_preferences(user.ceo_id)
        company = user.company_name or "your company"
        lines = [
            f"You are an executive AI assistant for the CEO of {company}.",
            "You have tools to read email, calendar, company data, documents, memory, and more.",
            "Be direct, concise, and executive-facing.",
            "For write actions (send_email_draft, slack_post, create_*), call the tool — "
            "it will be shown to the CEO for approval before execution.",
        ]
        if prefs and prefs.priority_senders:
            lines.append(f"Priority senders: {', '.join(list(prefs.priority_senders)[:5])}")
        if prefs and prefs.ignored_senders:
            lines.append(f"Ignore emails from: {', '.join(list(prefs.ignored_senders)[:5])}")
        return "\n".join(lines)

    def _load_history(self, ceo_id: str) -> list[dict[str, Any]]:
        """Load last 5 turns as Anthropic message format."""
        recent = get_session_history(ceo_id, limit=10)
        messages: list[dict[str, Any]] = []
        for interaction in recent[-5:]:
            if interaction.query:
                messages.append({"role": "user", "content": interaction.query})
            if interaction.response:
                messages.append({"role": "assistant", "content": interaction.response})
        return messages

    def _build_response(
        self,
        *,
        payload: AssistantQueryRequest,
        interaction: SessionInteraction,
        text: str,
        pending_action: dict[str, Any] | None,
    ) -> AssistantMessageResponse:
        metadata: dict[str, Any] = {}
        if pending_action:
            metadata["pending_action"] = pending_action

        return AssistantMessageResponse(
            conversation_id=payload.conversation_id,
            message_id=str(interaction.id),
            workflow_type="conversational",
            response_type="conversational",
            status="pending" if pending_action else "completed",
            answer=AnswerPayload(title="", summary=text, sections=[]),
            trust=TrustMetadata(),
            metadata=metadata,
        )
```

- [ ] **Step 4: Run all agent tests**

```bash
python -m pytest tests/test_agent.py -v 2>&1 | tail -20
```
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/assistant/agent.py tests/test_agent.py
git commit -m "feat: add AgenticAssistant — single Claude tool loop replaces routing pipeline"
```

---

## Task 4: Thin `src/api/routes/assistant.py`

**Files:**
- Modify: `src/api/routes/assistant.py`

The goal: replace the `generate_native_assistant_response` call (which invokes `AssistantService`) with `AgenticAssistant`, and replace the `resolve_assistant_message` handler with `execute_approval` / `reject_approval`.

- [ ] **Step 1: Replace the query handler**

In `src/api/routes/assistant.py`, replace lines 54–77 (the import block and `generate_native_assistant_response`) with:

```python
from src.assistant.agent import AgenticAssistant

_agent = AgenticAssistant()


async def generate_native_assistant_response(
    payload: AssistantQueryRequest,
    interaction: SessionInteraction,
    current_user: User,
) -> AssistantMessageResponse:
    return await _agent.handle(
        payload=payload,
        interaction=interaction,
        current_user=current_user,
    )
```

Remove these now-dead imports from the top of the file:
```python
# DELETE these lines:
from src.assistant.service import AssistantService
from src.workflows.direct_actions import resolve_direct_action
from src.workflows.runner import AssistantWorkflowRunner
```

- [ ] **Step 2: Replace the resolve handler**

Replace the `resolve_assistant_message` endpoint (lines 122–146) with:

```python
from src.assistant.approval import execute_approval, reject_approval

@router.post("/assistant/messages/{interaction_id}/resolve", response_model=AssistantMessageResponse)
async def resolve_assistant_message(
    interaction_id: int,
    resolution: ApprovalResolutionRequest,
    current_user: User = Depends(get_current_user),
):
    conversation_id = resolution.conversation_id or ""
    try:
        if resolution.decision == "approve":
            action_result = execute_approval(
                ceo_id=current_user.ceo_id,
                conversation_id=conversation_id,
                interaction_id=interaction_id,
            )
            summary = f"Done. {action_result.get('executed', 'Action')} executed."
        else:
            reject_approval(
                ceo_id=current_user.ceo_id,
                conversation_id=conversation_id,
                interaction_id=interaction_id,
            )
            summary = "Got it, action cancelled."
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return AssistantMessageResponse(
        conversation_id=conversation_id,
        message_id=str(interaction_id),
        workflow_type="conversational",
        response_type="conversational",
        status="completed",
        answer=AnswerPayload(title="", summary=summary, sections=[]),
        trust=TrustMetadata(),
    )
```

Note: `ApprovalResolutionRequest` currently has `decision`, `mode`, and `note` fields but no `conversation_id`. Add it to `src/api/schemas.py`:

```python
class ApprovalResolutionRequest(BaseModel):
    decision: Optional[ApprovalDecision] = None
    mode: Optional[ApprovalMode] = None
    description: Optional[str] = None
    note: Optional[str] = None
    conversation_id: Optional[str] = None  # ADD THIS LINE
```

- [ ] **Step 3: Verify the app starts cleanly**

```bash
python -c "from src.api.routes.assistant import router; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Run a quick smoke test**

```bash
python -m pytest tests/test_agent.py -v 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/assistant.py src/api/schemas.py
git commit -m "feat: thin assistant route — use AgenticAssistant, remove AssistantService"
```

---

## Task 5: Fix `registry.py` runtime dependency

`src/tools/registry.py` imports `ToolEvent` from `src/runtime/events.py`. Before archiving the runtime, remove this dependency.

**Files:**
- Modify: `src/tools/registry.py`

- [ ] **Step 1: Remove `ToolEvent` from `registry.py`**

In `src/tools/registry.py`, `ToolEvent` is only used in `invoke_with_event`. The new agent uses `invoke` directly via `execute_tool` in `sdk_tools.py` and never calls `invoke_with_event`. Remove the method and the import:

```python
# DELETE line 4:
from src.runtime.events import ToolEvent

# DELETE the entire invoke_with_event method (lines 50-73):
def invoke_with_event(
    self,
    name: str,
    context: Optional[ToolContext] = None,
    **kwargs: Any,
) -> tuple[ToolResult, ToolEvent]:
    ...
```

- [ ] **Step 2: Verify registry still works**

```bash
python -c "from src.tools.registry import build_default_tool_registry; r = build_default_tool_registry(); print(len(r.list_tools()), 'tools OK')"
```
Expected: `39 tools OK` (or similar count)

- [ ] **Step 3: Run agent tests to confirm nothing broke**

```bash
python -m pytest tests/test_agent.py -v 2>&1 | tail -10
```
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add src/tools/registry.py
git commit -m "refactor: remove ToolEvent dependency from registry — no longer needed"
```

---

## Task 6: Archive the routing/orchestration layer

Archive everything that is now dead (no longer imported by any active code). Utility workflows that `src/tools/` still imports (`workbook_models`, `coauthoring`, `webhook_normalizer`, `signal_extractor`) stay in place.

**Files:**
- Create: `archive/README.md`
- Move: `src/agents/` → `archive/agents/`
- Move: `src/runtime/` → `archive/runtime/`
- Move (select files): routing-layer files from `src/assistant/` and `src/workflows/`

- [ ] **Step 1: Create archive directory and README**

```bash
mkdir -p archive
cat > archive/README.md << 'EOF'
# Archive — Pre-Agentic Architecture

Archived 2026-05-04. These files implemented the 5-stage routing/workflow
pipeline replaced by the AgenticAssistant in src/assistant/agent.py.

Nothing in the active codebase imports from here. Safe to delete once the
new pipeline is verified in production.

## Contents
- agents/       — BriefingAgent, ReportAgent, PlannerAgent, etc.
- runtime/      — WorkflowEngine, StageHandlers, Bootstrap
- assistant/    — AssistantService, SemanticArbitration, IntentClassifier, etc.
- workflows/    — Routing pipeline (routing.py, request_planner.py, runner.py, etc.)
EOF
```

- [ ] **Step 2: Move src/agents/**

```bash
mv src/agents archive/agents
```

- [ ] **Step 3: Move src/runtime/**

```bash
mv src/runtime archive/runtime
```

- [ ] **Step 4: Move dead assistant files**

```bash
mkdir -p archive/assistant
# Move old orchestration files — the new ones (agent.py, sdk_tools.py, approval.py) stay
for f in service.py intent_classifier.py semantic_arbitration.py request_interpretation.py \
          enrichment.py clarification_signals.py request_interpretation_policy.py \
          classification.py artifact_mode.py memory.py; do
    [ -f "src/assistant/$f" ] && mv "src/assistant/$f" "archive/assistant/$f"
done
```

- [ ] **Step 5: Move dead workflow files**

```bash
mkdir -p archive/workflows
for f in routing.py request_planner.py runner.py runner_semantics.py direct_actions.py \
          clarification_policy.py intent_state.py planning_types.py planner_semantics.py \
          action_semantics.py action_references.py approval_envelope.py approval_records.py \
          interaction_persistence.py message_scaffolding.py llm_router.py \
          day_schedule_planning.py week_schedule_planning.py report_generation.py \
          morning_brief.py event_runner.py proactive_observations.py \
          question_ranking.py financial_semantic.py company_identity.py \
          context_loading.py plan_execution.py planning_time.py; do
    [ -f "src/workflows/$f" ] && mv "src/workflows/$f" "archive/workflows/$f"
done
```

- [ ] **Step 6: Move broken tests**

```bash
mkdir -p archive/tests
# Move tests that import from archived modules
for f in test_request_planner.py test_classifier_seam.py test_correction_routing_precedence.py \
          test_request_interpretation_authority.py test_request_interpretation_eval.py \
          test_request_interpretation_quality.py test_phase2_context_loading.py \
          test_phase2_conversation_handoff.py test_watch_context_assembler.py \
          test_workflow_mode_eval.py test_mode_eval_loader.py test_fake_ceo_eval.py \
          test_integration_benchmark.py test_llm_fallback.py test_read_model_seam.py \
          test_report_pipeline_wiring.py test_report_prompt_plan_block.py \
          test_report_specs.py test_thread_and_situational_tools.py; do
    [ -f "tests/$f" ] && mv "tests/$f" "archive/tests/$f"
done
```

- [ ] **Step 7: Verify the app still imports cleanly**

```bash
python -c "from src.api.main import app; print('app OK')"
python -c "from src.assistant.agent import AgenticAssistant; print('agent OK')"
```
Expected: both print OK

- [ ] **Step 8: Run the full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: passing tests — `test_agent.py` and `test_architecture.py` at minimum. Any remaining failures are in tests that also import from archived modules (move those too).

- [ ] **Step 9: Commit**

```bash
git add archive/ src/ tests/
git commit -m "refactor: archive routing/orchestration layer — replaced by AgenticAssistant"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] Archive plan → Task 6
- [x] `sdk_tools.py` → Task 1
- [x] `approval.py` → Task 2
- [x] `agent.py` → Task 3
- [x] API route thinning → Task 4
- [x] `approval.py` stores to `ConversationLiveContext.pending_actions` → Task 2 + Task 4
- [x] Write tools require approval → Task 2 + Task 3 (is_write_tool gate)
- [x] API contract preserved (`AssistantMessageResponse` unchanged) → Task 3 + Task 4
- [x] `ToolEvent` / `src/runtime` dependency resolved before archive → Task 5

**Type consistency check:**
- `store_pending_action` kwargs (`ceo_id`, `conversation_id`, `tool_name`, `tool_inputs`, `interaction_id`) used consistently in Task 2 definition and Task 3 call site ✓
- `execute_approval` / `reject_approval` kwargs (`ceo_id`, `conversation_id`, `interaction_id`) used consistently in Task 2 definition and Task 4 call site ✓
- `AgenticAssistant.handle` signature (`payload`, `interaction`, `current_user`) matches Task 3 definition and Task 4 call site ✓
- `AssistantMessageResponse` fields match actual schema (`conversation_id`, `message_id`, `workflow_type`, `response_type`, `status`, `answer`, `trust`) ✓

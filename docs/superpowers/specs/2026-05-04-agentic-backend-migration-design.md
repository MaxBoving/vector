# Agentic Backend Migration — Design Spec
**Date:** 2026-05-04
**Status:** Approved

---

## Problem

The current backend routes each CEO request through 5 layers of classification
(intent_state → request_planner → classify_route → routing → workflow engine)
before a single LLM call generates an answer. The routing machinery is the
primary source of bugs. Most engineering time goes into fixing routing, not
improving answer quality.

---

## Goal

Replace the routing/workflow pipeline with a single agentic loop:
**message → Claude with tools → response.**

- 1–2 LLM calls per request (down from 5+ stages)
- Claude decides what context to fetch — no hardcoded workflow types
- Write actions always require CEO approval — never auto-executed
- API contract with the frontend preserved (same response envelope)
- Old code archived, not deleted

---

## Scope

**Backend only.** Frontend is unchanged for now. API contract changes only
if strictly required. Approval UI wiring to the new flow is a follow-up.

---

## Architecture

### Request flow

```
POST /api/assistant
  → AgenticAssistant.handle(message, history, ceo_id)
      → load_context(ceo_id)         # company state + preferences from DB
      → anthropic.messages.create(   # one call; tool loop runs inside
            system=system_prompt,
            tools=sdk_tools,
            messages=history + [user_message]
        )
          ↳ tool_use (read)?  → execute tool → feed result back → continue
          ↳ tool_use (write)? → store draft → return ApprovalPendingResponse
          ↳ text?             → done
      → format_response(text, context)
  → AssistantMessageResponse          # existing envelope, unchanged
```

### LLM call budget

| Scenario | Calls |
|---|---|
| Conversational answer | 1 |
| Answer requiring context (email/calendar fetch) | 1 (tools don't count) |
| Structured artifact (docx/pptx) | 2 (agent loop + render pass) |
| Write action (email/calendar) | 1 (agent decides) + 0 (approval deferred) |

---

## New Files (3)

### `src/assistant/agent.py` (~200 lines)
The new core. Owns the tool loop and approval short-circuit.

Responsibilities:
- Load context (company state, preferences, conversation history)
- Build system prompt from context
- Run Claude tool loop
- Detect write tools → return `ApprovalPendingResponse` instead of executing
- Return `AssistantMessageResponse` in existing envelope shape

### `src/assistant/sdk_tools.py` (~100 lines)
Adapts existing `BaseTool` classes to Anthropic tool definitions.

```python
def to_anthropic_tool(tool: BaseTool) -> dict:
    return {
        "name": tool.metadata.name,
        "description": tool.metadata.description,
        "input_schema": tool.metadata.input_schema,
    }

def execute_tool(name: str, inputs: dict, context: ToolContext) -> str:
    tool = REGISTRY.get(name)
    result = tool.invoke(context, **inputs)
    return json.dumps(result.data) if result.success else f"Error: {result.error}"
```

No changes to `src/tools/`. Every existing tool implementation is reused as-is.

### `src/assistant/approval.py` (~80 lines)
Approval gate — replaces `direct_actions.py` (1,173 lines).

Responsibilities:
- Define `WRITE_TOOLS` set
- Store pending action draft to DB with `interaction_id`
- `execute_approval(interaction_id)` — loads draft, calls provider
- `reject_approval(interaction_id)` — logs rejection to memory

---

## Tool Exposure

| Tool | Type | Approval |
|---|---|---|
| `email_read` | read | no |
| `calendar_read` | read | no |
| `memory_read` | read | no |
| `document_read` | read | no |
| `send_email` | write | yes |
| `create_calendar_event` | write | yes |
| `draft_email` | write | yes |

---

## Approval Flow

```
Agent returns tool_use: send_email { to, subject, body }
  → agent detects write tool
  → stores draft in DB: { interaction_id, action_type, payload }
  → returns to CEO: "Here's the email I'd send — want me to send it?"
     + draft preview in existing approval envelope

CEO approves:
  POST /api/assistant/approve?interaction_id=<uuid>
  → load draft from DB → providers.send_email(draft) → confirm

CEO rejects:
  POST /api/assistant/reject?interaction_id=<uuid>
  → log rejection → "Got it, won't send"
```

Frontend approval UI wiring is a follow-up task — backend contract is defined
here, frontend integration comes after the backend is verified working.

---

## Archive Plan

Move before writing any new code:

```
archive/
  agents/      ← src/agents/
  workflows/   ← src/workflows/
  runtime/     ← src/runtime/
  assistant/   ← src/assistant/ (current contents)
  tests/       ← tests/ that import archived modules
  README.md
```

Stays in `src/`:
```
src/
  api/           (thin down assistant route to one AgenticAssistant call)
  core/          (db, models, llm, knowledge — untouched)
  tools/         (all tool implementations — untouched)
  integrations/  (providers, email_intelligence, crm, slack — untouched)
  presentation/  (artifact rendering — untouched)
  finance/       (untouched)
  assistant/     (new: agent.py, sdk_tools.py, approval.py only)
```

---

## What Gets Deleted

Nothing is deleted. Everything moves to `archive/`. Remove `archive/` only
after the new system is verified in production.

---

## Tests

- Existing tests that import from archived modules → move to `archive/tests/`
- New: `tests/test_agent.py` — covers the agent loop, tool execution, approval gate
- `tests/test_architecture.py` — remains, now enforces new smaller file limits

---

## Out of Scope

- Frontend approval UI wiring
- MCP server (FastMCP) — can be added later if external clients need the tools
- New tool implementations — existing tools are reused
- Changes to `src/core/`, `src/tools/`, `src/integrations/`, `src/presentation/`

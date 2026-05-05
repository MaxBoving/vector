"""Live query trace -- runs real CEO queries through AgenticAssistant and records
every tool Claude decides to call, in order, with its inputs and the raw result.

Usage:
    python scripts/query_trace.py
    python scripts/query_trace.py --ceo marcus_webb_ceo
    python scripts/query_trace.py --ceo marcus_webb_ceo "What's in my pipeline?"

No seeding, no mocking, no conditional patching. Tools execute for real and
return whatever they actually return (empty data, "no account connected", etc.).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
from datetime import datetime
from typing import Any
from unittest.mock import patch

from dotenv import load_dotenv
load_dotenv(_root / ".env")


class _FakeUser:
    ceo_id = "trace-ceo-001"
    company_name = "Acme Corp"
    username = "trace"
    hashed_password = ""
    id = None


class _FakeInteraction:
    id = 9999
    ceo_id = "trace-ceo-001"
    query = ""
    response = None
    status = "PENDING"
    timestamp = datetime.now().isoformat()
    last_updated = datetime.now().isoformat()
    intent = None
    current_stage = None
    gate_type = None


def _load_user(ceo_id: str) -> Any:
    """Try to load real User from DB; fall back to FakeUser."""
    try:
        from src.core.database import engine
        from src.core.models import User
        from sqlmodel import Session, select
        with Session(engine) as session:
            user = session.exec(select(User).where(User.ceo_id == ceo_id)).first()
            if user:
                return user
    except Exception:
        pass
    fake = _FakeUser()
    fake.ceo_id = ceo_id
    return fake


DEFAULT_QUERIES = [
    "What's in my inbox today?",
    "What meetings do I have this week?",
    "Schedule a call with Sarah Chen tomorrow at 2pm EST for 30 minutes.",
    "Remember that we're targeting Series B in Q3 2026.",
    "What's our current pipeline look like and who are the key deals?",
    "Draft a Slack message to the engineering team about the Q2 roadmap review.",
    "Give me a quick morning brief -- what should I focus on today?",
    "What do you know about Sequoia Capital?",
]

MARCUS_QUERIES = [
    "What's in my inbox today?",
    "What meetings do I have this week?",
    "What's our current sales pipeline? Show me deal names, stages, and amounts.",
    "Who are the key contacts on the Northstar Health deal?",
    "Give me a morning brief -- what are my top priorities today?",
    "What commitments have I made to Northstar Health?",
    "What's our ARR and burn rate?",
    "What do I know about Horizon Ventures?",
]


_call_log: list[dict[str, Any]] = []

def _traced_execute_tool(name: str, inputs: dict[str, Any], context: Any) -> str:
    from src.assistant import sdk_tools as _sdk
    result = _sdk._real_execute_tool(name, inputs, context)
    _call_log.append({
        "tool": name,
        "inputs": inputs,
        "result_preview": result[:200] if isinstance(result, str) else str(result)[:200],
    })
    return result


async def trace_query(query: str, agent: Any, user: Any) -> dict[str, Any]:
    _call_log.clear()

    from src.api.schemas import AssistantQueryRequest

    payload = AssistantQueryRequest(message=query, conversation_id="trace-conv-001")
    interaction = _FakeInteraction()
    interaction.query = query
    interaction.ceo_id = user.ceo_id

    pending_action = None
    answer_payload = None
    try:
        result = await agent.handle(payload=payload, interaction=interaction, current_user=user)
        answer_payload = result.answer
        pending_action = result.metadata.get("pending_action")
    except Exception as exc:
        answer_payload = None
        pending_action = None
        print(f"[ERROR: {exc}]")

    return {
        "query": query,
        "tools_called": list(_call_log),
        "answer": answer_payload,
        "pending_action": pending_action,
    }


def print_trace(trace: dict[str, Any], index: int) -> None:
    sep = "-" * 72
    print(f"\n{sep}")
    print(f"Query {index + 1}: {trace['query']}")
    print(sep)

    tools = trace["tools_called"]
    if tools:
        print(f"Tools called ({len(tools)}):")
        for i, call in enumerate(tools, 1):
            print(f"  {i}. {call['tool']}")
            if call["inputs"]:
                inputs_str = json.dumps(call["inputs"])
                print(f"     inputs: {textwrap.shorten(inputs_str, 80)}")
            print(f"     result: {textwrap.shorten(call['result_preview'], 100)}")
    else:
        print("Tools called: none")

    if trace.get("pending_action"):
        pa = trace["pending_action"]
        print(f"\nPending approval: {pa.get('tool_name')} -- {json.dumps(pa.get('tool_inputs', {}))}")

    answer = trace.get("answer")
    if answer:
        print(f"\nTitle:   {answer.title or '(none)'}")
        print(f"Summary: {answer.summary}")
        if answer.sections:
            print("Sections:")
            for s in answer.sections:
                print(f"  [{s.label}] {s.content or ''}")
                for item in s.items:
                    print(f"    - {item}")
    else:
        print("\n(no answer)")


async def main() -> None:
    parser = argparse.ArgumentParser(description="agenticMIND live query trace")
    parser.add_argument("--ceo", default="trace-ceo-001", help="CEO ID to use (e.g. marcus_webb_ceo)")
    parser.add_argument("query", nargs="?", help="Single query to run (optional)")
    args = parser.parse_args()

    user = _load_user(args.ceo)
    company_name = getattr(user, "company_name", "Acme Corp")

    if args.query:
        queries = [args.query]
    elif args.ceo == "marcus_webb_ceo":
        queries = MARCUS_QUERIES
    else:
        queries = DEFAULT_QUERIES

    import src.assistant.sdk_tools as sdk_module
    sdk_module._real_execute_tool = sdk_module.execute_tool

    with patch.object(sdk_module, "execute_tool", side_effect=_traced_execute_tool):
        from src.assistant.agent import AgenticAssistant
        agent = AgenticAssistant()

        print(f"agenticMIND -- live query trace")
        print(f"CEO: {user.ceo_id} | Company: {company_name}")
        print(f"Queries: {len(queries)}\n")

        results = []
        for i, query in enumerate(queries):
            print(f"Running query {i + 1}/{len(queries)}: {query[:60]}...", end="\r", flush=True)
            trace = await trace_query(query, agent, user)
            results.append(trace)
            print_trace(trace, i)

        print(f"\n{'-' * 72}")
        print("SUMMARY")
        print(f"{'-' * 72}")
        print(f"{'Query':<45} {'Tools':<30}")
        print(f"{'-' * 45} {'-' * 30}")
        for r in results:
            tool_names = ", ".join(c["tool"] for c in r["tools_called"]) or "none"
            q_short = textwrap.shorten(r["query"], 44)
            ans = r.get("answer")
            summary_short = textwrap.shorten(ans.summary if ans else "", 40)
            print(f"{q_short:<45} {textwrap.shorten(tool_names, 28):<30} {summary_short}")


if __name__ == "__main__":
    asyncio.run(main())

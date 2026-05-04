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

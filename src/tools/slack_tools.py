"""Slack tools — read channels/DMs and post messages.

Requires SLACK_BOT_TOKEN in the environment (set once, no OAuth per user).
"""
from __future__ import annotations

import os
from typing import Any

from src.integrations.slack import (
    SlackIntegrationError,
    fetch_slack_channels,
    fetch_slack_dms,
    fetch_slack_messages,
    post_slack_message,
    resolve_user,
)

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult

_MAX_MESSAGES = 50
_MAX_CHANNELS = 100
from src.tools.demo_config import DEV_DEMO_MODE, demo_lookup_id, load_fixture


def _get_demo_slack(ceo_id: str) -> dict | None:
    if not DEV_DEMO_MODE:
        return None
    data = load_fixture("slack_messages")
    return data if data else None


class SlackReadTool(BaseTool):
    metadata = ToolMetadata(
        name="slack_read",
        description=(
            "Read Slack channels, DMs, and message history. "
            "Actions: list_channels | list_dms | read_messages. "
            "Requires SLACK_BOT_TOKEN to be configured."
        ),
        read_only=True,
        side_effects=False,
        tags=["connector", "slack", "read"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "list_channels").strip()
        ceo_id = context.ceo_id or ""

        # Demo fallback — no SLACK_BOT_TOKEN needed
        demo = _get_demo_slack(ceo_id)
        if demo is not None:
            return self._invoke_demo(action, kwargs, demo)

        try:
            if action == "list_channels":
                return self._list_channels(kwargs)
            elif action == "list_dms":
                return self._list_dms(kwargs)
            elif action == "read_messages":
                return self._read_messages(kwargs)
            else:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error=f"Unknown action: {action!r}. Valid: list_channels, list_dms, read_messages.",
                )
        except SlackIntegrationError as exc:
            return ToolResult(tool_name=self.metadata.name, success=False, error=str(exc))

    def _invoke_demo(self, action: str, kwargs: dict, demo: dict) -> ToolResult:
        channels = demo.get("channels", [])
        if action == "list_channels":
            limit = min(int(kwargs.get("limit") or 50), _MAX_CHANNELS)
            rows = [{"id": c["id"], "name": c["name"], "topic": c.get("topic", "")} for c in channels[:limit]]
            return ToolResult(tool_name=self.metadata.name, success=True, data={"channels": rows, "count": len(rows)})
        elif action == "list_dms":
            dms = demo.get("dms", [])
            limit = min(int(kwargs.get("limit") or 20), _MAX_CHANNELS)
            return ToolResult(tool_name=self.metadata.name, success=True, data={"dms": dms[:limit], "count": len(dms[:limit])})
        elif action == "read_messages":
            channel_id = str(kwargs.get("channel_id") or "").strip()
            ch = next((c for c in channels if c["id"] == channel_id), None)
            if not ch:
                return ToolResult(tool_name=self.metadata.name, success=False, error=f"Channel '{channel_id}' not found.")
            limit = min(int(kwargs.get("limit") or 20), _MAX_MESSAGES)
            messages = ch.get("messages", [])[:limit]
            return ToolResult(tool_name=self.metadata.name, success=True, data={"channel_id": channel_id, "messages": messages, "count": len(messages)})
        return ToolResult(tool_name=self.metadata.name, success=False, error=f"Unknown action: {action!r}.")

    def _list_channels(self, kwargs: dict) -> ToolResult:
        limit = min(int(kwargs.get("limit") or 50), _MAX_CHANNELS)
        channels = fetch_slack_channels(limit=limit)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"channels": channels, "count": len(channels)},
        )

    def _list_dms(self, kwargs: dict) -> ToolResult:
        limit = min(int(kwargs.get("limit") or 20), _MAX_CHANNELS)
        dms = fetch_slack_dms(limit=limit)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"dms": dms, "count": len(dms)},
        )

    def _read_messages(self, kwargs: dict) -> ToolResult:
        channel_id = str(kwargs.get("channel_id") or "").strip()
        if not channel_id:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="read_messages requires 'channel_id'. Get it from list_channels or list_dms.",
            )
        limit = min(int(kwargs.get("limit") or 20), _MAX_MESSAGES)
        oldest = kwargs.get("oldest")
        resolve_users = bool(kwargs.get("resolve_users", True))

        messages = fetch_slack_messages(channel_id, limit=limit, oldest=oldest)

        if resolve_users:
            user_cache: dict[str, str] = {}
            for msg in messages:
                uid = msg.get("user_id", "")
                if uid and uid not in user_cache:
                    info = resolve_user(uid)
                    user_cache[uid] = info.get("display_name", uid)
                if uid:
                    msg["display_name"] = user_cache.get(uid, uid)

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "channel_id": channel_id,
                "messages": messages,
                "count": len(messages),
            },
        )


class SlackPostTool(BaseTool):
    metadata = ToolMetadata(
        name="slack_post",
        description=(
            "Post a message to a Slack channel or DM thread. "
            "Requires explicit approval (approved=True) before sending. "
            "Requires SLACK_BOT_TOKEN to be configured."
        ),
        read_only=False,
        side_effects=True,
        tags=["connector", "slack", "write"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        if not kwargs.get("approved"):
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error=(
                    "Human approval required before posting to Slack. "
                    "Set approved=True after the CEO confirms the message."
                ),
            )

        channel_id = str(kwargs.get("channel_id") or "").strip()
        text = str(kwargs.get("text") or "").strip()

        if not channel_id or not text:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="slack_post requires 'channel_id' and 'text'.",
            )

        thread_ts = kwargs.get("thread_ts")

        try:
            result = post_slack_message(channel_id, text, thread_ts=thread_ts)
        except SlackIntegrationError as exc:
            return ToolResult(tool_name=self.metadata.name, success=False, error=str(exc))

        if kwargs.get("record_world_event", True):
            from src.workflows.world_simulation import record_world_event

            record_world_event(
                context.ceo_id or "",
                domain="signals",
                event_type="slack_message_posted",
                description="Slack message posted by the assistant.",
                source_ids=[str(context.interaction_id or "")] if context.interaction_id is not None else [],
                payload={
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "ts": result.get("ts"),
                    "message_preview": text[:220],
                    "action": "message_posted",
                },
            )

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "channel_id": channel_id,
                "ts": result.get("ts"),
                "thread_ts": thread_ts,
                "message_preview": text[:200],
                "action": "message_posted",
            },
        )

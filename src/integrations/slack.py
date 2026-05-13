"""Slack integration layer.

Uses a Bot Token (SLACK_BOT_TOKEN env var) — no OAuth flow required.
Install the Slack app to the workspace and paste the bot token into the
environment to activate all Slack tools.

Scopes required on the bot token:
    channels:read, channels:history, groups:read, groups:history,
    im:read, im:history, mpim:read, mpim:history, chat:write, users:read
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SLACK_API_BASE = "https://slack.com/api"


class SlackIntegrationError(RuntimeError):
    pass


def _get_token() -> str:
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not token:
        raise SlackIntegrationError(
            "SLACK_BOT_TOKEN is not set. "
            "Install the agenticMIND Slack app to your workspace and set the bot token."
        )
    return token


def _api_get(method: str, params: dict[str, Any]) -> dict[str, Any]:
    token = _get_token()
    qs = urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{SLACK_API_BASE}/{method}?{qs}"
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(request) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise SlackIntegrationError(f"Slack API request failed ({method}): {exc}") from exc
    if not data.get("ok"):
        raise SlackIntegrationError(f"Slack API error ({method}): {data.get('error', 'unknown')}")
    return data


def _api_post(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = _get_token()
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        f"{SLACK_API_BASE}/{method}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urlopen(request) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise SlackIntegrationError(f"Slack API post failed ({method}): {exc}") from exc
    if not data.get("ok"):
        raise SlackIntegrationError(f"Slack API error ({method}): {data.get('error', 'unknown')}")
    return data


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def fetch_slack_channels(
    *,
    types: str = "public_channel,private_channel",
    limit: int = 50,
    exclude_archived: bool = True,
) -> list[dict[str, Any]]:
    """List accessible Slack channels."""
    data = _api_get(
        "conversations.list",
        {"types": types, "limit": min(limit, 200), "exclude_archived": str(exclude_archived).lower()},
    )
    channels = data.get("channels", [])
    return [
        {
            "channel_id": c.get("id"),
            "name": c.get("name"),
            "is_private": c.get("is_private", False),
            "is_dm": c.get("is_im", False),
            "member_count": c.get("num_members"),
            "topic": (c.get("topic") or {}).get("value", ""),
            "purpose": (c.get("purpose") or {}).get("value", ""),
        }
        for c in channels
    ]


def fetch_slack_messages(
    channel_id: str,
    *,
    limit: int = 20,
    oldest: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Fetch recent messages from a channel or DM."""
    params: dict[str, Any] = {"channel": channel_id, "limit": min(limit, 100)}
    if oldest:
        params["oldest"] = oldest
    data = _api_get("conversations.history", params)
    messages = data.get("messages", [])
    return [
        {
            "ts": m.get("ts"),
            "user_id": m.get("user"),
            "text": m.get("text", ""),
            "thread_ts": m.get("thread_ts"),
            "reply_count": m.get("reply_count", 0),
            "reactions": [r.get("name") for r in (m.get("reactions") or [])],
        }
        for m in messages
        if m.get("type") == "message" and not m.get("subtype")
    ]


def fetch_slack_dms(*, limit: int = 20) -> list[dict[str, Any]]:
    """Fetch recent direct message conversations."""
    return fetch_slack_channels(types="im,mpim", limit=limit, exclude_archived=False)


def resolve_user(user_id: str) -> dict[str, Any]:
    """Resolve a Slack user ID to display name and email."""
    try:
        data = _api_get("users.info", {"user": user_id})
        profile = data.get("user", {}).get("profile", {})
        return {
            "user_id": user_id,
            "display_name": profile.get("display_name") or profile.get("real_name", user_id),
            "email": profile.get("email", ""),
        }
    except SlackIntegrationError:
        return {"user_id": user_id, "display_name": user_id, "email": ""}


def post_slack_message(
    channel_id: str,
    text: str,
    *,
    thread_ts: Optional[str] = None,
    blocks: Optional[list] = None,
) -> dict[str, Any]:
    """Post a message to a channel or thread. Requires approval guard at call site."""
    payload: dict[str, Any] = {"channel": channel_id, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if blocks:
        payload["blocks"] = blocks
    data = _api_post("chat.postMessage", payload)
    return {
        "ts": data.get("ts"),
        "channel": data.get("channel"),
        "message_text": text[:200],
    }

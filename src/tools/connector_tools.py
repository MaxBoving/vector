"""MCP connector layer — wraps Google/Microsoft provider services as BaseTool subclasses.

Each tool calls the appropriate function from src/integrations/providers.py and returns
a ToolResult following the standard invoke(context, **kwargs) → ToolResult contract.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.database import get_connected_account

from src.tools.demo_config import DEV_DEMO_MODE, demo_lookup_id, load_fixture

logger = logging.getLogger(__name__)
from src.integrations.providers import (
    ProviderIntegrationError,
    _fetch_gmail_threads,
    _fetch_google_calendar_events,
    _fetch_outlook_calendar_events,
    _fetch_outlook_threads,
    _get_valid_account,
    create_calendar_write,
    create_email_draft_write,
    get_integration_statuses,
)

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


CONNECTOR_MANIFEST: dict[str, dict] = {
    "gmail": {
        "provides": ["read_threads", "send_draft"],
        "read_only": False,
        "tool_names": ["read_email_threads", "send_email_draft"],
    },
    "google_calendar": {
        "provides": ["read_events"],
        "read_only": True,
        "tool_names": ["read_calendar_events"],
    },
    "outlook_mail": {
        "provides": ["read_threads", "send_draft"],
        "read_only": False,
        "tool_names": ["read_email_threads", "send_email_draft"],
    },
    "outlook_calendar": {
        "provides": ["read_events"],
        "read_only": True,
        "tool_names": ["read_calendar_events"],
    },
}


def _get_demo_email_threads(ceo_id: str) -> list[dict[str, Any]] | None:
    if not DEV_DEMO_MODE:
        return None
    data = load_fixture("gmail_threads")
    threads = data.get("ranked_threads")
    return threads if threads is not None else None


def _get_demo_calendar_events(ceo_id: str) -> list[dict[str, Any]] | None:
    if not DEV_DEMO_MODE:
        return None
    data = load_fixture("gcal_events")
    events = data.get("upcoming_events")
    return events if events is not None else None


class ReadEmailThreadsTool(BaseTool):
    metadata = ToolMetadata(
        name="read_email_threads",
        description="Read recent email threads from the CEO's connected Gmail or Outlook account.",
        read_only=True,
        side_effects=False,
        tags=["connector", "email", "read"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        limit: int = int(kwargs.get("limit") or 10)
        ceo_id = context.ceo_id or ""

        demo_threads = _get_demo_email_threads(ceo_id)
        if demo_threads is not None:
            threads = demo_threads[:limit]
            logger.info("email: demo mode enabled — returning %d seeded threads for %s", len(threads), ceo_id)
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={"threads": threads, "service": "demo_gmail", "count": len(threads)},
            )

        try:
            google_account = _get_valid_account(ceo_id, "google", "gmail")
            if google_account:
                threads = _fetch_gmail_threads(google_account, limit=limit)
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=True,
                    data={"threads": threads, "service": "gmail", "count": len(threads)},
                )

            microsoft_account = _get_valid_account(ceo_id, "microsoft", "outlook_mail")
            if microsoft_account:
                threads = _fetch_outlook_threads(microsoft_account, limit=limit)
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=True,
                    data={"threads": threads, "service": "outlook_mail", "count": len(threads)},
                )
        except ProviderIntegrationError as exc:
            return ToolResult(tool_name=self.metadata.name, success=False, error=str(exc))

        logger.info("email: no account found for ceo_id=%s", ceo_id)
        return ToolResult(
            tool_name=self.metadata.name,
            success=False,
            error="No email account connected. Use /connect to link Gmail or Outlook.",
        )


class ReadCalendarEventsTool(BaseTool):
    metadata = ToolMetadata(
        name="read_calendar_events",
        description="Read upcoming calendar events from the CEO's connected Google Calendar or Outlook Calendar.",
        read_only=True,
        side_effects=False,
        tags=["connector", "calendar", "read"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        max_results: int = int(kwargs.get("max_results") or 20)
        ceo_id = context.ceo_id or ""

        demo_events = _get_demo_calendar_events(ceo_id)
        if demo_events is not None:
            events = demo_events[:max_results]
            logger.info("calendar: demo mode enabled — returning %d seeded events for %s", len(events), ceo_id)
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={"events": events, "service": "demo_calendar", "count": len(events)},
            )

        try:
            google_account = _get_valid_account(ceo_id, "google", "google_calendar")
            if google_account:
                events = _fetch_google_calendar_events(google_account, limit=max_results)
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=True,
                    data={"events": events, "service": "google_calendar", "count": len(events)},
                )

            microsoft_account = _get_valid_account(ceo_id, "microsoft", "outlook_calendar")
            if microsoft_account:
                events = _fetch_outlook_calendar_events(microsoft_account, limit=max_results)
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=True,
                    data={"events": events, "service": "outlook_calendar", "count": len(events)},
                )
        except ProviderIntegrationError as exc:
            return ToolResult(tool_name=self.metadata.name, success=False, error=str(exc))

        logger.info("calendar: no account found for ceo_id=%s", ceo_id)
        return ToolResult(
            tool_name=self.metadata.name,
            success=False,
            error="No calendar account connected. Use /connect to link Google Calendar or Outlook Calendar.",
        )


class SendEmailDraftTool(BaseTool):
    metadata = ToolMetadata(
        name="send_email_draft",
        description="Create an email draft in the CEO's connected Gmail or Outlook account. Requires explicit approval.",
        read_only=False,
        side_effects=True,
        tags=["connector", "email", "write"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        if not kwargs.get("approved"):
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="Human approval required before sending email. Set approved=True after CEO confirms.",
            )

        to = kwargs.get("to")
        subject = kwargs.get("subject")
        body = kwargs.get("body")
        if not to or not subject or not body:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="Missing required fields: 'to', 'subject', and 'body' are required.",
            )

        ceo_id = context.ceo_id or ""
        proposal: dict[str, Any] = {
            "to": to,
            "subject": subject,
            "body": body,
            "cc": kwargs.get("cc") or [],
        }
        if kwargs.get("thread_id"):
            proposal["thread_id"] = kwargs["thread_id"]

        try:
            result = create_email_draft_write(ceo_id, proposal)
        except ProviderIntegrationError as exc:
            return ToolResult(tool_name=self.metadata.name, success=False, error=str(exc))

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "draft_id": result.get("draft_id") or result.get("id"),
                "service": result.get("provider"),
                "action": "draft_created",
            },
        )


class ConnectorStatusTool(BaseTool):
    metadata = ToolMetadata(
        name="get_connector_status",
        description="Return connection status for all Google/Microsoft integrations for the current CEO.",
        read_only=True,
        side_effects=False,
        tags=["connector", "status"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        ceo_id = context.ceo_id or ""
        try:
            connectors = get_integration_statuses(ceo_id)
        except ProviderIntegrationError as exc:
            return ToolResult(tool_name=self.metadata.name, success=False, error=str(exc))

        connected_count = sum(1 for c in connectors if c.get("connected"))
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "connectors": connectors,
                "connected_count": connected_count,
                "total": len(connectors),
            },
        )


class CreateCalendarEventTool(BaseTool):
    metadata = ToolMetadata(
        name="create_calendar_event",
        description="Create a calendar event on the CEO's connected Google Calendar or Outlook Calendar. Requires explicit approval.",
        read_only=False,
        side_effects=True,
        tags=["connector", "calendar", "write"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        title = kwargs.get("title")
        starts_at = kwargs.get("starts_at")
        ends_at = kwargs.get("ends_at")
        if not title or not starts_at or not ends_at:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="Missing required fields: 'title', 'starts_at', and 'ends_at' are required.",
            )

        ceo_id = context.ceo_id or ""
        proposal: dict[str, Any] = {
            "title": title,
            "starts_at": starts_at,
            "ends_at": ends_at,
            "timezone": kwargs.get("timezone", "UTC"),
            "attendees": kwargs.get("attendees") or [],
            "description": kwargs.get("description") or "",
        }

        try:
            result = create_calendar_write(ceo_id, proposal)
        except ProviderIntegrationError as exc:
            return ToolResult(tool_name=self.metadata.name, success=False, error=str(exc))

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "event_id": result.get("event_id"),
                "html_link": result.get("html_link"),
                "title": result.get("title"),
                "starts_at": result.get("starts_at"),
                "service": result.get("provider"),
            },
        )

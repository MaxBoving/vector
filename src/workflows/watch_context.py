"""
WatchContextAssembler — assembles event_payload for watch and plan workflows.

Owns all provider fetch decisions so runner.py stays free of provider coupling.
Each workflow type declares which data sources it needs; the assembler fetches
only what is required and never duplicates calls within a single request.
"""
from __future__ import annotations

from typing import Any, Callable

from src.api.schemas import AssistantQueryRequest
from src.core.models import User
from src.integrations.providers import (
    ProviderIntegrationError,
    async_fetch_email_event,
    fetch_calendar_event,
    fetch_email_event,
)
from src.workflows.event_payloads import build_watch_event_payload
from src.workflows.routing import RouteDecision
from src.workflows.types import WorkflowType

# Workflows that need only email context (raw event passed through as-is)
_EMAIL_ONLY_WORKFLOWS = frozenset(
    {WorkflowType.EMAIL_WATCHER, WorkflowType.EMAIL_INGESTION}
)

# Workflows that need only calendar context (raw event passed through as-is)
_CALENDAR_ONLY_WORKFLOWS = frozenset({WorkflowType.CALENDAR_BRIEFING})

# Compound workflows that require both email + calendar assembled together
_COMPOUND_WORKFLOWS = frozenset(
    {
        WorkflowType.MORNING_BRIEF,
        WorkflowType.SCHEDULE_PLANNING,
        WorkflowType.MEETING_PREP,
        WorkflowType.WEEKLY_RECAP,
    }
)

# Compound workflows that also forward document attachment context
_DOCUMENT_COMPOUND_WORKFLOWS = frozenset(
    {
        WorkflowType.SCHEDULE_PLANNING,
        WorkflowType.MEETING_PREP,
    }
)

# All workflow types handled by this assembler
WATCH_PLAN_WORKFLOWS = _EMAIL_ONLY_WORKFLOWS | _CALENDAR_ONLY_WORKFLOWS | _COMPOUND_WORKFLOWS


class WatchContextAssembler:
    """
    Fetches provider context and builds event_payload for watch and plan workflows.

    The email_fetcher and calendar_fetcher callables default to the real provider
    functions but can be replaced in tests without any module-level patching.
    """

    def __init__(
        self,
        email_fetcher: Callable[[str], dict[str, Any]] | None = None,
        calendar_fetcher: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self._email_fetcher: Callable[[str], dict[str, Any]] = email_fetcher or fetch_email_event
        self._calendar_fetcher: Callable[[str], dict[str, Any]] = calendar_fetcher or fetch_calendar_event

    def build(
        self,
        *,
        workflow_type: str,
        payload: AssistantQueryRequest,
        current_user: User,
        route_decision: RouteDecision,
    ) -> dict[str, Any]:
        """Return the event_payload dict for the given workflow type."""
        if workflow_type in _EMAIL_ONLY_WORKFLOWS:
            return self._safe_email(current_user.ceo_id)

        if workflow_type in _CALENDAR_ONLY_WORKFLOWS:
            return self._safe_calendar(current_user.ceo_id)

        if workflow_type in _COMPOUND_WORKFLOWS:
            email_event = self._safe_email(current_user.ceo_id)
            calendar_event = self._safe_calendar(current_user.ceo_id)
            document_context = (
                _build_document_context(payload)
                if workflow_type in _DOCUMENT_COMPOUND_WORKFLOWS
                else None
            )
            return build_watch_event_payload(
                email_event=email_event,
                calendar_event=calendar_event,
                message=payload.message,
                request_plan=route_decision.request_plan,
                document_context=document_context or None,
                route_decision_payload=route_decision.model_dump(mode="json"),
            )

        raise ValueError(
            f"WatchContextAssembler.build called for non-watch workflow: {workflow_type!r}"
        )

    async def async_build(
        self,
        *,
        workflow_type: str,
        payload: AssistantQueryRequest,
        current_user: User,
        route_decision: RouteDecision,
    ) -> dict[str, Any]:
        """Async variant of build() — uses LLM-enhanced email ranking for email workflows."""
        if workflow_type in _EMAIL_ONLY_WORKFLOWS:
            return await self._safe_email_async(current_user.ceo_id)

        if workflow_type in _CALENDAR_ONLY_WORKFLOWS:
            return self._safe_calendar(current_user.ceo_id)

        if workflow_type in _COMPOUND_WORKFLOWS:
            email_event = await self._safe_email_async(current_user.ceo_id)
            calendar_event = self._safe_calendar(current_user.ceo_id)
            document_context = (
                _build_document_context(payload)
                if workflow_type in _DOCUMENT_COMPOUND_WORKFLOWS
                else None
            )
            return build_watch_event_payload(
                email_event=email_event,
                calendar_event=calendar_event,
                message=payload.message,
                request_plan=route_decision.request_plan,
                document_context=document_context or None,
                route_decision_payload=route_decision.model_dump(mode="json"),
            )

        raise ValueError(
            f"WatchContextAssembler.async_build called for non-watch workflow: {workflow_type!r}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_email(self, ceo_id: str) -> dict[str, Any]:
        try:
            return self._email_fetcher(ceo_id)
        except ProviderIntegrationError:
            return {}

    async def _safe_email_async(self, ceo_id: str) -> dict[str, Any]:
        try:
            return await async_fetch_email_event(ceo_id)
        except ProviderIntegrationError:
            return {}
        except Exception:
            return self._safe_email(ceo_id)

    def _safe_calendar(self, ceo_id: str) -> dict[str, Any]:
        try:
            return self._calendar_fetcher(ceo_id)
        except ProviderIntegrationError:
            return {}


def _build_document_context(payload: AssistantQueryRequest) -> dict[str, Any]:
    attachments = [attachment.model_dump() for attachment in payload.attachments]
    if not attachments:
        return {}
    return {"attachments": attachments, "attachment_count": len(attachments)}

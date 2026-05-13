"""Webhook signal ingester tool — webhook-signal-ingester pattern.

Accepts a raw SaaS webhook payload, normalizes it into a typed ExtractedSignal,
and optionally persists it to the signal store for agent consumption.

Supported sources: stripe, carta, jira, pagerduty, auto (inferred).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from src.workflows.webhook_normalizer import normalize_webhook
from src.workflows.signal_extractor import ExtractedSignal

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult

_VALID_SOURCES = {"stripe", "carta", "jira", "pagerduty", "auto"}


class WebhookSignalIngesterTool(BaseTool):
    metadata = ToolMetadata(
        name="webhook_signal_ingester",
        description=(
            "Normalize an inbound SaaS webhook payload into a typed signal. "
            "Supported sources: stripe, carta, jira, pagerduty (or 'auto' to infer). "
            "Returns urgency, key facts, action hint, and domain classification. "
            "Set persist=True to save to the signal store."
        ),
        read_only=False,
        side_effects=False,
        tags=["webhook", "signal", "ingest", "connector"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        source = str(kwargs.get("source") or "auto").strip().lower()
        payload = kwargs.get("payload")
        persist = bool(kwargs.get("persist", False))

        if not payload or not isinstance(payload, dict):
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="'payload' (dict) is required — pass the raw webhook body.",
            )

        if source not in _VALID_SOURCES:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error=(
                    f"Unknown source: {source!r}. "
                    f"Valid sources: {sorted(_VALID_SOURCES)}."
                ),
            )

        signal: ExtractedSignal = normalize_webhook(source, payload)

        if persist and context.ceo_id:
            _persist_signal(signal, context)

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "signal_id": signal.signal_id,
                "source": signal.source,
                "signal_type": signal.signal_type,
                "urgency": signal.urgency,
                "title": signal.title,
                "summary": signal.summary,
                "entities": signal.entities,
                "key_facts": signal.key_facts,
                "action_required": signal.action_required,
                "action_hint": signal.action_hint,
                "domains": signal.domains,
                "persisted": persist and bool(context.ceo_id),
            },
        )


def _persist_signal(signal: ExtractedSignal, context: ToolContext) -> None:
    """Persist to IncomingSignal store if context has ceo_id."""
    try:
        from src.core.database import save_object
        from src.core.models import IncomingSignal

        record = IncomingSignal(
            ceo_id=context.ceo_id,
            timestamp=datetime.now().isoformat(),
            source=signal.source.capitalize(),
            sender=signal.entities[0] if signal.entities else signal.source,
            subject=signal.title,
            content=signal.summary,
            importance=signal.urgency.upper() if signal.urgency in {"critical", "high"} else "LOW",
            strategic_concepts=signal.domains,
            talking_points=signal.key_facts,
            status="UNREAD",
        )
        save_object(record)
    except Exception:
        # Persistence is best-effort; don't fail the normalization
        pass

"""CRM tools — pipeline deal context for meeting prep and reports.

Supports HubSpot (HUBSPOT_API_TOKEN) and Salesforce
(SALESFORCE_INSTANCE_URL + SALESFORCE_ACCESS_TOKEN).
Falls back to demo data seeded in ConnectedAccount when no CRM env var is set.
The tool auto-detects which CRM is configured.
"""
from __future__ import annotations

import os
from typing import Any

from src.integrations.crm import (
    CRMIntegrationError,
    fetch_crm_deal_contacts,
    fetch_crm_deals,
)

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


def _fetch_demo_deals(ceo_id: str) -> list[dict[str, Any]] | None:
    """Return demo CRM deals from ConnectedAccount if seeded, else None."""
    try:
        from src.core.database import get_connected_account
        account = get_connected_account(ceo_id, "demo", "crm")
        if account and account.provider_metadata:
            return account.provider_metadata.get("deals")
    except Exception:
        pass
    return None


class CRMDealContextTool(BaseTool):
    metadata = ToolMetadata(
        name="crm_deal_context",
        description=(
            "Fetch CRM deal pipeline, stage, amount, close date, and contacts. "
            "action=list_deals: returns all open deals with deal_id, name, stage, amount, account_name. "
            "action=deal_contacts: requires deal_id (from list_deals first) — returns champion, legal, and other key contacts for that specific deal."
        ),
        read_only=True,
        side_effects=False,
        tags=["connector", "crm", "deals", "contacts"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "list_deals").strip()

        try:
            if action == "list_deals":
                return self._list_deals(kwargs, ceo_id=context.ceo_id or "")
            elif action == "deal_contacts":
                return self._deal_contacts(kwargs, ceo_id=context.ceo_id or "")
            else:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error=f"Unknown action: {action!r}. Valid: list_deals, deal_contacts.",
                )
        except CRMIntegrationError as exc:
            return ToolResult(tool_name=self.metadata.name, success=False, error=str(exc))

    def _list_deals(self, kwargs: dict, *, ceo_id: str) -> ToolResult:
        limit = min(int(kwargs.get("limit") or 20), 100)
        pipeline = kwargs.get("pipeline")
        query = str(kwargs.get("query") or "").strip().lower()

        # Live CRM first; fall back to demo seed if unconfigured
        _has_crm = os.getenv("HUBSPOT_API_TOKEN", "").strip() or os.getenv("SALESFORCE_ACCESS_TOKEN", "").strip()
        if not _has_crm and ceo_id:
            demo_deals = _fetch_demo_deals(ceo_id)
            if demo_deals is not None:
                deals = demo_deals[:limit]
                if query:
                    deals = [
                        d for d in deals
                        if query in (d.get("name") or "").lower()
                        or query in (d.get("stage") or "").lower()
                        or query in (d.get("account_name") or "").lower()
                    ]
                deals_sorted = sorted(deals, key=lambda d: d.get("amount") or 0, reverse=True)
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=True,
                    data={"deals": deals_sorted, "count": len(deals_sorted), "crm": "demo", "pipeline_filter": pipeline},
                )

        deals = fetch_crm_deals(limit=limit, pipeline=pipeline)

        if query:
            deals = [
                d for d in deals
                if query in (d.get("name") or "").lower()
                or query in (d.get("stage") or "").lower()
                or query in (d.get("account_name") or "").lower()
            ]

        # Sort by amount descending for CEO relevance
        deals_sorted = sorted(deals, key=lambda d: d.get("amount") or 0, reverse=True)

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "deals": deals_sorted,
                "count": len(deals_sorted),
                "crm": deals_sorted[0].get("crm") if deals_sorted else "unknown",
                "pipeline_filter": pipeline,
            },
        )

    def _deal_contacts(self, kwargs: dict, *, ceo_id: str = "") -> ToolResult:
        deal_id = str(kwargs.get("deal_id") or "").strip()
        if not deal_id:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="deal_contacts requires 'deal_id'. Get it from list_deals.",
            )

        # Demo fallback: contacts are embedded in the deal dict
        _has_crm = os.getenv("HUBSPOT_API_TOKEN", "").strip() or os.getenv("SALESFORCE_ACCESS_TOKEN", "").strip()
        if not _has_crm and ceo_id:
            demo_deals = _fetch_demo_deals(ceo_id)
            if demo_deals is not None:
                deal = next((d for d in demo_deals if d.get("deal_id") == deal_id), None)
                contacts = deal.get("contacts", []) if deal else []
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=True,
                    data={"deal_id": deal_id, "contacts": contacts, "count": len(contacts)},
                )

        contacts = fetch_crm_deal_contacts(deal_id)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "deal_id": deal_id,
                "contacts": contacts,
                "count": len(contacts),
            },
        )

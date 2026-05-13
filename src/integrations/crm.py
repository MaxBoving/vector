"""CRM integration layer — HubSpot and Salesforce.

Authentication:
    HubSpot:    HUBSPOT_API_TOKEN env var (Private App token)
    Salesforce: SALESFORCE_INSTANCE_URL + SALESFORCE_ACCESS_TOKEN env vars

Both adapters return normalized deal / contact / company dicts so callers
don't need to know which CRM is active.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class CRMIntegrationError(RuntimeError):
    pass


def _get(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = Request(url, headers=headers)
    try:
        with urlopen(request) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise CRMIntegrationError(f"CRM request failed: {exc}") from exc


# ---------------------------------------------------------------------------
# HubSpot
# ---------------------------------------------------------------------------

HUBSPOT_API_BASE = "https://api.hubapi.com"

_DEAL_PROPS = [
    "dealname", "amount", "dealstage", "pipeline", "closedate",
    "hubspot_owner_id", "hs_lastmodifieddate", "hs_deal_stage_probability",
    "description",
]
_CONTACT_PROPS = ["firstname", "lastname", "email", "jobtitle", "company"]


def _hubspot_headers() -> dict[str, str]:
    token = os.getenv("HUBSPOT_API_TOKEN", "").strip()
    if not token:
        raise CRMIntegrationError(
            "HUBSPOT_API_TOKEN is not set. "
            "Create a HubSpot Private App and paste the token into the environment."
        )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def fetch_hubspot_deals(*, limit: int = 20, pipeline: Optional[str] = None) -> list[dict[str, Any]]:
    """Fetch open deals from HubSpot, optionally filtered by pipeline."""
    props = ",".join(_DEAL_PROPS)
    params: dict[str, Any] = {"limit": min(limit, 100), "properties": props}
    if pipeline:
        params["pipeline"] = pipeline
    url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals?{urlencode(params)}"
    data = _get(url, _hubspot_headers())
    results = data.get("results", [])
    return [_normalize_hubspot_deal(r) for r in results]


def fetch_hubspot_deal_contacts(deal_id: str) -> list[dict[str, Any]]:
    """Fetch contacts associated with a HubSpot deal."""
    assoc_url = f"{HUBSPOT_API_BASE}/crm/v3/objects/deals/{deal_id}/associations/contacts"
    assoc_data = _get(assoc_url, _hubspot_headers())
    contact_ids = [r["id"] for r in assoc_data.get("results", [])]
    contacts: list[dict[str, Any]] = []
    props = ",".join(_CONTACT_PROPS)
    for cid in contact_ids[:5]:
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/{cid}?properties={props}"
        try:
            contact_data = _get(url, _hubspot_headers())
            contacts.append(_normalize_hubspot_contact(contact_data))
        except CRMIntegrationError:
            continue
    return contacts


def _normalize_hubspot_deal(raw: dict[str, Any]) -> dict[str, Any]:
    props = raw.get("properties", {})
    return {
        "deal_id": raw.get("id"),
        "crm": "hubspot",
        "name": props.get("dealname", "Unnamed Deal"),
        "amount": _parse_float(props.get("amount")),
        "stage": props.get("dealstage", ""),
        "pipeline": props.get("pipeline", ""),
        "close_date": props.get("closedate", ""),
        "win_probability": _parse_float(props.get("hs_deal_stage_probability")),
        "last_modified": props.get("hs_lastmodifieddate", ""),
        "description": props.get("description", ""),
        "owner_id": props.get("hubspot_owner_id", ""),
    }


def _normalize_hubspot_contact(raw: dict[str, Any]) -> dict[str, Any]:
    props = raw.get("properties", {})
    first = props.get("firstname", "")
    last = props.get("lastname", "")
    return {
        "contact_id": raw.get("id"),
        "name": f"{first} {last}".strip() or "Unknown",
        "email": props.get("email", ""),
        "title": props.get("jobtitle", ""),
        "company": props.get("company", ""),
    }


# ---------------------------------------------------------------------------
# Salesforce
# ---------------------------------------------------------------------------

def _salesforce_headers() -> dict[str, str]:
    token = os.getenv("SALESFORCE_ACCESS_TOKEN", "").strip()
    if not token:
        raise CRMIntegrationError(
            "SALESFORCE_ACCESS_TOKEN is not set. "
            "Set SALESFORCE_INSTANCE_URL and SALESFORCE_ACCESS_TOKEN in the environment."
        )
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _salesforce_instance() -> str:
    url = os.getenv("SALESFORCE_INSTANCE_URL", "").strip().rstrip("/")
    if not url:
        raise CRMIntegrationError("SALESFORCE_INSTANCE_URL is not set.")
    return url


def fetch_salesforce_opportunities(*, limit: int = 20) -> list[dict[str, Any]]:
    """Fetch open opportunities from Salesforce via SOQL."""
    soql = (
        "SELECT Id, Name, Amount, StageName, CloseDate, Probability, "
        "AccountId, Account.Name, OwnerId, Description, LastModifiedDate "
        "FROM Opportunity "
        "WHERE IsClosed = false "
        "ORDER BY LastModifiedDate DESC "
        f"LIMIT {min(limit, 100)}"
    )
    url = f"{_salesforce_instance()}/services/data/v58.0/query?q={quote(soql)}"
    data = _get(url, _salesforce_headers())
    records = data.get("records", [])
    return [_normalize_sf_opportunity(r) for r in records]


def fetch_salesforce_opportunity_contacts(opportunity_id: str) -> list[dict[str, Any]]:
    """Fetch contacts linked to a Salesforce opportunity via OpportunityContactRole."""
    soql = (
        f"SELECT ContactId, Contact.Name, Contact.Email, Contact.Title, Role "
        f"FROM OpportunityContactRole WHERE OpportunityId = '{opportunity_id}' LIMIT 10"
    )
    url = f"{_salesforce_instance()}/services/data/v58.0/query?q={quote(soql)}"
    data = _get(url, _salesforce_headers())
    return [
        {
            "contact_id": r.get("ContactId"),
            "name": (r.get("Contact") or {}).get("Name", ""),
            "email": (r.get("Contact") or {}).get("Email", ""),
            "title": (r.get("Contact") or {}).get("Title", ""),
            "role": r.get("Role", ""),
        }
        for r in data.get("records", [])
    ]


def _normalize_sf_opportunity(raw: dict[str, Any]) -> dict[str, Any]:
    account = raw.get("Account") or {}
    return {
        "deal_id": raw.get("Id"),
        "crm": "salesforce",
        "name": raw.get("Name", "Unnamed Opportunity"),
        "amount": _parse_float(raw.get("Amount")),
        "stage": raw.get("StageName", ""),
        "pipeline": "default",
        "close_date": str(raw.get("CloseDate", "")),
        "win_probability": _parse_float(raw.get("Probability")),
        "last_modified": str(raw.get("LastModifiedDate", "")),
        "description": raw.get("Description", "") or "",
        "account_name": account.get("Name", ""),
        "owner_id": raw.get("OwnerId", ""),
    }


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def fetch_crm_deals(*, limit: int = 20, pipeline: Optional[str] = None) -> list[dict[str, Any]]:
    """
    Try HubSpot first, then Salesforce. Returns normalized deal dicts from
    whichever CRM is configured, or raises CRMIntegrationError if neither is set.
    """
    hubspot_token = os.getenv("HUBSPOT_API_TOKEN", "").strip()
    if hubspot_token:
        return fetch_hubspot_deals(limit=limit, pipeline=pipeline)

    sf_token = os.getenv("SALESFORCE_ACCESS_TOKEN", "").strip()
    if sf_token:
        return fetch_salesforce_opportunities(limit=limit)

    raise CRMIntegrationError(
        "No CRM is configured. Set HUBSPOT_API_TOKEN (HubSpot) or "
        "SALESFORCE_INSTANCE_URL + SALESFORCE_ACCESS_TOKEN (Salesforce)."
    )


def fetch_crm_deal_contacts(deal_id: str) -> list[dict[str, Any]]:
    """Fetch contacts for a specific deal from whichever CRM is active."""
    hubspot_token = os.getenv("HUBSPOT_API_TOKEN", "").strip()
    if hubspot_token:
        return fetch_hubspot_deal_contacts(deal_id)
    sf_token = os.getenv("SALESFORCE_ACCESS_TOKEN", "").strip()
    if sf_token:
        return fetch_salesforce_opportunity_contacts(deal_id)
    raise CRMIntegrationError("No CRM is configured.")


def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

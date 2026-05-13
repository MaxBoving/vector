"""Webhook normalization layer — webhook-signal-ingester pattern.

Converts raw inbound SaaS webhook payloads into typed ExtractedSignal objects.
Supported sources: stripe, carta, jira, pagerduty.
Unknown sources fall back to the generic operating signal extractor.

Each normalizer:
    - Sets signal_type and source correctly
    - Classifies urgency from event type + payload content
    - Extracts key facts and entities
    - Sets action_required and action_hint
"""
from __future__ import annotations

import re
from typing import Any, Dict

from src.workflows.signal_extractor import (
    ExtractedSignal,
    _classify_domains,
    _classify_urgency,
    _extract_entities,
    _extract_key_facts,
    extract_from_operating_signal,
)


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------

_STRIPE_CRITICAL = {
    "payment_intent.payment_failed",
    "charge.dispute.created",
    "charge.dispute.funds_withdrawn",
    "invoice.payment_failed",
    "customer.subscription.deleted",
    "radar.early_fraud_warning.created",
    "account.application.deauthorized",
}
_STRIPE_HIGH = {
    "payment_intent.succeeded",
    "invoice.paid",
    "customer.subscription.trial_will_end",
    "customer.subscription.updated",
    "payout.failed",
    "transfer.failed",
}


def normalize_stripe_webhook(payload: Dict[str, Any]) -> ExtractedSignal:
    event_type = str(payload.get("type", "stripe.unknown"))
    event_data = payload.get("data", {}).get("object", {}) or {}
    event_id = str(payload.get("id", ""))

    amount_raw = event_data.get("amount") or event_data.get("amount_due") or event_data.get("amount_paid")
    amount_str = f"${amount_raw / 100:,.2f}" if isinstance(amount_raw, (int, float)) else ""

    customer = str(event_data.get("customer", event_data.get("customer_email", "")))
    description = str(event_data.get("description", event_data.get("statement_descriptor", "")))
    currency = str(event_data.get("currency", "usd")).upper()

    if event_type in _STRIPE_CRITICAL:
        urgency = "critical"
    elif event_type in _STRIPE_HIGH:
        urgency = "high"
    else:
        urgency = "normal"

    title = _stripe_title(event_type, amount_str, currency)
    summary_parts = [title]
    if customer:
        summary_parts.append(f"Customer: {customer}")
    if description:
        summary_parts.append(description[:120])
    summary = " | ".join(summary_parts)

    key_facts = []
    if amount_str:
        key_facts.append(f"Amount: {amount_str} {currency}")
    if customer:
        key_facts.append(f"Customer: {customer}")
    event_label = event_type.replace(".", " → ")
    key_facts.append(f"Event: {event_label}")

    failure_msg = str(event_data.get("failure_message", event_data.get("last_payment_error", {}).get("message", "")))
    if failure_msg:
        key_facts.append(f"Reason: {failure_msg[:120]}")

    action_required = urgency in {"critical", "high"}
    action_hint = _stripe_action_hint(event_type)

    return ExtractedSignal(
        signal_id=event_id,
        source="stripe",
        signal_type="payment_event",
        urgency=urgency,
        title=title,
        summary=summary[:400],
        entities=[e for e in [customer, description] if e],
        key_facts=key_facts[:5],
        action_required=action_required,
        action_hint=action_hint,
        domains=["finance"],
        raw=payload,
    )


def _stripe_title(event_type: str, amount_str: str, currency: str) -> str:
    label = event_type.replace("_", " ").replace(".", " — ").title()
    if amount_str:
        return f"Stripe: {label} ({amount_str} {currency})"
    return f"Stripe: {label}"


def _stripe_action_hint(event_type: str) -> str:
    hints = {
        "payment_intent.payment_failed": "Review failed payment and follow up with customer.",
        "charge.dispute.created": "Respond to dispute within the deadline to avoid chargeback.",
        "invoice.payment_failed": "Contact customer — subscription at risk.",
        "customer.subscription.deleted": "Churn event. Notify sales for win-back analysis.",
        "radar.early_fraud_warning.created": "Review transaction for potential fraud.",
        "payout.failed": "Check bank account details and retry payout.",
    }
    return hints.get(event_type, "")


# ---------------------------------------------------------------------------
# Carta (equity events)
# ---------------------------------------------------------------------------

_CARTA_CRITICAL = {
    "tender_offer.started", "liquidity_event.closed", "409a_valuation.completed",
    "convertible_note.converted", "safe.converted",
}
_CARTA_HIGH = {
    "option_grant.approved", "stock_certificate.issued", "equity_plan.amended",
    "vesting_schedule.updated", "secondary_transaction.completed",
}


def normalize_carta_webhook(payload: Dict[str, Any]) -> ExtractedSignal:
    event = str(payload.get("event", payload.get("event_type", "carta.unknown")))
    event_id = str(payload.get("id", payload.get("event_id", "")))
    data = payload.get("data", payload) or {}

    company = str(data.get("company_name", data.get("company", "")))
    stakeholder = str(data.get("stakeholder_name", data.get("holder_name", "")))
    amount = data.get("amount") or data.get("value") or data.get("principal_amount")
    shares = data.get("shares") or data.get("number_of_shares")

    if event in _CARTA_CRITICAL:
        urgency = "critical"
    elif event in _CARTA_HIGH:
        urgency = "high"
    else:
        urgency = _classify_urgency(f"{event} {str(data)[:200]}")

    label = event.replace("_", " ").replace(".", " — ").title()
    title = f"Carta: {label}"

    key_facts = [f"Event: {label}"]
    if company:
        key_facts.append(f"Company: {company}")
    if stakeholder:
        key_facts.append(f"Stakeholder: {stakeholder}")
    if amount:
        key_facts.append(f"Amount: {amount}")
    if shares:
        key_facts.append(f"Shares: {shares:,}" if isinstance(shares, (int, float)) else f"Shares: {shares}")

    action_hint = _carta_action_hint(event)

    return ExtractedSignal(
        signal_id=event_id,
        source="carta",
        signal_type="equity_event",
        urgency=urgency,
        title=title,
        summary=f"{title} — {stakeholder or company or 'see details'}",
        entities=[e for e in [company, stakeholder] if e],
        key_facts=key_facts[:5],
        action_required=urgency in {"critical", "high"},
        action_hint=action_hint,
        domains=["finance", "legal", "hr"],
        raw=payload,
    )


def _carta_action_hint(event: str) -> str:
    hints = {
        "tender_offer.started": "Review tender offer terms and communicate to eligible stakeholders.",
        "liquidity_event.closed": "Coordinate with legal and finance on distribution and tax implications.",
        "409a_valuation.completed": "Update strike prices for new option grants.",
        "convertible_note.converted": "Update cap table and notify new shareholders.",
    }
    return hints.get(event, "")


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

_JIRA_CRITICAL_PRIORITIES = {"blocker", "critical"}
_JIRA_HIGH_PRIORITIES = {"major", "high"}


def normalize_jira_webhook(payload: Dict[str, Any]) -> ExtractedSignal:
    webhook_event = str(payload.get("webhookEvent", "jira:unknown"))
    issue = payload.get("issue", {}) or {}
    fields = issue.get("fields", {}) or {}
    event_id = str(payload.get("timestamp", issue.get("id", "")))

    summary = str(fields.get("summary", "Jira Update"))
    description = str((fields.get("description") or {}).get("text", "") if isinstance(fields.get("description"), dict) else fields.get("description", ""))
    issue_type = str((fields.get("issuetype") or {}).get("name", "Issue"))
    priority = str((fields.get("priority") or {}).get("name", "medium")).lower()
    status = str((fields.get("status") or {}).get("name", ""))
    assignee_raw = fields.get("assignee") or {}
    assignee = str(assignee_raw.get("displayName", assignee_raw.get("name", "")))
    reporter_raw = fields.get("reporter") or {}
    reporter = str(reporter_raw.get("displayName", reporter_raw.get("name", "")))
    issue_key = str(issue.get("key", ""))
    project = str((fields.get("project") or {}).get("name", ""))

    if priority in _JIRA_CRITICAL_PRIORITIES:
        urgency = "critical"
    elif priority in _JIRA_HIGH_PRIORITIES:
        urgency = "high"
    else:
        urgency = _classify_urgency(f"{summary} {description[:200]}")

    event_label = webhook_event.replace("jira:", "").replace("_", " ").title()
    title = f"Jira {event_label}: [{issue_key}] {summary}"

    key_facts = [f"Issue: {issue_key} ({issue_type})", f"Status: {status}", f"Priority: {priority.title()}"]
    if assignee:
        key_facts.append(f"Assignee: {assignee}")
    if project:
        key_facts.append(f"Project: {project}")

    action_required = urgency in {"critical", "high"}
    action_hint = ""
    if urgency == "critical":
        action_hint = f"Critical Jira issue [{issue_key}] requires immediate attention."
    elif urgency == "high":
        action_hint = f"High-priority issue [{issue_key}] should be reviewed promptly."

    full_text = f"{summary} {description}"
    return ExtractedSignal(
        signal_id=event_id,
        source="jira",
        signal_type="ticket_update",
        urgency=urgency,
        title=title,
        summary=f"[{issue_key}] {summary} — {status} | Priority: {priority.title()} | {assignee or 'Unassigned'}",
        entities=[e for e in [assignee, reporter, project] if e],
        key_facts=key_facts[:5],
        action_required=action_required,
        action_hint=action_hint,
        domains=_classify_domains(full_text) or ["ops", "product"],
        raw=payload,
    )


# ---------------------------------------------------------------------------
# PagerDuty
# ---------------------------------------------------------------------------

_PD_CRITICAL_SEVERITIES = {"critical", "p1"}
_PD_HIGH_SEVERITIES = {"error", "high", "p2"}


def normalize_pagerduty_webhook(payload: Dict[str, Any]) -> ExtractedSignal:
    # PagerDuty sends either a v3 event or a messages list
    messages = payload.get("messages") or []
    if messages and isinstance(messages, list):
        msg = messages[0]
        event_type = str(msg.get("event", ""))
        incident = msg.get("incident", {}) or {}
    else:
        event_type = str(payload.get("event", {}).get("event_type", payload.get("event_type", "incident.trigger")))
        incident = payload.get("incident", payload.get("event", {}).get("data", {})) or {}

    incident_id = str(incident.get("id", incident.get("incident_number", "")))
    title_raw = str(incident.get("title", incident.get("description", "PagerDuty Incident")))
    status = str(incident.get("status", "triggered"))
    urgency_raw = str(incident.get("urgency", incident.get("severity", "high"))).lower()
    service = (incident.get("service") or {}).get("summary", "")
    escalation_policy = (incident.get("escalation_policy") or {}).get("summary", "")
    html_url = str(incident.get("html_url", ""))

    if urgency_raw in _PD_CRITICAL_SEVERITIES or event_type in {"incident.trigger"}:
        urgency = "critical"
    elif urgency_raw in _PD_HIGH_SEVERITIES:
        urgency = "high"
    else:
        urgency = "normal"

    status_label = status.replace("_", " ").title()
    event_label = event_type.replace("incident.", "").replace("_", " ").title()
    title = f"PagerDuty {event_label}: {title_raw}"

    key_facts = [f"Status: {status_label}", f"Urgency: {urgency_raw.upper()}"]
    if service:
        key_facts.append(f"Service: {service}")
    if escalation_policy:
        key_facts.append(f"Escalation policy: {escalation_policy}")
    if incident_id:
        key_facts.append(f"Incident #{incident_id}")

    action_hint = ""
    if urgency == "critical":
        action_hint = f"Active incident on {service or 'a service'}. Check PagerDuty immediately."
    elif status in {"acknowledged", "resolved"}:
        action_hint = f"Incident {status}. Review post-mortem once resolved."

    return ExtractedSignal(
        signal_id=incident_id or event_type,
        source="pagerduty",
        signal_type="incident",
        urgency=urgency,
        title=title,
        summary=f"{title} | Status: {status_label}" + (f" | Service: {service}" if service else ""),
        entities=[e for e in [service, escalation_policy] if e],
        key_facts=key_facts[:5],
        action_required=urgency in {"critical", "high"} and status not in {"resolved"},
        action_hint=action_hint,
        domains=["ops"],
        raw=payload,
    )


# ---------------------------------------------------------------------------
# Unified normalizer
# ---------------------------------------------------------------------------

_SOURCE_SIGNATURES = {
    "stripe": re.compile(r'\b(stripe|payment_intent|charge\.|invoice\.|payout|radar)\b', re.IGNORECASE),
    "carta": re.compile(r'\b(carta|tender_offer|option_grant|409a|cap_table|vesting|convertible)\b', re.IGNORECASE),
    "jira": re.compile(r'\b(jira|webhookEvent|issuetype|issuekey|projectKey|sprint)\b', re.IGNORECASE),
    "pagerduty": re.compile(r'\b(pagerduty|pd_|incident\.|escalation_policy|oncall)\b', re.IGNORECASE),
}


def _detect_source(payload: Dict[str, Any]) -> str:
    text = " ".join(str(v) for v in list(payload.keys())[:20])
    text += " " + " ".join(str(v) for v in list(payload.values())[:10] if isinstance(v, str))
    for source, pattern in _SOURCE_SIGNATURES.items():
        if pattern.search(text):
            return source
    # Stripe-specific: type field like "payment_intent.succeeded"
    event_type = str(payload.get("type", ""))
    if re.match(r'^[a-z_]+\.[a-z_.]+$', event_type) and payload.get("data"):
        return "stripe"
    # Jira-specific
    if "webhookEvent" in payload or "issue" in payload:
        return "jira"
    # PagerDuty-specific
    if "messages" in payload or ("incident" in payload and "escalation_policy" in payload.get("incident", {})):
        return "pagerduty"
    return "unknown"


def normalize_webhook(source: str, payload: Dict[str, Any]) -> ExtractedSignal:
    """
    Normalize a SaaS webhook payload into an ExtractedSignal.

    Args:
        source: "stripe" | "carta" | "jira" | "pagerduty" | "auto"
        payload: raw webhook dict

    If source is "auto", the source is inferred from the payload structure.
    """
    resolved = source.lower().strip()
    if resolved == "auto":
        resolved = _detect_source(payload)

    if resolved == "stripe":
        return normalize_stripe_webhook(payload)
    if resolved == "carta":
        return normalize_carta_webhook(payload)
    if resolved == "jira":
        return normalize_jira_webhook(payload)
    if resolved == "pagerduty":
        return normalize_pagerduty_webhook(payload)

    # Fallback: treat as generic operating signal with source tag
    signal = extract_from_operating_signal(payload)
    signal.source = resolved or "webhook"
    signal.signal_type = "webhook_event"
    return signal

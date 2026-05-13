"""Event ingestion routes: /events, /briefings, /webhook/gmail."""
from __future__ import annotations

import base64
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from src.api.routes.auth import get_current_user
from src.api.schemas import (
    AssistantMessageResponse,
    CalendarBriefingRequest,
    EmailIngestionRequest,
    MorningBriefRequest,
)
from src.core.database import engine, get_user_by_ceo_id
from src.integrations.providers import (
    ProviderIntegrationError,
    fetch_new_messages_since,
    register_gmail_watch,
)
from src.workflows.event_runner import EventWorkflowRunner

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

_CONTENT_MAX_CHARS = 8_000


def _sanitize_external_content(text: str | None) -> str | None:
    """Truncate untrusted external content and wrap with sentinels."""
    if not text:
        return text
    cleaned = "".join(
        ch for ch in text if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    )
    truncated = cleaned[:_CONTENT_MAX_CHARS]
    if len(cleaned) > _CONTENT_MAX_CHARS:
        truncated += "\n[... content truncated by agenticMIND security filter ...]"
    return (
        "--- BEGIN EXTERNAL UNTRUSTED CONTENT ---\n"
        + truncated
        + "\n--- END EXTERNAL UNTRUSTED CONTENT ---"
    )


GMAIL_PUBSUB_AUDIENCE = os.getenv("API_BASE_URL", "http://localhost:8000") + "/webhook/gmail/pubsub"
GMAIL_PUBSUB_TOPIC = os.getenv("GMAIL_PUBSUB_TOPIC", "")


async def _verify_pubsub_jwt(request: Request) -> str:
    from jose import JWTError, jwt as jose_jwt

    if os.getenv("AGENTICMIND_MODE", "dev").strip().lower() == "dev":
        logger.warning("Pub/Sub JWT verification skipped in dev mode")
        return "dev@localhost"

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Pub/Sub bearer token")

    token = auth_header.removeprefix("Bearer ").strip()
    try:
        from jose import jwk
        import urllib.request as _urllib

        with _urllib.urlopen("https://www.googleapis.com/oauth2/v3/certs") as resp:
            jwks = json.loads(resp.read())

        header = jose_jwt.get_unverified_header(token)
        key = next((k for k in jwks["keys"] if k.get("kid") == header.get("kid")), None)
        if not key:
            raise HTTPException(status_code=401, detail="No matching public key for Pub/Sub JWT")

        claims = jose_jwt.decode(token, jwk.construct(key), algorithms=["RS256"], audience=GMAIL_PUBSUB_AUDIENCE)
        return claims.get("email", "unknown")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid Pub/Sub JWT: {exc}") from exc


@router.post("/events/email", response_model=AssistantMessageResponse)
async def email_ingestion(
    payload: EmailIngestionRequest | None = None,
    current_user: User = Depends(get_current_user),
):
    runner = EventWorkflowRunner()
    try:
        logger.info(
            "events.email received ceo_id=%s sender=%r subject=%r",
            current_user.ceo_id,
            (payload or EmailIngestionRequest()).sender,
            (payload or EmailIngestionRequest()).subject,
        )
        return await runner.run_email_ingestion(payload or EmailIngestionRequest(), current_user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Email workflow failed: {str(exc)}") from exc


@router.post("/events/calendar", response_model=AssistantMessageResponse)
async def calendar_briefing(
    payload: CalendarBriefingRequest | None = None,
    current_user: User = Depends(get_current_user),
):
    runner = EventWorkflowRunner()
    try:
        logger.info(
            "events.calendar received ceo_id=%s title=%r scheduled_for=%r timezone=%r",
            current_user.ceo_id,
            (payload or CalendarBriefingRequest()).title,
            (payload or CalendarBriefingRequest()).scheduled_for,
            (payload or CalendarBriefingRequest()).timezone,
        )
        return await runner.run_calendar_briefing(payload or CalendarBriefingRequest(), current_user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calendar workflow failed: {str(exc)}") from exc


@router.post("/briefings/morning", response_model=AssistantMessageResponse)
async def morning_briefing(
    payload: MorningBriefRequest,
    current_user: User = Depends(get_current_user),
):
    runner = EventWorkflowRunner()
    try:
        logger.info(
            "events.morning_brief received ceo_id=%s scheduled_for=%r timezone=%r",
            current_user.ceo_id,
            payload.scheduled_for,
            payload.timezone,
        )
        return await runner.run_morning_brief(payload, current_user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Morning brief failed: {str(exc)}") from exc


@router.post("/webhook/gmail/pubsub")
async def gmail_pubsub_push(request: Request):
    """Receive Gmail push notifications from Google Cloud Pub/Sub."""
    await _verify_pubsub_jwt(request)

    try:
        body = await request.json()
        encoded_data = body["message"]["data"]
        notification = json.loads(base64.b64decode(encoded_data + "==").decode("utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse Pub/Sub push payload: %s", exc)
        return Response(status_code=204)

    email_address = notification.get("emailAddress")
    new_history_id = notification.get("historyId")

    if not email_address or not new_history_id:
        logger.warning("Pub/Sub notification missing emailAddress or historyId: %s", notification)
        return Response(status_code=204)

    from src.core.models import ConnectedAccount as _CA
    with Session(engine) as session:
        account = session.exec(
            select(_CA).where(_CA.account_email == email_address).where(_CA.service == "gmail")
        ).first()

    if not account:
        logger.info("No CEO found for Gmail push address %s -- ignoring", email_address)
        return Response(status_code=204)

    user = get_user_by_ceo_id(account.ceo_id)
    if not user:
        return Response(status_code=204)

    stored_history_id = (account.provider_metadata or {}).get(
        "gmail_history_id", str(int(new_history_id) - 1)
    )

    messages = fetch_new_messages_since(account.ceo_id, stored_history_id)
    if not messages:
        return Response(status_code=204)

    runner = EventWorkflowRunner()
    for msg in messages[:5]:
        try:
            ingestion_request = EmailIngestionRequest(
                sender=msg.get("sender"),
                subject=msg.get("subject"),
                content=_sanitize_external_content(msg.get("content")),
                thread_id=msg.get("thread_id"),
                labels=msg.get("labels", []),
                received_at=msg.get("received_at"),
            )
            await runner.run_email_ingestion(ingestion_request, user)
        except Exception as exc:
            logger.exception("Email ingestion failed for %s: %s", email_address, exc)

    return Response(status_code=204)


@router.post("/integrations/gmail/watch")
async def setup_gmail_watch(current_user: User = Depends(get_current_user)):
    """Register (or renew) a Gmail push watch for the authenticated CEO."""
    from datetime import datetime, timezone as _tz

    if not GMAIL_PUBSUB_TOPIC:
        raise HTTPException(
            status_code=500,
            detail="GMAIL_PUBSUB_TOPIC is not configured. Set it in .env.",
        )
    try:
        result = register_gmail_watch(current_user.ceo_id, GMAIL_PUBSUB_TOPIC)
        expiry_ms = int(result.get("expiration", 0))
        expiry_iso = None
        if expiry_ms:
            expiry_iso = datetime.fromtimestamp(expiry_ms / 1000, tz=_tz.utc).isoformat()
        return {
            "ok": True,
            "history_id": result.get("historyId"),
            "expires_at": expiry_iso,
            "note": "Watch registered. Renew before expiry via this endpoint.",
        }
    except ProviderIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

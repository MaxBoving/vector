"""Event ingestion routes: /events, /briefings, /webhook/openclaw, /webhook/gmail."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from src.api.routes.auth import get_current_user
from src.api.schemas import (
    AssistantMessageResponse,
    AssistantQueryRequest,
    CalendarBriefingRequest,
    EmailIngestionRequest,
    MorningBriefRequest,
    OpenClawCalendarData,
    OpenClawEmailData,
    OpenClawMessageData,
    OpenClawWebhookPayload,
    OpenClawWebhookResponse,
)
from src.core.database import (
    engine,
    get_user_by_ceo_id,
    get_user_by_openclaw_token,
    rotate_openclaw_token,
    save_object,
)
from src.core.models import SessionInteraction, User
from src.integrations.providers import (
    ProviderIntegrationError,
    fetch_new_messages_since,
    register_gmail_watch,
)
from src.assistant.agent import AgenticAssistant
from src.workflows.event_runner import EventWorkflowRunner
from src.workflows.read_model import get_default_conversation_id

logger = logging.getLogger(__name__)

_agent = AgenticAssistant()

router = APIRouter(tags=["events"])

# ---------------------------------------------------------------------------
# OpenClaw security constants + helpers
# ---------------------------------------------------------------------------

_OPENCLAW_RATE: dict[str, list[float]] = {}   # token → list of unix timestamps
_OPENCLAW_RATE_LIMIT = 30                      # max requests
_OPENCLAW_RATE_WINDOW = 60.0                   # per N seconds
_OPENCLAW_TIMESTAMP_TOLERANCE = 300            # 5 minutes in seconds
_OPENCLAW_CONTENT_MAX_CHARS = 8_000


def _verify_openclaw_request(request: Request, raw_body: bytes, token: str) -> None:
    """Verify timestamp freshness and HMAC-SHA256 signature.

    Signed message is: ``<unix_timestamp>\\n<raw_body_bytes>``
    """
    ts_header = request.headers.get("X-OpenClaw-Timestamp", "")
    sig_header = request.headers.get("X-OpenClaw-Signature", "")

    if not ts_header or not sig_header:
        raise HTTPException(status_code=401, detail="Missing OpenClaw auth headers")

    try:
        ts = float(ts_header)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp header")

    if abs(time.time() - ts) > _OPENCLAW_TIMESTAMP_TOLERANCE:
        raise HTTPException(status_code=401, detail="Request timestamp too old or too far in the future")

    signed_msg = f"{ts_header}\n".encode() + raw_body
    expected = hmac.new(token.encode(), signed_msg, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(sig_header, expected):
        raise HTTPException(status_code=401, detail="Invalid OpenClaw webhook signature")


def _openclaw_rate_check(token: str) -> None:
    """Sliding-window rate limiter: max 30 requests per 60 s per token."""
    now = time.time()
    window_start = now - _OPENCLAW_RATE_WINDOW
    hits = _OPENCLAW_RATE.get(token, [])
    hits = [t for t in hits if t > window_start]
    if len(hits) >= _OPENCLAW_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="OpenClaw webhook rate limit exceeded")
    hits.append(now)
    _OPENCLAW_RATE[token] = hits


def _sanitize_external_content(text: str | None) -> str | None:
    """Truncate and wrap external content to prevent prompt injection."""
    if not text:
        return text
    cleaned = "".join(
        ch for ch in text if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    )
    truncated = cleaned[:_OPENCLAW_CONTENT_MAX_CHARS]
    if len(cleaned) > _OPENCLAW_CONTENT_MAX_CHARS:
        truncated += "\n[… content truncated by agenticMIND security filter …]"
    return (
        "--- BEGIN EXTERNAL UNTRUSTED CONTENT ---\n"
        + truncated
        + "\n--- END EXTERNAL UNTRUSTED CONTENT ---"
    )


# ---------------------------------------------------------------------------
# Gmail Pub/Sub constants + helper
# ---------------------------------------------------------------------------

GMAIL_PUBSUB_AUDIENCE = os.getenv("API_BASE_URL", "http://localhost:8000") + "/webhook/gmail/pubsub"
GMAIL_PUBSUB_TOPIC = os.getenv("GMAIL_PUBSUB_TOPIC", "")


async def _verify_pubsub_jwt(request: Request) -> str:
    """Verify the Google-signed bearer JWT on Pub/Sub push requests.

    Skipped in dev mode (AGENTICMIND_MODE=dev) so local testing works
    without a real Google Cloud project.
    """
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
        key = next(
            (k for k in jwks["keys"] if k.get("kid") == header.get("kid")), None
        )
        if not key:
            raise HTTPException(status_code=401, detail="No matching public key for Pub/Sub JWT")

        claims = jose_jwt.decode(
            token,
            jwk.construct(key),
            algorithms=["RS256"],
            audience=GMAIL_PUBSUB_AUDIENCE,
        )
        return claims.get("email", "unknown")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid Pub/Sub JWT: {exc}") from exc


# ---------------------------------------------------------------------------
# Event routes
# ---------------------------------------------------------------------------

@router.post("/events/email", response_model=AssistantMessageResponse)
async def email_ingestion(
    payload: EmailIngestionRequest | None = None,
    current_user: User = Depends(get_current_user),
):
    runner = EventWorkflowRunner()
    try:
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
        return await runner.run_calendar_briefing(payload or CalendarBriefingRequest(), current_user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Calendar workflow failed: {str(exc)}") from exc


@router.post("/briefings/morning", response_model=AssistantMessageResponse)
async def morning_briefing(
    payload: MorningBriefRequest,
    current_user: User = Depends(get_current_user),
):
    message = (
        f"Generate the morning executive brief for {payload.scheduled_for} "
        f"(timezone: {payload.timezone}). "
        "Check my email and calendar, surface what needs attention today."
    )
    query = AssistantQueryRequest(message=message)
    interaction = save_object(SessionInteraction(
        ceo_id=current_user.ceo_id,
        query=message,
        status="PENDING",
    ))
    try:
        return await _agent.handle(payload=query, interaction=interaction, current_user=current_user)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Morning brief failed: {str(exc)}") from exc


# ---------------------------------------------------------------------------
# OpenClaw webhook routes
# Security model:
#   • URL uses a per-CEO opaque token (64 hex chars) — not the ceo_id.
#     The token is both the URL identifier AND the HMAC signing key.
#     Rotating it invalidates all prior signatures for that CEO only.
#   • HMAC-SHA256 over "<timestamp>\n<raw_body>" prevents request forgery.
#   • Timestamp freshness check (±5 min) prevents replay attacks.
#   • Per-token rate limiting prevents email flood / cost abuse.
#   • External content is sanitized before reaching any LLM prompt.
# ---------------------------------------------------------------------------

@router.post("/webhook/openclaw/{token}", response_model=OpenClawWebhookResponse)
async def openclaw_webhook(
    token: str,
    payload: OpenClawWebhookPayload,
    request: Request,
):
    """Receive events from OpenClaw and route them to the appropriate workflow.

    Auth: HMAC-SHA256 per-CEO token + timestamp freshness (no JWT).
    """
    raw_body = await request.body()

    user = get_user_by_openclaw_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    _verify_openclaw_request(request, raw_body, token)
    _openclaw_rate_check(token)

    runner = EventWorkflowRunner()

    if payload.event == "email":
        email = OpenClawEmailData(**payload.data)
        ingestion_request = EmailIngestionRequest(
            sender=email.sender,
            subject=email.subject,
            content=_sanitize_external_content(email.content or email.snippet),
            thread_id=email.thread_id,
            labels=email.labels,
            received_at=email.received_at,
        )
        try:
            result = await runner.run_email_ingestion(ingestion_request, user)
            return OpenClawWebhookResponse(
                ok=True,
                event="email",
                interaction_id=result.interaction_id,
                summary=result.answer.summary if result.answer else None,
            )
        except Exception as exc:
            logger.exception("OpenClaw email ingestion failed for ceo_id=%s", user.ceo_id)
            raise HTTPException(status_code=500, detail="Email ingestion failed") from exc

    if payload.event == "message":
        msg = OpenClawMessageData(**payload.data)
        sanitized_text = _sanitize_external_content(msg.text) or ""
        resolved_conversation_id = msg.conversation_id or get_default_conversation_id(user.ceo_id)
        query_request = AssistantQueryRequest(
            message=sanitized_text,
            conversation_id=resolved_conversation_id,
        )
        interaction = SessionInteraction(ceo_id=user.ceo_id, query=sanitized_text, status="PENDING")
        saved = save_object(interaction)
        try:
            result = await _agent.handle(
                payload=query_request,
                interaction=saved,
                current_user=user,
            )
            return OpenClawWebhookResponse(
                ok=True,
                event="message",
                interaction_id=result.interaction_id,
                summary=result.answer.summary if result.answer else None,
            )
        except Exception as exc:
            logger.exception("OpenClaw message query failed for ceo_id=%s", user.ceo_id)
            raise HTTPException(status_code=500, detail="Message query failed") from exc

    if payload.event == "calendar":
        cal = OpenClawCalendarData(**payload.data)
        cal_request = CalendarBriefingRequest(
            meeting_id=cal.meeting_id,
            title=cal.title,
            starts_at=cal.starts_at,
            attendees=cal.attendees,
            agenda=_sanitize_external_content(cal.agenda),
            notes=_sanitize_external_content(cal.notes),
        )
        try:
            result = await runner.run_calendar_briefing(cal_request, user)
            return OpenClawWebhookResponse(
                ok=True,
                event="calendar",
                interaction_id=result.interaction_id,
                summary=result.answer.summary if result.answer else None,
            )
        except Exception as exc:
            logger.exception("OpenClaw calendar briefing failed for ceo_id=%s", user.ceo_id)
            raise HTTPException(status_code=500, detail="Calendar briefing failed") from exc

    raise HTTPException(status_code=400, detail=f"Unknown event type: '{payload.event}'")


@router.post("/webhook/openclaw/token/rotate")
async def rotate_openclaw_webhook_token(current_user: User = Depends(get_current_user)):
    """Generate or rotate the OpenClaw webhook token for the authenticated CEO."""
    new_token = rotate_openclaw_token(current_user.ceo_id)
    base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
    return {
        "token": new_token,
        "webhook_url": f"{base_url}/webhook/openclaw/{new_token}",
        "note": "Update your OpenClaw skill config with this URL. The previous token is now invalid.",
    }


@router.get("/webhook/openclaw/token")
async def get_openclaw_webhook_token(current_user: User = Depends(get_current_user)):
    """Return the current OpenClaw webhook URL for the authenticated CEO."""
    base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
    token = current_user.openclaw_webhook_token
    return {
        "token": token,
        "webhook_url": f"{base_url}/webhook/openclaw/{token}" if token else None,
        "configured": token is not None,
    }


# ---------------------------------------------------------------------------
# Gmail Pub/Sub push
# ---------------------------------------------------------------------------

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
        logger.info("No CEO found for Gmail push address %s — ignoring", email_address)
        return Response(status_code=204)

    user = get_user_by_ceo_id(account.ceo_id)
    if not user:
        return Response(status_code=204)

    stored_history_id = (account.provider_metadata or {}).get("gmail_history_id", str(int(new_history_id) - 1))

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


# ---------------------------------------------------------------------------
# Gmail watch registration
# ---------------------------------------------------------------------------

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

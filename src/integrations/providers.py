from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from jose import JWTError, jwt

from src.core.database import get_connected_account, get_connected_accounts, upsert_connected_account
from src.core.models import ConnectedAccount, User
from src.integrations.email_extraction import cross_reference_threads_with_calendar, extract_structured_watch_items
from src.integrations.email_intelligence import rank_email_threads, select_primary_thread
from src.tools.demo_config import load_fixture


FRONTEND_APP_URL = os.getenv("APP_BASE_URL", "http://localhost:5173")
BACKEND_APP_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
OAUTH_STATE_SECRET = os.getenv("JWT_SECRET_KEY", "agenticmind-oauth-state")
APP_MODE = os.getenv("AGENTICMIND_MODE", "dev").strip().lower()

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_GMAIL_LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GOOGLE_GMAIL_GET_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}"
GOOGLE_GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GOOGLE_CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

MICROSOFT_AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MICROSOFT_GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"
MICROSOFT_GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"
MICROSOFT_GRAPH_CALENDAR_URL = "https://graph.microsoft.com/v1.0/me/calendarview"
MICROSOFT_GRAPH_EVENTS_URL = "https://graph.microsoft.com/v1.0/me/events"
MICROSOFT_GRAPH_SENDMAIL_URL = "https://graph.microsoft.com/v1.0/me/sendMail"
GOOGLE_GMAIL_DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
GOOGLE_DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
GOOGLE_DRIVE_EXPORT_URL = "https://www.googleapis.com/drive/v3/files/{file_id}/export"


class ProviderIntegrationError(RuntimeError):
    pass


SERVICE_CONFIG: dict[str, dict[str, Any]] = {
    "gmail": {
        "provider": "google",
        "scopes": [
            "openid",
            "email",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
            # Required for users.watch() — registers Gmail push notifications
            "https://www.googleapis.com/auth/gmail.metadata",
        ],
    },
    "google_calendar": {
        "provider": "google",
        "scopes": [
            "openid",
            "email",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/calendar.events",
        ],
    },
    "outlook_mail": {
        "provider": "microsoft",
        "scopes": [
            "openid",
            "email",
            "offline_access",
            "Mail.Read",
            "Mail.ReadWrite",
        ],
    },
    "outlook_calendar": {
        "provider": "microsoft",
        "scopes": [
            "openid",
            "email",
            "offline_access",
            "Calendars.Read",
            "Calendars.ReadWrite",
        ],
    },
    "google_drive": {
        "provider": "google",
        "scopes": [
            "openid",
            "email",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
    },
}


def get_integration_statuses(ceo_id: str) -> list[dict[str, Any]]:
    accounts = get_connected_accounts(ceo_id)
    by_key = {(account.provider, account.service): account for account in accounts}
    statuses = []
    for service, config in SERVICE_CONFIG.items():
        provider = config["provider"]
        account = by_key.get((provider, service))
        if APP_MODE == "dev":
            demo_account = _ensure_demo_account(ceo_id, service)
            if demo_account:
                account = demo_account
                provider = "demo"
        if _writes_disabled_for_ceo(ceo_id) and service in {"gmail", "google_calendar", "outlook_mail", "outlook_calendar"}:
            account = None
        statuses.append(
            {
                "provider": provider,
                "service": service,
                "connected": account is not None,
                "account_email": account.account_email if account else None,
                "expires_at": account.expires_at if account else None,
            }
        )
    return statuses


def build_connect_url(current_user: User, service: str) -> str:
    config = SERVICE_CONFIG.get(service)
    if not config:
        raise ProviderIntegrationError(f"Unsupported service: {service}")
    provider = config["provider"]
    state = _encode_state(
        {
            "ceo_id": current_user.ceo_id,
            "username": current_user.username,
            "service": service,
            "provider": provider,
            "nonce": secrets.token_urlsafe(16),
        }
    )
    redirect_uri = _redirect_uri(provider)

    if provider == "google":
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        if not client_id:
            raise ProviderIntegrationError("GOOGLE_CLIENT_ID is not configured.")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(config["scopes"]),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "include_granted_scopes": "true",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    client_id = os.getenv("MICROSOFT_CLIENT_ID")
    if not client_id:
        raise ProviderIntegrationError("MICROSOFT_CLIENT_ID is not configured.")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(config["scopes"]),
        "response_mode": "query",
        "state": state,
        "prompt": "select_account",
    }
    return f"{MICROSOFT_AUTH_URL}?{urlencode(params)}"


def handle_oauth_callback(provider: str, code: str, state: str) -> str:
    payload = _decode_state(state)
    expected_provider = payload.get("provider")
    if expected_provider != provider:
        raise ProviderIntegrationError("OAuth state/provider mismatch.")
    service = payload["service"]
    ceo_id = payload["ceo_id"]

    token_data = _exchange_code(provider, code)
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    token_type = token_data.get("token_type")
    expires_at = None
    if token_data.get("expires_in"):
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(token_data["expires_in"]))).isoformat()

    account_email = _fetch_account_email(provider, access_token)
    upsert_connected_account(
        ceo_id,
        provider,
        service,
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=token_type,
        expires_at=expires_at,
        account_email=account_email,
        scopes=SERVICE_CONFIG[service]["scopes"],
        metadata={"connected_via": provider},
    )
    return f"{FRONTEND_APP_URL}/?integration=connected&service={service}"


def fetch_email_event(ceo_id: str) -> dict[str, Any]:
    if APP_MODE == "dev":
        demo_payload = _get_demo_event_payload(ceo_id, "gmail")
        if demo_payload:
            return demo_payload
    google_account = _get_valid_account(ceo_id, "google", "gmail")
    if google_account:
        upcoming_events = _safe_fetch_recent_calendar_context(ceo_id)
        return _build_ranked_email_event(_fetch_gmail_threads(google_account, limit=8), upcoming_events=upcoming_events)
    microsoft_account = _get_valid_account(ceo_id, "microsoft", "outlook_mail")
    if microsoft_account:
        upcoming_events = _safe_fetch_recent_calendar_context(ceo_id)
        return _build_ranked_email_event(_fetch_outlook_threads(microsoft_account, limit=8), upcoming_events=upcoming_events)
    raise ProviderIntegrationError("No connected email provider found.")


async def async_fetch_email_event(ceo_id: str) -> dict[str, Any]:
    """
    Async email fetch with LLM-enhanced thread ranking.

    Runs the blocking provider fetch in a thread pool, then applies
    async_rerank_threads to semantically re-score the ranked_threads list.
    """
    import asyncio
    from src.integrations.email_intelligence import async_rerank_threads

    loop = asyncio.get_event_loop()
    event = await loop.run_in_executor(None, fetch_email_event, ceo_id)

    ranked = event.get("ranked_threads") or []
    if ranked:
        enriched = await async_rerank_threads(ranked)
        event = dict(event)
        event["ranked_threads"] = enriched
        # Update primary thread fields if the top thread changed
        if enriched:
            primary = next(
                (t for t in enriched if t.get("importance_level") in {"high", "medium"} and not t.get("suppressed")),
                enriched[0],
            )
            event["importance"] = primary.get("importance_level", "medium").upper()
            event["importance_score"] = primary.get("importance_score", 0)
            event["importance_reasons"] = primary.get("importance_reasons", [])

    return event


def fetch_calendar_event(ceo_id: str) -> dict[str, Any]:
    if APP_MODE == "dev":
        demo_payload = _get_demo_event_payload(ceo_id, "google_calendar")
        if demo_payload:
            return demo_payload
    google_account = _get_valid_account(ceo_id, "google", "google_calendar")
    if google_account:
        event = _fetch_google_calendar_event(google_account)
        event["upcoming_events"] = _fetch_google_calendar_events(google_account, limit=5)
        event["related_threads"] = _safe_fetch_recent_email_context(ceo_id, event)
        return event
    microsoft_account = _get_valid_account(ceo_id, "microsoft", "outlook_calendar")
    if microsoft_account:
        event = _fetch_outlook_calendar_event(microsoft_account)
        event["upcoming_events"] = _fetch_outlook_calendar_events(microsoft_account, limit=5)
        event["related_threads"] = _safe_fetch_recent_email_context(ceo_id, event)
        return event
    raise ProviderIntegrationError("No connected calendar provider found.")


def create_calendar_write(ceo_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    if _writes_disabled_for_ceo(ceo_id):
        raise ProviderIntegrationError("No writable calendar provider found.")
    if APP_MODE == "dev":
        demo_account = _ensure_demo_account(ceo_id, "google_calendar")
        if demo_account:
            return _create_demo_calendar_event(proposal)
    google_account = _get_valid_account(ceo_id, "google", "google_calendar")
    if google_account:
        return _create_google_calendar_event(google_account, proposal)
    microsoft_account = _get_valid_account(ceo_id, "microsoft", "outlook_calendar")
    if microsoft_account:
        return _create_outlook_calendar_event(microsoft_account, proposal)
    raise ProviderIntegrationError("No writable calendar provider found.")


def create_email_draft_write(ceo_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    if _writes_disabled_for_ceo(ceo_id):
        raise ProviderIntegrationError("No writable email provider found.")
    if APP_MODE == "dev":
        demo_account = _ensure_demo_account(ceo_id, "gmail")
        if demo_account:
            return _create_demo_email_draft(proposal)
    google_account = _get_valid_account(ceo_id, "google", "gmail")
    if google_account:
        return _create_gmail_draft(google_account, proposal)
    microsoft_account = _get_valid_account(ceo_id, "microsoft", "outlook_mail")
    if microsoft_account:
        return _create_outlook_draft(microsoft_account, proposal)
    raise ProviderIntegrationError("No writable email provider found.")


def send_email_write(ceo_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
    if _writes_disabled_for_ceo(ceo_id):
        raise ProviderIntegrationError("No writable email provider found.")
    if APP_MODE == "dev":
        demo_account = _ensure_demo_account(ceo_id, "gmail")
        if demo_account:
            return _send_demo_email(proposal)
    google_account = _get_valid_account(ceo_id, "google", "gmail")
    if google_account:
        return _send_gmail_message(google_account, proposal)
    microsoft_account = _get_valid_account(ceo_id, "microsoft", "outlook_mail")
    if microsoft_account:
        return _send_outlook_mail(microsoft_account, proposal)
    raise ProviderIntegrationError("No writable email provider found.")


def _get_valid_account(ceo_id: str, provider: str, service: str) -> Optional[ConnectedAccount]:
    account = get_connected_account(ceo_id, provider, service)
    if not account:
        return None
    if account.expires_at:
        try:
            expires_at = datetime.fromisoformat(account.expires_at.replace("Z", "+00:00"))
            if expires_at <= datetime.now(timezone.utc) + timedelta(minutes=2):
                refreshed = _refresh_account(account)
                if refreshed:
                    return refreshed
        except ValueError:
            pass
    return account


def _refresh_account(account: ConnectedAccount) -> Optional[ConnectedAccount]:
    if not account.refresh_token:
        return account
    provider = account.provider
    service = account.service
    if provider == "google":
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        if not client_id or not client_secret:
            return account
        token_data = _post_form(
            GOOGLE_TOKEN_URL,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": account.refresh_token,
                "grant_type": "refresh_token",
            },
        )
    else:
        client_id = os.getenv("MICROSOFT_CLIENT_ID")
        client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
        if not client_id or not client_secret:
            return account
        token_data = _post_form(
            MICROSOFT_TOKEN_URL,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": account.refresh_token,
                "grant_type": "refresh_token",
                "scope": " ".join(SERVICE_CONFIG[service]["scopes"]),
            },
        )
    access_token = token_data["access_token"]
    expires_at = None
    if token_data.get("expires_in"):
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(token_data["expires_in"]))).isoformat()
    return upsert_connected_account(
        account.ceo_id,
        provider,
        service,
        access_token=access_token,
        refresh_token=token_data.get("refresh_token", account.refresh_token),
        token_type=token_data.get("token_type", account.token_type),
        expires_at=expires_at,
        account_email=account.account_email,
        scopes=account.scopes,
        metadata=account.provider_metadata,
    )


def _exchange_code(provider: str, code: str) -> dict[str, Any]:
    redirect_uri = _redirect_uri(provider)
    if provider == "google":
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise ProviderIntegrationError("Google OAuth credentials are not configured.")
        return _post_form(
            GOOGLE_TOKEN_URL,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )

    client_id = os.getenv("MICROSOFT_CLIENT_ID")
    client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ProviderIntegrationError("Microsoft OAuth credentials are not configured.")
    return _post_form(
        MICROSOFT_TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )


def _fetch_account_email(provider: str, access_token: str) -> Optional[str]:
    if provider == "google":
        data = _authorized_get_json(GOOGLE_USERINFO_URL, access_token)
        return data.get("email")
    data = _authorized_get_json(MICROSOFT_GRAPH_ME_URL, access_token)
    return data.get("mail") or data.get("userPrincipalName")


def _fetch_gmail_threads(account: ConnectedAccount, limit: int = 8) -> list[dict[str, Any]]:
    message_list = _authorized_get_json(
        f"{GOOGLE_GMAIL_LIST_URL}?{urlencode({'maxResults': limit, 'labelIds': 'INBOX'})}",
        account.access_token,
    )
    messages = message_list.get("messages", [])
    if not messages:
        raise ProviderIntegrationError("No Gmail inbox messages found.")
    thread_map: dict[str, dict[str, Any]] = {}
    for item in messages:
        message_id = item["id"]
        data = _authorized_get_json(GOOGLE_GMAIL_GET_URL.format(message_id=message_id), account.access_token)
        headers = {header["name"].lower(): header["value"] for header in data.get("payload", {}).get("headers", [])}
        thread_id = data.get("threadId") or message_id
        sender = headers.get("from", account.account_email or "Unknown sender")
        subject = headers.get("subject", "Inbox Brief")
        received_at = headers.get("date")
        snippet = data.get("snippet", "")
        body_preview = _decode_gmail_body(data.get("payload", {})) or snippet
        thread = thread_map.setdefault(
            thread_id,
            {
                "thread_id": thread_id,
                "subject": subject,
                "participants": [],
                "latest_sender": sender,
                "latest_received_at": received_at,
                "labels": [],
                "provider": "gmail",
                "messages": [],
                "message_count": 0,
                "snippet": snippet,
            },
        )
        thread["latest_sender"] = sender
        thread["latest_received_at"] = received_at
        thread["labels"] = list(dict.fromkeys([*(thread.get("labels") or []), *(data.get("labelIds") or [])]))
        participants = [headers.get("from"), headers.get("to"), headers.get("cc")]
        thread["participants"] = list(
            dict.fromkeys([*(thread.get("participants") or []), *[value for value in participants if value]])
        )
        thread["messages"].append(
            {
                "message_id": message_id,
                "sender": sender,
                "received_at": received_at,
                "body_preview": body_preview,
                "snippet": snippet,
            }
        )
        thread["message_count"] = len(thread["messages"])
    return list(thread_map.values())


def _fetch_outlook_threads(account: ConnectedAccount, limit: int = 8) -> list[dict[str, Any]]:
    url = f"{MICROSOFT_GRAPH_MESSAGES_URL}?$top={limit}&$orderby=receivedDateTime%20DESC"
    data = _authorized_get_json(url, account.access_token)
    messages = data.get("value", [])
    if not messages:
        raise ProviderIntegrationError("No Outlook inbox messages found.")
    thread_map: dict[str, dict[str, Any]] = {}
    for message in messages:
        sender = (((message.get("from") or {}).get("emailAddress") or {}).get("address")) or account.account_email or "Unknown sender"
        thread_id = message.get("conversationId") or message.get("id") or f"thread-{secrets.token_hex(4)}"
        subject = message.get("subject", "Inbox Brief")
        received_at = message.get("receivedDateTime")
        thread = thread_map.setdefault(
            thread_id,
            {
                "thread_id": thread_id,
                "subject": subject,
                "participants": [],
                "latest_sender": sender,
                "latest_received_at": received_at,
                "labels": [],
                "provider": "outlook_mail",
                "messages": [],
                "message_count": 0,
                "snippet": message.get("bodyPreview", ""),
            },
        )
        thread["latest_sender"] = sender
        thread["latest_received_at"] = received_at
        participants = [
            sender,
            *[
                ((recipient.get("emailAddress") or {}).get("address"))
                for recipient in message.get("toRecipients", []) or []
                if (recipient.get("emailAddress") or {}).get("address")
            ],
        ]
        thread["participants"] = list(
            dict.fromkeys([*(thread.get("participants") or []), *[value for value in participants if value]])
        )
        thread["messages"].append(
            {
                "message_id": message.get("id"),
                "sender": sender,
                "received_at": received_at,
                "body_preview": message.get("bodyPreview", ""),
                "snippet": message.get("bodyPreview", ""),
            }
        )
        thread["message_count"] = len(thread["messages"])
    return list(thread_map.values())


def _fetch_google_calendar_event(account: ConnectedAccount) -> dict[str, Any]:
    events = _fetch_google_calendar_events(account, limit=1)
    if not events:
        raise ProviderIntegrationError("No upcoming Google Calendar events found.")
    return events[0]


def _fetch_google_calendar_events(account: ConnectedAccount, limit: int = 5) -> list[dict[str, Any]]:
    params = {
        "maxResults": limit,
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": datetime.now(timezone.utc).isoformat(),
    }
    data = _authorized_get_json(f"{GOOGLE_CALENDAR_EVENTS_URL}?{urlencode(params)}", account.access_token)
    events = data.get("items", [])
    if not events:
        return []
    return [
        {
            "meeting_id": event.get("id", f"gcal-{secrets.token_hex(4)}"),
            "title": event.get("summary", "Upcoming meeting"),
            "starts_at": (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date"),
            "ends_at": (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date"),
            "attendees": [attendee.get("email", "") for attendee in event.get("attendees", []) if attendee.get("email")],
            "agenda": event.get("description"),
            "notes": None,
        }
        for event in events
    ]


def _fetch_outlook_calendar_event(account: ConnectedAccount) -> dict[str, Any]:
    events = _fetch_outlook_calendar_events(account, limit=1)
    if not events:
        raise ProviderIntegrationError("No upcoming Outlook events found.")
    return events[0]


def _fetch_outlook_calendar_events(account: ConnectedAccount, limit: int = 5) -> list[dict[str, Any]]:
    start = datetime.now(timezone.utc).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    url = f"{MICROSOFT_GRAPH_CALENDAR_URL}?startDateTime={start}&endDateTime={end}&$top={limit}"
    data = _authorized_get_json(url, account.access_token)
    events = data.get("value", [])
    if not events:
        return []
    return [
        {
            "meeting_id": event.get("id", f"outlook-{secrets.token_hex(4)}"),
            "title": event.get("subject", "Upcoming meeting"),
            "starts_at": ((event.get("start") or {}).get("dateTime")),
            "ends_at": ((event.get("end") or {}).get("dateTime")),
            "attendees": [
                ((attendee.get("emailAddress") or {}).get("address"))
                for attendee in event.get("attendees", [])
                if (attendee.get("emailAddress") or {}).get("address")
            ],
            "agenda": ((event.get("bodyPreview")) or ""),
            "notes": None,
        }
        for event in events
    ]


def _search_google_drive(
    account: ConnectedAccount,
    query: str,
    max_results: int = 20,
    include_folders: bool = False,
) -> list[dict[str, Any]]:
    """Search Google Drive files by name/content query. Returns file metadata."""
    drive_query = query
    if not include_folders:
        drive_query = f"({query}) and mimeType != 'application/vnd.google-apps.folder'"

    fields = "files(id,name,mimeType,modifiedTime,size,webViewLink,parents)"
    params = urlencode(
        {
            "q": drive_query,
            "pageSize": min(max_results, 100),
            "fields": fields,
            "orderBy": "modifiedTime desc",
        }
    )
    data = _authorized_get_json(f"{GOOGLE_DRIVE_FILES_URL}?{params}", account.access_token)
    return data.get("files", [])


def _export_google_drive_doc(account: ConnectedAccount, file_id: str) -> str:
    """Export a Google Doc, Sheet, or Slide as plain text (max ~50 KB returned)."""
    url = GOOGLE_DRIVE_EXPORT_URL.format(file_id=file_id)
    export_url = f"{url}?mimeType=text%2Fplain"
    request = Request(export_url, headers={"Authorization": f"Bearer {account.access_token}"})
    try:
        with urlopen(request) as response:
            content = response.read().decode("utf-8", errors="replace")
            return content[:50_000]
    except Exception as exc:
        raise ProviderIntegrationError(f"Drive export failed for {file_id}: {exc}") from exc


def _authorized_get_json(url: str, access_token: str) -> dict[str, Any]:
    request = Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ProviderIntegrationError(f"Provider request failed: {exc}") from exc


def _authorized_json_request(url: str, access_token: str, *, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urlopen(request) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body) if response_body else {}
    except Exception as exc:
        raise ProviderIntegrationError(f"Provider write failed: {exc}") from exc


def _post_form(url: str, data: dict[str, Any]) -> dict[str, Any]:
    payload = urlencode(data).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ProviderIntegrationError(f"Token exchange failed: {exc}") from exc


def _create_google_calendar_event(account: ConnectedAccount, proposal: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "summary": proposal.get("title", "Scheduled meeting"),
        "description": proposal.get("description") or proposal.get("notes") or "",
        "start": {
            "dateTime": proposal["starts_at"],
            "timeZone": proposal.get("timezone", "UTC"),
        },
        "end": {
            "dateTime": proposal["ends_at"],
            "timeZone": proposal.get("timezone", "UTC"),
        },
        "attendees": [{"email": attendee} for attendee in proposal.get("attendees", []) if attendee],
    }
    data = _authorized_json_request(
        GOOGLE_CALENDAR_EVENTS_URL,
        account.access_token,
        method="POST",
        payload=payload,
    )
    return {
        "provider": "google_calendar",
        "event_id": data.get("id"),
        "html_link": data.get("htmlLink"),
        "title": data.get("summary", proposal.get("title")),
        "starts_at": ((data.get("start") or {}).get("dateTime")) or proposal.get("starts_at"),
    }


def _create_outlook_calendar_event(account: ConnectedAccount, proposal: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "subject": proposal.get("title", "Scheduled meeting"),
        "body": {
            "contentType": "Text",
            "content": proposal.get("description") or proposal.get("notes") or "",
        },
        "start": {
            "dateTime": proposal["starts_at"],
            "timeZone": proposal.get("timezone", "UTC"),
        },
        "end": {
            "dateTime": proposal["ends_at"],
            "timeZone": proposal.get("timezone", "UTC"),
        },
        "attendees": [
            {"emailAddress": {"address": attendee}, "type": "required"}
            for attendee in proposal.get("attendees", [])
            if attendee
        ],
    }
    data = _authorized_json_request(
        MICROSOFT_GRAPH_EVENTS_URL,
        account.access_token,
        method="POST",
        payload=payload,
    )
    return {
        "provider": "outlook_calendar",
        "event_id": data.get("id"),
        "html_link": data.get("webLink"),
        "title": data.get("subject", proposal.get("title")),
        "starts_at": ((data.get("start") or {}).get("dateTime")) or proposal.get("starts_at"),
    }


def _create_gmail_draft(account: ConnectedAccount, proposal: dict[str, Any]) -> dict[str, Any]:
    message = EmailMessage()
    message["To"] = proposal["to"]
    message["Subject"] = proposal["subject"]
    if proposal.get("cc"):
        message["Cc"] = ", ".join(proposal.get("cc", []))
    message.set_content(proposal["body"])
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    data = _authorized_json_request(
        GOOGLE_GMAIL_DRAFTS_URL,
        account.access_token,
        method="POST",
        payload={"message": {"raw": raw}},
    )
    draft_message = data.get("message", {})
    return {
        "provider": "gmail",
        "draft_id": data.get("id"),
        "message_id": draft_message.get("id"),
        "to": proposal["to"],
        "subject": proposal["subject"],
    }


def _create_outlook_draft(account: ConnectedAccount, proposal: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "subject": proposal["subject"],
        "body": {"contentType": "Text", "content": proposal["body"]},
        "toRecipients": [{"emailAddress": {"address": proposal["to"]}}],
        "ccRecipients": [
            {"emailAddress": {"address": recipient}}
            for recipient in proposal.get("cc", [])
            if recipient
        ],
    }
    data = _authorized_json_request(
        MICROSOFT_GRAPH_MESSAGES_URL,
        account.access_token,
        method="POST",
        payload=payload,
    )
    return {
        "provider": "outlook_mail",
        "draft_id": data.get("id"),
        "to": proposal["to"],
        "subject": proposal["subject"],
    }


def _send_gmail_message(account: ConnectedAccount, proposal: dict[str, Any]) -> dict[str, Any]:
    raw = _build_gmail_raw_message(proposal)
    data = _authorized_json_request(
        GOOGLE_GMAIL_SEND_URL,
        account.access_token,
        method="POST",
        payload={"raw": raw},
    )
    return {
        "provider": "gmail",
        "message_id": data.get("id"),
        "to": proposal["to"],
        "subject": proposal["subject"],
        "sent": True,
    }


def _send_outlook_mail(account: ConnectedAccount, proposal: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "message": {
            "subject": proposal["subject"],
            "body": {"contentType": "Text", "content": proposal["body"]},
            "toRecipients": [{"emailAddress": {"address": proposal["to"]}}],
            "ccRecipients": [
                {"emailAddress": {"address": recipient}}
                for recipient in proposal.get("cc", [])
                if recipient
            ],
        },
        "saveToSentItems": True,
    }
    _authorized_json_request(
        MICROSOFT_GRAPH_SENDMAIL_URL,
        account.access_token,
        method="POST",
        payload=payload,
    )
    return {
        "provider": "outlook_mail",
        "to": proposal["to"],
        "subject": proposal["subject"],
        "sent": True,
    }


def _redirect_uri(provider: str) -> str:
    return f"{BACKEND_APP_URL}/integrations/{provider}/callback"


def _encode_state(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, OAUTH_STATE_SECRET, algorithm="HS256")


def _decode_state(state: str) -> dict[str, Any]:
    try:
        return jwt.decode(state, OAUTH_STATE_SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise ProviderIntegrationError("Invalid OAuth state.") from exc


def _decode_gmail_body(payload: dict[str, Any]) -> str:
    body_data = (payload.get("body") or {}).get("data")
    if body_data:
        try:
            return base64.urlsafe_b64decode(body_data.encode("utf-8")).decode("utf-8", errors="ignore")
        except Exception:
            return ""
    for part in payload.get("parts", []) or []:
        content = _decode_gmail_body(part)
        if content:
            return content
    return ""


def _build_ranked_email_event(threads: list[dict[str, Any]], *, upcoming_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ranked_threads = rank_email_threads(threads)
    if upcoming_events:
        ranked_threads = cross_reference_threads_with_calendar(ranked_threads, upcoming_events)
    primary_thread = select_primary_thread(ranked_threads)
    if not primary_thread:
        raise ProviderIntegrationError("No recent email threads were available.")
    structured_watch = extract_structured_watch_items(ranked_threads, upcoming_events or [])

    return {
        "sender": primary_thread.get("latest_sender", "Unknown sender"),
        "subject": primary_thread.get("subject", "Inbox Brief"),
        "content": _compose_primary_thread_content(primary_thread),
        "thread_id": primary_thread.get("thread_id"),
        "labels": primary_thread.get("labels", []),
        "received_at": primary_thread.get("latest_received_at"),
        "importance": primary_thread.get("importance_level", "medium").upper(),
        "importance_score": primary_thread.get("importance_score", 0),
        "importance_reasons": primary_thread.get("importance_reasons", []),
        "ranked_threads": ranked_threads,
        "upcoming_events": upcoming_events or [],
        "structured_watch": structured_watch,
    }


def _compose_primary_thread_content(thread: dict[str, Any]) -> str:
    body_lines = []
    snippet = str(thread.get("snippet") or "").strip()
    if snippet:
        body_lines.append(snippet)
    for message in thread.get("messages", [])[:2]:
        preview = str(message.get("body_preview") or "").strip()
        if preview and preview not in body_lines:
            body_lines.append(preview)
    return "\n\n".join(body_lines).strip()


def _build_gmail_raw_message(proposal: dict[str, Any]) -> str:
    message = EmailMessage()
    message["To"] = proposal["to"]
    message["Subject"] = proposal["subject"]
    if proposal.get("cc"):
        message["Cc"] = ", ".join(proposal.get("cc", []))
    message.set_content(proposal["body"])
    return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")


def _create_demo_calendar_event(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"demo-calendar-{int(datetime.now(timezone.utc).timestamp())}",
        "title": proposal.get("title", "Scheduled meeting"),
        "starts_at": proposal.get("starts_at"),
        "ends_at": proposal.get("ends_at"),
        "provider": "demo-calendar",
        "html_link": None,
    }


def _create_demo_email_draft(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"demo-draft-{int(datetime.now(timezone.utc).timestamp())}",
        "to": proposal.get("to"),
        "subject": proposal.get("subject"),
        "body": proposal.get("body"),
        "provider": "demo-mail",
        "status": "drafted",
    }


def _send_demo_email(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"demo-send-{int(datetime.now(timezone.utc).timestamp())}",
        "to": proposal.get("to"),
        "subject": proposal.get("subject"),
        "body": proposal.get("body"),
        "provider": "demo-mail",
        "status": "sent",
    }


def _fetch_recent_calendar_context(ceo_id: str) -> list[dict[str, Any]]:
    if APP_MODE == "dev":
        demo_payload = _get_demo_event_payload(ceo_id, "google_calendar")
        if demo_payload:
            return list(demo_payload.get("upcoming_events", []))
    google_account = _get_valid_account(ceo_id, "google", "google_calendar")
    if google_account:
        return _fetch_google_calendar_events(google_account, limit=5)
    microsoft_account = _get_valid_account(ceo_id, "microsoft", "outlook_calendar")
    if microsoft_account:
        return _fetch_outlook_calendar_events(microsoft_account, limit=5)
    return []


def _fetch_recent_email_context(ceo_id: str, event: dict[str, Any]) -> list[dict[str, Any]]:
    if APP_MODE == "dev":
        demo_payload = _get_demo_event_payload(ceo_id, "gmail")
        if demo_payload:
            return cross_reference_threads_with_calendar(list(demo_payload.get("ranked_threads", [])), [event])[:3]
    google_account = _get_valid_account(ceo_id, "google", "gmail")
    if google_account:
        ranked = rank_email_threads(_fetch_gmail_threads(google_account, limit=6))
        return cross_reference_threads_with_calendar(ranked, [event])[:3]
    microsoft_account = _get_valid_account(ceo_id, "microsoft", "outlook_mail")
    if microsoft_account:
        ranked = rank_email_threads(_fetch_outlook_threads(microsoft_account, limit=6))
        return cross_reference_threads_with_calendar(ranked, [event])[:3]
    return []


def _get_demo_event_payload(ceo_id: str, service: str) -> dict[str, Any] | None:
    account = _ensure_demo_account(ceo_id, service)
    if not account:
        return None
    payload = account.provider_metadata.get("event_payload") if isinstance(account.provider_metadata, dict) else None
    base_payload = dict(payload) if isinstance(payload, dict) else {}
    if service == "google_calendar":
        calendar_fixture = load_fixture("gcal_events")
        if isinstance(calendar_fixture, dict) and "upcoming_events" in calendar_fixture:
            base_payload["upcoming_events"] = _normalize_demo_calendar_events(
                list(calendar_fixture.get("upcoming_events", []))
            )
    elif service == "gmail":
        email_fixture = load_fixture("gmail_threads")
        if isinstance(email_fixture, dict) and "ranked_threads" in email_fixture:
            base_payload["ranked_threads"] = list(email_fixture.get("ranked_threads", []))
    return base_payload if base_payload else None


def _normalize_demo_calendar_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        normalized.append(
            {
                **event,
                "starts_at": event.get("starts_at") or event.get("start_time"),
                "ends_at": event.get("ends_at") or event.get("end_time"),
            }
        )
    return normalized


def _ensure_demo_account(ceo_id: str, service: str) -> Optional[ConnectedAccount]:
    canonical_service = "gmail" if service in {"gmail", "outlook_mail"} else "google_calendar"
    return get_connected_account(ceo_id, "demo", canonical_service)


def _writes_disabled_for_ceo(ceo_id: str) -> bool:
    disabled = {
        item.strip()
        for item in os.getenv("AGENTICMIND_DISABLE_WRITES_FOR_CEO_IDS", "").split(",")
        if item.strip()
    }
    return ceo_id in disabled


def _safe_fetch_recent_calendar_context(ceo_id: str) -> list[dict[str, Any]]:
    try:
        return _fetch_recent_calendar_context(ceo_id)
    except ProviderIntegrationError:
        return []


def _safe_fetch_recent_email_context(ceo_id: str, event: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        return _fetch_recent_email_context(ceo_id, event)
    except ProviderIntegrationError:
        return []


# ── Gmail Pub/Sub push integration ───────────────────────────────────────────

GMAIL_WATCH_URL = "https://gmail.googleapis.com/gmail/v1/users/me/watch"
GMAIL_HISTORY_URL = "https://gmail.googleapis.com/gmail/v1/users/me/history"
GMAIL_MESSAGE_GET_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}"
GOOGLE_PUBKEY_URL = "https://www.googleapis.com/oauth2/v3/certs"


def register_gmail_watch(ceo_id: str, pubsub_topic: str) -> dict[str, Any]:
    """Register a Gmail push watch for this CEO's connected account.

    Gmail watches expire after ~7 days and must be renewed.  Call this once
    after OAuth connection and again before expiry (store expiration from the
    response and set a cron job).

    Args:
        ceo_id: CEO identifier.
        pubsub_topic: Full Pub/Sub topic resource name,
                      e.g. 'projects/my-project/topics/gmail-push-agenticmind'

    Returns:
        Gmail watch response: { historyId, expiration (unix ms) }
    """
    account = _get_valid_account(ceo_id, "google", "gmail")
    if not account:
        raise ProviderIntegrationError(f"No connected Gmail account for ceo_id '{ceo_id}'")

    response = _authorized_json_request(
        GMAIL_WATCH_URL,
        account.access_token,
        method="POST",
        payload={
            "topicName": pubsub_topic,
            "labelIds": ["INBOX"],
            "labelFilterBehavior": "INCLUDE",
        },
    )
    # Persist the starting historyId so we don't miss or double-process messages
    upsert_connected_account(
        ceo_id,
        "google",
        "gmail",
        access_token=account.access_token,
        refresh_token=account.refresh_token,
        token_type=account.token_type,
        expires_at=account.expires_at,
        account_email=account.account_email,
        scopes=account.scopes,
        metadata={
            **(account.provider_metadata or {}),
            "gmail_history_id": response.get("historyId"),
            "gmail_watch_expiration": response.get("expiration"),
        },
    )
    return response


def fetch_new_messages_since(ceo_id: str, history_id: str) -> list[dict[str, Any]]:
    """Fetch messages added to INBOX since the given historyId.

    Returns a list of lightweight message dicts ready for EmailIngestionRequest:
    { sender, subject, content, thread_id, labels, received_at }
    """
    account = _get_valid_account(ceo_id, "google", "gmail")
    if not account:
        return []

    try:
        history_data = _authorized_get_json(
            f"{GMAIL_HISTORY_URL}?startHistoryId={history_id}&historyTypes=messageAdded&labelId=INBOX",
            account.access_token,
        )
    except ProviderIntegrationError:
        return []

    messages = []
    for record in history_data.get("history", []):
        for added in record.get("messagesAdded", []):
            msg_id = added.get("message", {}).get("id")
            if not msg_id:
                continue
            try:
                full = _authorized_get_json(
                    GMAIL_MESSAGE_GET_URL.format(message_id=msg_id) + "?format=full",
                    account.access_token,
                )
                messages.append(_parse_gmail_message(full))
            except ProviderIntegrationError:
                continue

    return [m for m in messages if m]


def _parse_gmail_message(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Extract EmailIngestionRequest-compatible fields from a full Gmail message."""
    headers = {
        h["name"].lower(): h["value"]
        for h in raw.get("payload", {}).get("headers", [])
    }
    thread_id = raw.get("threadId")
    labels = raw.get("labelIds", [])
    internal_date_ms = raw.get("internalDate")
    received_at = None
    if internal_date_ms:
        try:
            from datetime import timezone as _tz
            received_at = datetime.fromtimestamp(
                int(internal_date_ms) / 1000, tz=_tz.utc
            ).isoformat()
        except (ValueError, OSError):
            pass

    body = _extract_gmail_body(raw.get("payload", {}))

    return {
        "sender": headers.get("from"),
        "subject": headers.get("subject"),
        "content": body,
        "thread_id": thread_id,
        "labels": labels,
        "received_at": received_at,
    }


def _extract_gmail_body(payload: dict[str, Any]) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        result = _extract_gmail_body(part)
        if result:
            return result
    return ""

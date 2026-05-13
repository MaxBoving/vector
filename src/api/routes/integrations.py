"""Integration status, OAuth connect, and OAuth callback routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from src.api.routes.auth import get_current_user
from src.api.schemas import IntegrationConnectResponse, IntegrationStatusResponse
from src.core.models import User
from src.integrations.providers import (
    ProviderIntegrationError,
    build_connect_url,
    get_integration_statuses,
    handle_oauth_callback,
)

router = APIRouter(tags=["integrations"])


@router.get("/integrations", response_model=list[IntegrationStatusResponse])
async def list_integrations(current_user: User = Depends(get_current_user)):
    return get_integration_statuses(current_user.ceo_id)


@router.post("/integrations/{service}/connect", response_model=IntegrationConnectResponse)
async def connect_integration(service: str, current_user: User = Depends(get_current_user)):
    try:
        auth_url = build_connect_url(current_user, service)
    except ProviderIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider = "google" if service in {"gmail", "google_calendar"} else "microsoft"
    return IntegrationConnectResponse(service=service, provider=provider, auth_url=auth_url)


@router.get("/integrations/{provider}/callback")
async def integration_callback(provider: str, code: str, state: str):
    try:
        redirect_url = handle_oauth_callback(provider, code, state)
    except ProviderIntegrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=redirect_url)

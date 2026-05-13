"""agenticMIND Executive API — application entry point.

Responsible for:
  - FastAPI app creation
  - Router registration
  - DB initialization on startup
  - Gated demo seeding (only when AGENTICMIND_MODE=dev)

All route implementations live in src/api/routes/.
"""
from __future__ import annotations

import logging
import os
import sys

from fastapi import FastAPI

# Load .env before any module reads os.getenv at import time
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — rely on shell env

from src.core.database import init_db

from src.api.routes import auth, assistant, documents, events, integrations

logger = logging.getLogger(__name__)

app = FastAPI(title="agenticMIND Executive API")

# Register all route modules
app.include_router(auth.router)
app.include_router(assistant.router)
app.include_router(documents.router)
app.include_router(events.router)
app.include_router(integrations.router)


@app.get("/")
async def root():
    return {
        "message": "agenticMIND Executive API is Online",
        "docs": "/docs",
        "status": "active",
        "version": "4.0.0",
    }


@app.get("/health")
def health():
    return {"status": "active"}


@app.on_event("startup")
def startup():
    _configure_logging()
    init_db()
    _maybe_seed_demo_context()


def _configure_logging() -> None:
    """Promote app loggers to INFO so request traces are visible in dev."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
        root_logger.addHandler(handler)
    for name in (
        __name__,
        "src.api.routes.assistant",
        "src.api.routes.events",
        "src.workflows.event_runner",
        "src.workflows.request_planner",
        "src.workflows.read_model",
        "src.workflows.action_semantics",
        "src.workflows.routing",
        "src.agents.briefing_agent",
    ):
        logging.getLogger(name).setLevel(logging.INFO)


def _maybe_seed_demo_context() -> None:
    """Seed demo context only in explicit dev mode.

    Gated by AGENTICMIND_MODE=dev.  Production startups are unaffected.
    To seed manually: python -m src.dev.bootstrap
    """
    if os.getenv("AGENTICMIND_MODE", "").strip().lower() != "dev":
        return

    try:
        from src.dev.bootstrap import seed_all_users
        seed_all_users()
    except Exception:
        logger.exception("Dev demo seeding failed — server will still start")

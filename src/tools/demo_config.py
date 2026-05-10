"""Shared dev/demo configuration for connector tools."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEV_DEMO_CEO_ID: str = os.getenv("DEV_DEMO_CEO_ID", "ceo_001")
DEV_DEMO_MODE: bool = os.getenv("DEV_DEMO_MODE", "").lower() in ("1", "true", "yes")

_FIXTURES_DIR = Path(__file__).parent.parent / "dev" / "fixtures"


def demo_lookup_id(ceo_id: str) -> str:
    return DEV_DEMO_CEO_ID if DEV_DEMO_MODE else ceo_id


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file from src/dev/fixtures/<name>.json.

    Returns empty dict if file not found — connector tools handle missing
    fixtures gracefully by falling through to their error path.
    """
    path = _FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)

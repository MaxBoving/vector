"""Tests that world fixtures load correctly and connector tools use them."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "src" / "dev" / "fixtures"


def test_load_fixture_helper_returns_empty_for_missing():
    from src.tools.demo_config import load_fixture
    assert load_fixture("does_not_exist") == {}


def test_load_fixture_helper_returns_data_for_existing():
    from src.tools.demo_config import load_fixture
    data = load_fixture("gmail_threads")
    assert "ranked_threads" in data


def test_gmail_fixture_structure():
    path = FIXTURES_DIR / "gmail_threads.json"
    assert path.exists(), "gmail_threads.json missing"
    data = json.loads(path.read_text())
    threads = data.get("ranked_threads", [])
    assert len(threads) >= 15
    for t in threads:
        assert "thread_id" in t
        assert "subject" in t
        assert "importance_score" in t
        assert "body_preview" in t


def test_gcal_fixture_structure():
    path = FIXTURES_DIR / "gcal_events.json"
    assert path.exists()
    data = json.loads(path.read_text())
    events = data.get("upcoming_events", [])
    assert len(events) >= 8
    for e in events:
        assert "meeting_id" in e
        assert "start_time" in e
        assert "attendees" in e


def test_crm_fixture_structure():
    path = FIXTURES_DIR / "crm_data.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert "deals" in data
    assert len(data["deals"]) >= 8
    for deal in data["deals"]:
        assert "deal_id" in deal
        assert "stage" in deal
        assert "amount" in deal


def test_drive_fixture_structure():
    path = FIXTURES_DIR / "drive_files.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert "files" in data
    assert len(data["files"]) >= 10
    for f in data["files"]:
        assert "id" in f
        assert "name" in f
        assert "content" in f


def test_slack_fixture_structure():
    path = FIXTURES_DIR / "slack_messages.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert "channels" in data
    assert len(data["channels"]) >= 4


def test_financials_fixture_structure():
    path = FIXTURES_DIR / "financials.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert "current_metrics" in data
    assert "monthly_pnl" in data
    assert len(data["monthly_pnl"]) == 6
    assert data["current_metrics"]["arr"] == 14200000


def test_gmail_fixture_has_noise_patterns():
    data = json.loads((FIXTURES_DIR / "gmail_threads.json").read_text())
    threads = data["ranked_threads"]
    categories = [t["category"] for t in threads]
    assert "automated" in categories, "Should have automated notification threads"
    scores = [t["importance_score"] for t in threads]
    assert min(scores) < 0.2, "Should have low-importance automated noise"
    assert max(scores) > 0.9, "Should have critical importance threads"


def test_crm_fixture_has_noise_patterns():
    data = json.loads((FIXTURES_DIR / "crm_data.json").read_text())
    deals = data["deals"]
    stalled = [d for d in deals if d["stage"] == "Stalled"]
    assert len(stalled) >= 1, "Should have at least one stalled deal"
    risk_deals = [d for d in deals if d.get("risk_flags")]
    assert len(risk_deals) >= 2, "Should have deals with risk flags"


def test_seed_world_populates_db(tmp_path, monkeypatch):
    """seed_world() creates all required DB records."""
    import os
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    from src.core.database import init_db
    init_db()

    from seed_world import seed_world
    seed_world(ceo_id="test_jordan", username="jordan.test", password="test123")

    from sqlmodel import Session, select
    from src.core.database import engine
    from src.core.models import (
        CEOPreferences, CompanyState, IncomingSignal, User
    )

    with Session(engine) as session:
        user = session.exec(select(User).where(User.ceo_id == "test_jordan")).first()
        assert user is not None

        prefs = session.exec(
            select(CEOPreferences).where(CEOPreferences.ceo_id == "test_jordan")
        ).first()
        assert prefs is not None
        assert prefs.tone == "concise"

        state = session.exec(
            select(CompanyState).where(CompanyState.ceo_id == "test_jordan")
        ).first()
        assert state is not None
        assert state.arr == 14200000

        signals = session.exec(
            select(IncomingSignal).where(IncomingSignal.ceo_id == "test_jordan")
        ).all()
        assert len(signals) >= 6

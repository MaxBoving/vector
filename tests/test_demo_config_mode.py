from __future__ import annotations

import importlib


def test_demo_mode_defaults_to_dev_when_unset(monkeypatch):
    monkeypatch.delenv("DEV_DEMO_MODE", raising=False)
    monkeypatch.setenv("AGENTICMIND_MODE", "dev")

    import src.tools.demo_config as demo_config

    reloaded = importlib.reload(demo_config)
    assert reloaded.DEV_DEMO_MODE is True
    assert reloaded.demo_lookup_id("ceo_123") == reloaded.DEV_DEMO_CEO_ID

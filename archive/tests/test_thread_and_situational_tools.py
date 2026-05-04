import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tools.base import ToolContext
from src.tools.situational_tools import GetSituationalProfileTool, UpdateSituationalProfileTool
from src.tools.thread_tools import GetLiveContextTool, ResolveThreadEntryTool, WriteThreadEntryTool


def test_write_thread_entry_updates_live_context(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    class SavedEntry:
        id = 42

    def fake_append(entry):
        calls.append(("append", {"entry_type": entry.entry_type, "content": entry.content}))
        return SavedEntry()

    def fake_update(conversation_id, **kwargs):
        calls.append(("update", {"conversation_id": conversation_id, **kwargs}))
        return None

    monkeypatch.setattr("src.tools.thread_tools.append_thread_entry", fake_append)
    monkeypatch.setattr("src.tools.thread_tools.update_live_context", fake_update)

    tool = WriteThreadEntryTool()
    result = tool.invoke(
        ToolContext(ceo_id="ceo_test", interaction_id=7, metadata={"conversation_id": "conv_1"}),
        entry_type="schedule",
        actor="briefing_agent",
        content="Built the weekly plan.",
        structured_payload={"blocks": [{"title": "Board prep"}], "meetings": [], "deadlines": []},
        entities=["Board Pack"],
        turn=3,
        workflow_type="schedule_planning",
    )

    assert result.success is True
    assert result.data["entry_id"] == 42
    assert any(call[0] == "append" for call in calls)
    assert any(call[0] == "update" and call[1].get("current_schedule") for call in calls)


def test_get_live_context_tool_reads_current_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.tools.thread_tools.get_or_create_live_context",
        lambda ceo_id, conversation_id: type(
            "LiveContext",
            (),
            {"model_dump": lambda self: {"conversation_id": conversation_id, "ceo_id": ceo_id, "turn_count": 2}},
        )(),
    )

    result = GetLiveContextTool().invoke(
        ToolContext(ceo_id="ceo_test", metadata={"conversation_id": "conv_1"})
    )

    assert result.success is True
    assert result.data["live_context"]["turn_count"] == 2


def test_situational_tools_round_trip(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.tools.situational_tools.get_or_create_situational_profile",
        lambda ceo_id: type("Profile", (), {"model_dump": lambda self: {"ceo_id": ceo_id, "operating_mode": "standard"}})(),
    )
    monkeypatch.setattr(
        "src.tools.situational_tools.update_situational_profile",
        lambda ceo_id, **kwargs: type(
            "Profile",
            (),
            {"model_dump": lambda self: {"ceo_id": ceo_id, "operating_mode": kwargs.get("operating_mode", "standard"), "active_pressures": [kwargs.get("add_pressure")] if kwargs.get("add_pressure") else []}},
        )(),
    )

    get_result = GetSituationalProfileTool().invoke(ToolContext(ceo_id="ceo_test"))
    update_result = UpdateSituationalProfileTool().invoke(
        ToolContext(ceo_id="ceo_test"),
        operating_mode="strategic",
        add_pressure="Board-related pressure raised Mar 30",
        updated_by="briefing_agent",
    )

    assert get_result.success is True
    assert get_result.data["situational_profile"]["operating_mode"] == "standard"
    assert update_result.success is True
    assert update_result.data["situational_profile"]["operating_mode"] == "strategic"
    assert update_result.data["situational_profile"]["active_pressures"] == ["Board-related pressure raised Mar 30"]


def test_resolve_thread_entry_prunes_live_context(monkeypatch) -> None:
    class ResolvedEntry:
        def __init__(self, entry_id: int, content: str):
            self.id = entry_id
            self.content = content

    updates: list[dict] = []

    monkeypatch.setattr(
        "src.tools.thread_tools.resolve_thread_entries",
        lambda conversation_id, **kwargs: [ResolvedEntry(7, "Cloud containment option")],
    )
    monkeypatch.setattr(
        "src.tools.thread_tools.update_live_context",
        lambda conversation_id, **kwargs: updates.append({"conversation_id": conversation_id, **kwargs}),
    )

    result = ResolveThreadEntryTool().invoke(
        ToolContext(ceo_id="ceo_test", metadata={"conversation_id": "conv_1"}),
        entry_type="decision",
        match_text="Cloud containment option",
        entities=["Board Pack"],
        resolution_note="Decision captured in board memo.",
    )

    assert result.success is True
    assert result.data["resolved_count"] == 1
    assert updates[0]["resolved_decisions"] == ["Cloud containment option"]
    assert updates[0]["resolved_entities"] == ["Board Pack"]

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.database import delete_ceo_memory, get_ceo_memories, init_db
from src.tools.base import ToolContext
from src.tools.memory_tools import MemoryManagementTool


TEST_CEO_ID = "memory-gov-test"


def _clear_memories() -> None:
    for memory in get_ceo_memories(TEST_CEO_ID, limit=200):
        delete_ceo_memory(memory.memory_id)


def setup_function() -> None:
    init_db()
    _clear_memories()


def teardown_function() -> None:
    _clear_memories()


def test_auto_save_requires_medium_or_high_confidence() -> None:
    tool = MemoryManagementTool()
    result = tool.invoke(
        ToolContext(ceo_id=TEST_CEO_ID, interaction_id=1),
        action="save",
        auto_save=True,
        memory_type="commitment",
        title="Finance will send the update by Friday",
        content="Finance will send the investor update by Friday afternoon.",
        confidence="low",
        confidence_score=0.4,
        evidence_state="mixed",
        tags=["auto", "report"],
    )

    assert result.success is False
    assert result.data["skipped"] is True
    assert get_ceo_memories(TEST_CEO_ID, limit=20) == []


def test_auto_save_rejects_sparse_evidence() -> None:
    tool = MemoryManagementTool()
    result = tool.invoke(
        ToolContext(ceo_id=TEST_CEO_ID, interaction_id=1),
        action="save",
        auto_save=True,
        memory_type="commitment",
        title="CEO + Finance: lock board packet narrative",
        content="CEO + Finance: lock the board packet narrative before the finance close review.",
        confidence="medium",
        confidence_score=0.65,
        evidence_state="sparse",
        tags=["auto", "report"],
    )

    assert result.success is False
    assert "sparse" in result.error.lower()


def test_auto_save_dedupes_near_duplicate_memory() -> None:
    tool = MemoryManagementTool()
    context = ToolContext(ceo_id=TEST_CEO_ID, interaction_id=1)
    first = tool.invoke(
        context,
        action="save",
        auto_save=True,
        memory_type="commitment",
        title="CEO + Finance: lock board packet narrative",
        content="CEO + Finance: lock the board packet narrative before Friday's close review.",
        confidence="high",
        confidence_score=0.82,
        evidence_state="mixed",
        tags=["auto", "report"],
    )
    second = tool.invoke(
        context,
        action="save",
        auto_save=True,
        memory_type="commitment",
        title="CEO + Finance: lock board packet narrative",
        content="CEO + Finance: lock the board packet narrative before Friday close review.",
        confidence="high",
        confidence_score=0.83,
        evidence_state="mixed",
        tags=["auto", "report"],
    )

    memories = get_ceo_memories(TEST_CEO_ID, limit=20)
    assert first.success is True
    assert second.success is True
    assert second.data["deduped"] is True
    assert len(memories) == 1


def test_auto_save_sets_expiration_defaults_by_memory_type() -> None:
    tool = MemoryManagementTool()
    context = ToolContext(ceo_id=TEST_CEO_ID, interaction_id=1)
    commitment = tool.invoke(
        context,
        action="save",
        auto_save=True,
        memory_type="commitment",
        title="Operations: send recap today",
        content="Operations: send the close recap today before 5pm.",
        confidence="high",
        confidence_score=0.9,
        evidence_state="strong",
        tags=["auto", "briefing"],
    )
    decision = tool.invoke(
        context,
        action="save",
        auto_save=True,
        memory_type="decision",
        title="CEO approved the Europe pricing test",
        content="CEO approved the Europe pricing test and committed to review results next month.",
        confidence="high",
        confidence_score=0.9,
        evidence_state="strong",
        tags=["auto", "report"],
    )

    memories = {memory.memory_type: memory for memory in get_ceo_memories(TEST_CEO_ID, limit=20)}
    assert commitment.success is True
    assert decision.success is True
    assert memories["commitment"].expires_at is not None
    assert memories["decision"].expires_at is None

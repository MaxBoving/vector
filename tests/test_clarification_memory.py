from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlmodel import Session, create_engine, select

from src.core.database import init_db
from src.core.models import AssistantConversation, CEOPreferences, ConversationLiveContext, SessionInteraction
from src.workflows.clarification_memory import record_clarification_follow_up
from src.workflows.read_model import _read_model_metadata


def test_init_db_adds_resolved_clarifications_column(tmp_path, monkeypatch):
    import src.core.database as database

    db_path = Path(tmp_path) / "clarification_memory.sqlite3"
    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)

    init_db()

    with test_engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(conversationlivecontext)").fetchall()
        }

    assert "resolved_clarifications" in columns


def test_read_model_metadata_exposes_resolved_clarifications(monkeypatch):
    interaction = SimpleNamespace(
        ceo_id="ceo_test",
        gate_type=None,
        missing_data_context=None,
        current_stage=None,
        response='{"conversation_id": "conv_test"}',
        status="COMPLETED",
        query="How should I frame this?",
        timestamp="2026-05-10T00:00:00+00:00",
        id=1,
    )
    workflow_run = None
    live_context = SimpleNamespace(
        turn_count=4,
        current_schedule={},
        open_decisions=[],
        open_commitments=[],
        entities_in_play={},
        resolved_clarifications={"output_format": "board_presentation"},
        updated_at="2026-05-10T00:00:00+00:00",
    )
    situational_profile = SimpleNamespace(
        operating_mode="standard",
        active_pressures=[],
        recurring_topics=[],
        open_threads=[],
        relationship_obligations=[],
        inferred_blind_spots=[],
        updated_at="2026-05-10T00:00:00+00:00",
    )

    monkeypatch.setattr(
        "src.workflows.read_model.get_or_create_live_context",
        lambda ceo_id, conversation_id: live_context,
    )
    monkeypatch.setattr(
        "src.workflows.read_model.get_or_create_situational_profile",
        lambda ceo_id: situational_profile,
    )

    metadata = _read_model_metadata(interaction, workflow_run)

    assert metadata["resolved_clarifications"] == {"output_format": "board_presentation"}
    assert metadata["live_context"]["resolved_clarifications"] == {"output_format": "board_presentation"}


def test_record_clarification_follow_up_writes_conversation_state(tmp_path, monkeypatch):
    import src.core.database as database

    db_path = Path(tmp_path) / "clarification_follow_up.sqlite3"
    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)

    init_db()

    ceo_id = "ceo_test"
    conversation_id = "conv:ceo_test:clarification"
    with Session(test_engine) as session:
        conversation = AssistantConversation(
            conversation_id=conversation_id,
            ceo_id=ceo_id,
            title="Clarification",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        clarification = SessionInteraction(
            ceo_id=ceo_id,
            query="How should I frame this?",
            response=(
                '{"response_type":"clarification","trust":{"question_options":[{"question":"Frame this for your own decision or for the board?","options":[{"label":"Board presentation","value":"board_presentation","apply_text":"Format this for a board presentation — structured and polished.","description":"Board-ready language."}],"offer_type":"clarification"}]}}'
            ),
            status="COMPLETED",
            timestamp="2026-05-10T00:00:00+00:00",
        )
        session.add(clarification)
        session.commit()
        session.refresh(clarification)
        clarification_id = clarification.id
        conversation.interaction_ids = [clarification.id]
        session.add(conversation)
        session.commit()

    result = record_clarification_follow_up(
        ceo_id=ceo_id,
        conversation_id=conversation_id,
        answer_text="Format this for a board presentation — structured and polished.",
        source_interaction_id=clarification_id,
        source_response_type="clarification",
        selected_option_value="board_presentation",
        selected_option_label="Board presentation",
    )

    assert result == {
        "signal_type": "output_format",
        "signal_value": "board_presentation",
        "option_value": "board_presentation",
        "option_label": "Board presentation",
    }

    with Session(test_engine) as session:
        live_context = session.exec(
            select(ConversationLiveContext).where(ConversationLiveContext.ceo_id == ceo_id)
        ).first()
        prefs = session.exec(
            select(CEOPreferences).where(CEOPreferences.ceo_id == ceo_id)
        ).first()

    assert live_context is not None
    assert live_context.resolved_clarifications == {"output_format": "board_presentation"}
    assert prefs is not None
    assert prefs.learned_defaults["output_format"]["board_presentation"] == 1


def test_record_clarification_follow_up_uses_latest_clarification_history(tmp_path, monkeypatch):
    import src.core.database as database

    db_path = Path(tmp_path) / "clarification_follow_up_fallback.sqlite3"
    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)

    init_db()

    ceo_id = "ceo_test"
    conversation_id = "conv:ceo_test:clarification"
    with Session(test_engine) as session:
        conversation = AssistantConversation(
            conversation_id=conversation_id,
            ceo_id=ceo_id,
            title="Clarification",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        clarification = SessionInteraction(
            ceo_id=ceo_id,
            query="Which time anchor should I use?",
            response=(
                '{"response_type":"clarification","trust":{"question_options":[{"question":"Should I anchor this to this month or quarter close?","options":[{"label":"Quarter close","value":"quarter_close","apply_text":"Anchor this to quarter close.","description":"Quarter close"}],"offer_type":"clarification"}]}}'
            ),
            status="COMPLETED",
            timestamp="2026-05-10T00:00:00+00:00",
        )
        session.add(clarification)
        session.commit()
        session.refresh(clarification)
        conversation.interaction_ids = [clarification.id]
        session.add(conversation)
        session.commit()

    result = record_clarification_follow_up(
        ceo_id=ceo_id,
        conversation_id=conversation_id,
        answer_text="Anchor this to quarter close.",
    )

    assert result == {
        "signal_type": "time_anchor",
        "signal_value": "quarter_close",
        "option_value": "quarter_close",
        "option_label": "Quarter close",
    }

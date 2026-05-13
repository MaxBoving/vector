from __future__ import annotations

import json
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
    assert "clarification_resolutions" in columns


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
        clarification_resolutions=[
            {
                "ceo_id": "ceo_test",
                "conversation_id": "conv_test",
                "source_interaction_id": 1,
                "source_response_type": "clarification",
                "gate_type": "CLARIFICATION_REQUIRED",
                "question": "Frame this for your own decision or for the board?",
                "selected_option": {
                    "label": "Board presentation",
                    "value": "board_presentation",
                    "apply_text": "Format this for a board presentation — structured and polished.",
                    "description": "Board-ready language.",
                },
                "signal_type": "output_format",
                "signal_value": "board_presentation",
                "answer_text": "Format this for a board presentation — structured and polished.",
                "match_strategy": "explicit_value",
                "recorded_at": "2026-05-10T00:00:00+00:00",
            }
        ],
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
    assert metadata["clarification_resolutions"][0]["signal_value"] == "board_presentation"


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
        "match_strategy": "explicit_value",
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
    assert live_context.clarification_resolutions is not None
    assert live_context.clarification_resolutions[-1]["selected_option"]["value"] == "board_presentation"
    assert live_context.clarification_resolutions[-1]["signal_value"] == "board_presentation"
    assert prefs is not None
    assert prefs.learned_defaults["output_format"]["board_presentation"] == 1


def test_record_clarification_follow_up_reuses_persisted_resolution(tmp_path, monkeypatch):
    import src.core.database as database

    db_path = Path(tmp_path) / "clarification_follow_up_reuse.sqlite3"
    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)

    init_db()

    ceo_id = "ceo_test"
    conversation_id = "conv:ceo_test:reuse"
    with Session(test_engine) as session:
        conversation = AssistantConversation(
            conversation_id=conversation_id,
            ceo_id=ceo_id,
            title="Reuse",
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

        live_context = ConversationLiveContext(
            conversation_id=conversation_id,
            ceo_id=ceo_id,
            resolved_clarifications={"output_format": "board_presentation"},
            clarification_resolutions=[
                {
                    "ceo_id": ceo_id,
                    "conversation_id": conversation_id,
                    "source_interaction_id": clarification_id,
                    "source_response_type": "clarification",
                    "gate_type": "CLARIFICATION_REQUIRED",
                    "question": "Frame this for your own decision or for the board?",
                    "selected_option": {
                        "label": "Board presentation",
                        "value": "board_presentation",
                        "apply_text": "Format this for a board presentation — structured and polished.",
                        "description": "Board-ready language.",
                    },
                    "signal_type": "output_format",
                    "signal_value": "board_presentation",
                    "answer_text": "Format this for a board presentation — structured and polished.",
                    "match_strategy": "explicit_value",
                    "recorded_at": "2026-05-10T00:01:00+00:00",
                }
            ],
        )
        session.add(live_context)
        session.commit()

    result = record_clarification_follow_up(
        ceo_id=ceo_id,
        conversation_id=conversation_id,
        answer_text="This is a completely different reply that should be ignored.",
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
        "match_strategy": "explicit_value",
    }

    with Session(test_engine) as session:
        prefs = session.exec(
            select(CEOPreferences).where(CEOPreferences.ceo_id == ceo_id)
        ).first()

    assert prefs is None


def test_record_clarification_follow_up_learns_presentation_style(tmp_path, monkeypatch):
    import src.core.database as database

    db_path = Path(tmp_path) / "clarification_follow_up_style.sqlite3"
    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)

    init_db()

    ceo_id = "ceo_test"
    conversation_id = "conv:ceo_test:style"
    with Session(test_engine) as session:
        conversation = AssistantConversation(
            conversation_id=conversation_id,
            ceo_id=ceo_id,
            title="Style",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        clarification = SessionInteraction(
            ceo_id=ceo_id,
            query="How should I frame this?",
            response=(
                '{"response_type":"clarification","trust":{"question_options":[{"question":"Do you want this as a list recap or a narrative recap?","options":[{"label":"List recap","value":"list_form","apply_text":"Format this as a concise list recap with clear bullets.","description":"Keep the recap in bullets and short sections."}],"offer_type":"clarification"}]}}'
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
        answer_text="Format this as a concise list recap with clear bullets.",
        source_interaction_id=clarification_id,
        source_response_type="clarification",
        selected_option_value="list_form",
        selected_option_label="List recap",
    )

    assert result == {
        "signal_type": "presentation_style",
        "signal_value": "list_form",
        "option_value": "list_form",
        "option_label": "List recap",
        "match_strategy": "explicit_value",
    }

    with Session(test_engine) as session:
        prefs = session.exec(
            select(CEOPreferences).where(CEOPreferences.ceo_id == ceo_id)
        ).first()

    assert prefs is not None
    assert prefs.learned_defaults["presentation_style"]["list_form"] == 1


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
        "match_strategy": "text_match",
    }


def test_record_clarification_follow_up_falls_back_to_top_level_clarification_options(tmp_path, monkeypatch):
    import src.core.database as database

    db_path = Path(tmp_path) / "clarification_follow_up_legacy.sqlite3"
    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)

    init_db()

    ceo_id = "ceo_test"
    conversation_id = "conv:ceo_test:legacy"
    with Session(test_engine) as session:
        conversation = AssistantConversation(
            conversation_id=conversation_id,
            ceo_id=ceo_id,
            title="Legacy Clarification",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        clarification = SessionInteraction(
            ceo_id=ceo_id,
            query="How should I frame this?",
            response=(
                '{"response_type":"clarification","clarification_options":[{"question":"Do you want this as a timeline or a compact list?","options":[{"label":"Compact list","value":"list_form","apply_text":"Render this as a compact list of priorities and actions.","description":"Keep it concise."}],"offer_type":"clarification"}]}'
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
        answer_text="Render this as a compact list of priorities and actions.",
        source_interaction_id=clarification_id,
        source_response_type="clarification",
        selected_option_value="list_form",
        selected_option_label="Compact list",
    )

    assert result == {
        "signal_type": "presentation_style",
        "signal_value": "list_form",
        "option_value": "list_form",
        "option_label": "Compact list",
        "match_strategy": "explicit_value",
    }


def test_record_clarification_follow_up_reads_gate_context_without_response_payload(tmp_path, monkeypatch):
    import src.core.database as database

    db_path = Path(tmp_path) / "clarification_follow_up_gate.sqlite3"
    test_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)

    init_db()

    ceo_id = "ceo_test"
    conversation_id = "conv:ceo_test:gate"
    gate = {
        "gate_type": "CLARIFICATION_REQUIRED",
        "reason": "Do you want this as a list recap or a narrative recap?",
        "options": [
            {
                "question": "Do you want this as a list recap or a narrative recap?",
                "options": [
                    {
                        "label": "List recap",
                        "value": "list_form",
                        "apply_text": "Format this as a concise list recap with clear bullets.",
                        "description": "Keep the recap in bullets and short sections.",
                    },
                    {
                        "label": "Narrative recap",
                        "value": "narrative_recap",
                        "apply_text": "Format this as a narrative recap with prose.",
                        "description": "Use prose instead of bullets.",
                    },
                ],
                "offer_type": "clarification",
            }
        ],
        "context": {"original_query": "Give me a recap of the week."},
    }
    with Session(test_engine) as session:
        conversation = AssistantConversation(
            conversation_id=conversation_id,
            ceo_id=ceo_id,
            title="Gate Clarification",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        clarification = SessionInteraction(
            ceo_id=ceo_id,
            query="Give me a recap of the week.",
            response=None,
            status="COMPLETED",
            gate_type="CLARIFICATION_REQUIRED",
            missing_data_context=json.dumps({"gate": gate}),
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
        answer_text="Format this as a narrative recap with prose.",
        source_interaction_id=clarification_id,
        selected_option_value="narrative_recap",
        selected_option_label="Narrative recap",
    )

    assert result == {
        "signal_type": "presentation_style",
        "signal_value": "narrative_recap",
        "option_value": "narrative_recap",
        "option_label": "Narrative recap",
        "match_strategy": "explicit_value",
    }

from datetime import datetime
import json
import secrets
from sqlmodel import SQLModel, create_engine, Session, select
from typing import Optional, Any
from .models import AssistantConversation, AssistantProject, CEOMemory, CEOSituationalProfile, CompanyIdentityProfile, CompanyProfileRecord, CompanyState, CEOPreferences, ConnectedAccount, ConversationLiveContext, ConversationThreadEntry, SessionInteraction, AuditLog, User, ApprovedDecision, IncomingSignal
import os

sqlite_url = "sqlite:///./agenticmind.db"
engine = create_engine(sqlite_url, echo=False)

def init_db():
    SQLModel.metadata.create_all(engine)
    with engine.begin() as connection:
        existing_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(assistantconversation)").fetchall()
        }
        if "pinned" not in existing_columns:
            connection.exec_driver_sql("ALTER TABLE assistantconversation ADD COLUMN pinned BOOLEAN DEFAULT 0")
        if "archived" not in existing_columns:
            connection.exec_driver_sql("ALTER TABLE assistantconversation ADD COLUMN archived BOOLEAN DEFAULT 0")
        preference_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(ceopreferences)").fetchall()
        }
        if "priority_senders" not in preference_columns:
            connection.exec_driver_sql("ALTER TABLE ceopreferences ADD COLUMN priority_senders JSON")
        if "priority_domains" not in preference_columns:
            connection.exec_driver_sql("ALTER TABLE ceopreferences ADD COLUMN priority_domains JSON")
        if "ignored_senders" not in preference_columns:
            connection.exec_driver_sql("ALTER TABLE ceopreferences ADD COLUMN ignored_senders JSON")
        if "ignored_domains" not in preference_columns:
            connection.exec_driver_sql("ALTER TABLE ceopreferences ADD COLUMN ignored_domains JSON")
        if "learned_defaults" not in preference_columns:
            connection.exec_driver_sql("ALTER TABLE ceopreferences ADD COLUMN learned_defaults JSON")
        user_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(user)").fetchall()
        }
        if "openclaw_webhook_token" not in user_columns:
            connection.exec_driver_sql("ALTER TABLE user ADD COLUMN openclaw_webhook_token VARCHAR")
        existing_indexes = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA index_list(user)").fetchall()
        }
        if "ix_user_openclaw_webhook_token" not in existing_indexes:
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_openclaw_webhook_token "
                "ON user (openclaw_webhook_token)"
            )
        thread_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(conversationthreadentry)").fetchall()
        } if connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversationthreadentry'"
        ).fetchone() else set()
        if thread_columns:
            if "status" not in thread_columns:
                connection.exec_driver_sql("ALTER TABLE conversationthreadentry ADD COLUMN status VARCHAR DEFAULT 'open'")
            if "parent_entry_id" not in thread_columns:
                connection.exec_driver_sql("ALTER TABLE conversationthreadentry ADD COLUMN parent_entry_id INTEGER")
            if "resolution_note" not in thread_columns:
                connection.exec_driver_sql("ALTER TABLE conversationthreadentry ADD COLUMN resolution_note VARCHAR")
            if "resolved_at" not in thread_columns:
                connection.exec_driver_sql("ALTER TABLE conversationthreadentry ADD COLUMN resolved_at VARCHAR")
        live_context_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(conversationlivecontext)").fetchall()
        } if connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversationlivecontext'"
        ).fetchone() else set()
        if live_context_columns and "intent_state" not in live_context_columns:
            connection.exec_driver_sql("ALTER TABLE conversationlivecontext ADD COLUMN intent_state JSON")
        if live_context_columns and "unified_memory" not in live_context_columns:
            connection.exec_driver_sql("ALTER TABLE conversationlivecontext ADD COLUMN unified_memory JSON")
        if live_context_columns and "pending_actions" not in live_context_columns:
            connection.exec_driver_sql("ALTER TABLE conversationlivecontext ADD COLUMN pending_actions JSON")

def get_ceo_preferences(ceo_id: str) -> Optional[CEOPreferences]:
    with Session(engine) as session:
        statement = select(CEOPreferences).where(CEOPreferences.ceo_id == ceo_id)
        return session.exec(statement).first()


def get_or_create_ceo_preferences(ceo_id: str) -> CEOPreferences:
    with Session(engine) as session:
        statement = select(CEOPreferences).where(CEOPreferences.ceo_id == ceo_id)
        preferences = session.exec(statement).first()
        if preferences:
            return preferences
        preferences = CEOPreferences(ceo_id=ceo_id)
        session.add(preferences)
        session.commit()
        session.refresh(preferences)
        return preferences


def update_watcher_preferences(
    ceo_id: str,
    *,
    action: str,
    sender: Optional[str] = None,
    domain: Optional[str] = None,
) -> CEOPreferences:
    normalized_sender = str(sender or "").strip().lower()
    normalized_domain = str(domain or "").strip().lower()

    with Session(engine) as session:
        statement = select(CEOPreferences).where(CEOPreferences.ceo_id == ceo_id)
        preferences = session.exec(statement).first()
        if not preferences:
            preferences = CEOPreferences(ceo_id=ceo_id)

        priority_senders = {str(item).strip().lower() for item in (preferences.priority_senders or []) if str(item).strip()}
        priority_domains = {str(item).strip().lower() for item in (preferences.priority_domains or []) if str(item).strip()}
        ignored_senders = {str(item).strip().lower() for item in (preferences.ignored_senders or []) if str(item).strip()}
        ignored_domains = {str(item).strip().lower() for item in (preferences.ignored_domains or []) if str(item).strip()}

        if action == "prioritize_sender" and normalized_sender:
            priority_senders.add(normalized_sender)
            ignored_senders.discard(normalized_sender)
        elif action == "ignore_sender" and normalized_sender:
            ignored_senders.add(normalized_sender)
            priority_senders.discard(normalized_sender)
        elif action == "ignore_domain" and normalized_domain:
            ignored_domains.add(normalized_domain)
            priority_domains.discard(normalized_domain)
        elif action == "prioritize_domain" and normalized_domain:
            priority_domains.add(normalized_domain)
            ignored_domains.discard(normalized_domain)

        preferences.priority_senders = sorted(priority_senders)
        preferences.priority_domains = sorted(priority_domains)
        preferences.ignored_senders = sorted(ignored_senders)
        preferences.ignored_domains = sorted(ignored_domains)
        session.add(preferences)
        session.commit()
        session.refresh(preferences)
        return preferences

_PREFERENCE_CONFIDENCE_THRESHOLD = 3  # minimum selections before we treat it as a default


def record_preference_signal(ceo_id: str, *, signal_type: str, value: str) -> None:
    """
    Increment a learned preference counter and update the dominant default when confident.

    signal_type examples: "output_format", "framing", "depth"
    value examples: "personal_decision", "board_presentation", "operator", "brief"
    """
    with Session(engine) as session:
        statement = select(CEOPreferences).where(CEOPreferences.ceo_id == ceo_id)
        preferences = session.exec(statement).first()
        if not preferences:
            preferences = CEOPreferences(ceo_id=ceo_id)

        learned = dict(preferences.learned_defaults or {})
        bucket = dict(learned.get(signal_type, {}))
        bucket[value] = bucket.get(value, 0) + 1
        learned[signal_type] = bucket
        preferences.learned_defaults = learned
        session.add(preferences)
        session.commit()


def get_learned_preference(ceo_id: str, signal_type: str) -> str | None:
    """
    Return the dominant learned preference for a signal_type if we're confident,
    otherwise None (meaning we should still ask).
    Confident = top value has ≥ PREFERENCE_CONFIDENCE_THRESHOLD selections AND
                represents > 60% of all selections for that type.
    """
    prefs = get_ceo_preferences(ceo_id)
    if not prefs:
        return None
    learned = prefs.learned_defaults or {}
    bucket: dict = learned.get(signal_type, {})
    if not bucket:
        return None
    total = sum(bucket.values())
    top_value, top_count = max(bucket.items(), key=lambda kv: kv[1])
    if top_count >= _PREFERENCE_CONFIDENCE_THRESHOLD and top_count / total > 0.6:
        return top_value
    return None


def record_clarification_answer(
    ceo_id: str,
    conversation_id: str,
    signal_type: str,
    signal_value: str,
) -> None:
    """
    Persist a CEO's clarification answer at two scopes:
      1. Conversation-scoped: ConversationLiveContext.resolved_clarifications
         Suppresses re-asking for the rest of this conversation immediately.
      2. Long-term: CEOPreferences.learned_defaults via record_preference_signal()
         Contributes toward the confidence threshold that suppresses across conversations.
    """
    with Session(engine) as db:
        stmt = (
            select(ConversationLiveContext)
            .where(ConversationLiveContext.ceo_id == ceo_id)
            .where(ConversationLiveContext.conversation_id == conversation_id)
        )
        ctx = db.exec(stmt).first()
        if not ctx:
            ctx = ConversationLiveContext(conversation_id=conversation_id, ceo_id=ceo_id)
        current = dict(ctx.resolved_clarifications or {})
        current[signal_type] = signal_value
        ctx.resolved_clarifications = current
        db.add(ctx)
        db.commit()
    record_preference_signal(ceo_id, signal_type=signal_type, value=signal_value)


def get_company_state(company_name: str) -> Optional[CompanyState]:
    with Session(engine) as session:
        statement = select(CompanyState).where(CompanyState.company_name == company_name)
        return session.exec(statement).first()


def get_company_identity_profile(company_name: str) -> Optional[CompanyIdentityProfile]:
    with Session(engine) as session:
        statement = select(CompanyIdentityProfile).where(CompanyIdentityProfile.company_name == company_name)
        return session.exec(statement).first()


def save_company_identity_profile(profile: CompanyIdentityProfile) -> CompanyIdentityProfile:
    with Session(engine) as session:
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile


def get_company_profile_record(ceo_id: str) -> Optional[CompanyProfileRecord]:
    with Session(engine) as session:
        statement = select(CompanyProfileRecord).where(CompanyProfileRecord.ceo_id == ceo_id)
        return session.exec(statement).first()


def save_company_profile_record(profile: CompanyProfileRecord) -> CompanyProfileRecord:
    with Session(engine) as session:
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile


def get_or_create_live_context(ceo_id: str, conversation_id: str) -> ConversationLiveContext:
    with Session(engine) as session:
        statement = (
            select(ConversationLiveContext)
            .where(ConversationLiveContext.ceo_id == ceo_id)
            .where(ConversationLiveContext.conversation_id == conversation_id)
        )
        ctx = session.exec(statement).first()
        if not ctx:
            ctx = ConversationLiveContext(conversation_id=conversation_id, ceo_id=ceo_id)
            session.add(ctx)
            session.commit()
            session.refresh(ctx)
        return ctx


def append_thread_entry(entry: ConversationThreadEntry) -> ConversationThreadEntry:
    with Session(engine) as session:
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry


def update_live_context(
    conversation_id: str,
    *,
    ceo_id: Optional[str] = None,
    current_schedule: Optional[dict[str, Any]] = None,
    open_decisions: Optional[list[str]] = None,
    open_commitments: Optional[list[str]] = None,
    pending_actions: Optional[list[dict[str, Any]]] = None,
    resolved_decisions: Optional[list[str]] = None,
    resolved_commitments: Optional[list[str]] = None,
    entities_update: Optional[dict[str, Any]] = None,
    resolved_entities: Optional[list[str]] = None,
    new_contribution: Optional[dict[str, Any]] = None,
    intent_state: Optional[dict[str, Any]] = None,
    unified_memory: Optional[dict[str, Any]] = None,
) -> Optional[ConversationLiveContext]:
    with Session(engine) as session:
        statement = select(ConversationLiveContext).where(ConversationLiveContext.conversation_id == conversation_id)
        ctx = session.exec(statement).first()
        if not ctx:
            if not ceo_id:
                return None
            ctx = ConversationLiveContext(conversation_id=conversation_id, ceo_id=ceo_id)

        if current_schedule is not None:
            ctx.current_schedule = current_schedule
        if open_decisions:
            merged_decisions = list(ctx.open_decisions or [])
            for item in open_decisions:
                if item and item not in merged_decisions:
                    merged_decisions.append(item)
            ctx.open_decisions = merged_decisions
        if open_commitments:
            merged_commitments = list(ctx.open_commitments or [])
            for item in open_commitments:
                if item and item not in merged_commitments:
                    merged_commitments.append(item)
            ctx.open_commitments = merged_commitments
        if pending_actions is not None:
            ctx.pending_actions = pending_actions
        if resolved_decisions:
            normalized = {str(item).strip().lower() for item in resolved_decisions if str(item).strip()}
            ctx.open_decisions = [
                item for item in (ctx.open_decisions or [])
                if str(item).strip().lower() not in normalized
            ]
        if resolved_commitments:
            normalized = {str(item).strip().lower() for item in resolved_commitments if str(item).strip()}
            ctx.open_commitments = [
                item for item in (ctx.open_commitments or [])
                if str(item).strip().lower() not in normalized
            ]
        if entities_update:
            merged_entities = dict(ctx.entities_in_play or {})
            merged_entities.update(entities_update)
            ctx.entities_in_play = merged_entities
        if resolved_entities:
            merged_entities = dict(ctx.entities_in_play or {})
            for entity in resolved_entities:
                if entity in merged_entities:
                    merged_entities[entity] = f"Resolved {datetime.now().isoformat()[:10]}"
            ctx.entities_in_play = merged_entities
        if new_contribution:
            contribs = list(ctx.last_agent_contributions or [])
            contribs.append(new_contribution)
            ctx.last_agent_contributions = contribs[-5:]
        if intent_state is not None:
            ctx.intent_state = intent_state
        if unified_memory is not None:
            ctx.unified_memory = unified_memory

        ctx.turn_count = int(ctx.turn_count or 0) + 1
        ctx.updated_at = datetime.now().isoformat()
        session.add(ctx)
        session.commit()
        session.refresh(ctx)
        return ctx


def get_latest_intent_state(ceo_id: str, conversation_id: str) -> dict[str, Any] | None:
    with Session(engine) as session:
        statement = (
            select(ConversationLiveContext)
            .where(ConversationLiveContext.ceo_id == ceo_id)
            .where(ConversationLiveContext.conversation_id == conversation_id)
        )
        ctx = session.exec(statement).first()
        return dict(ctx.intent_state or {}) if ctx else None


def persist_latest_intent_state(
    *,
    ceo_id: str,
    conversation_id: str,
    intent_state: dict[str, Any],
) -> ConversationLiveContext:
    with Session(engine) as session:
        statement = (
            select(ConversationLiveContext)
            .where(ConversationLiveContext.ceo_id == ceo_id)
            .where(ConversationLiveContext.conversation_id == conversation_id)
        )
        ctx = session.exec(statement).first()
        if not ctx:
            ctx = ConversationLiveContext(conversation_id=conversation_id, ceo_id=ceo_id)
        ctx.intent_state = intent_state
        ctx.updated_at = datetime.now().isoformat()
        session.add(ctx)
        session.commit()
        session.refresh(ctx)
        return ctx


def get_latest_unified_memory(ceo_id: str, conversation_id: str) -> dict[str, Any] | None:
    with Session(engine) as session:
        statement = (
            select(ConversationLiveContext)
            .where(ConversationLiveContext.ceo_id == ceo_id)
            .where(ConversationLiveContext.conversation_id == conversation_id)
        )
        ctx = session.exec(statement).first()
        return dict(ctx.unified_memory or {}) if ctx else None


def persist_latest_unified_memory(
    *,
    ceo_id: str,
    conversation_id: str,
    unified_memory: dict[str, Any],
) -> ConversationLiveContext:
    with Session(engine) as session:
        statement = (
            select(ConversationLiveContext)
            .where(ConversationLiveContext.ceo_id == ceo_id)
            .where(ConversationLiveContext.conversation_id == conversation_id)
        )
        ctx = session.exec(statement).first()
        if not ctx:
            ctx = ConversationLiveContext(conversation_id=conversation_id, ceo_id=ceo_id)
        ctx.unified_memory = unified_memory
        ctx.updated_at = datetime.now().isoformat()
        session.add(ctx)
        session.commit()
        session.refresh(ctx)
        return ctx


def get_thread_entries(
    conversation_id: str,
    *,
    limit: int = 20,
    entry_types: Optional[list[str]] = None,
) -> list[ConversationThreadEntry]:
    with Session(engine) as session:
        statement = (
            select(ConversationThreadEntry)
            .where(ConversationThreadEntry.conversation_id == conversation_id)
            .order_by(ConversationThreadEntry.turn.asc(), ConversationThreadEntry.id.asc())
        )
        if entry_types:
            statement = statement.where(ConversationThreadEntry.entry_type.in_(entry_types))
        rows = session.exec(statement).all()
        return rows[-limit:]


def resolve_thread_entries(
    conversation_id: str,
    *,
    ceo_id: Optional[str] = None,
    entry_type: Optional[str] = None,
    match_text: Optional[str] = None,
    entities: Optional[list[str]] = None,
    resolution_note: Optional[str] = None,
) -> list[ConversationThreadEntry]:
    normalized_match = str(match_text or "").strip().lower()
    normalized_entities = [str(item).strip().lower() for item in (entities or []) if str(item).strip()]
    with Session(engine) as session:
        statement = select(ConversationThreadEntry).where(
            ConversationThreadEntry.conversation_id == conversation_id
        )
        if ceo_id:
            statement = statement.where(ConversationThreadEntry.ceo_id == ceo_id)
        if entry_type:
            statement = statement.where(ConversationThreadEntry.entry_type == entry_type)
        rows = session.exec(statement).all()
        resolved: list[ConversationThreadEntry] = []
        for row in rows:
            if row.status == "resolved":
                continue
            row_text = " ".join(
                [
                    str(row.content or ""),
                    json.dumps(row.structured_payload or {}),
                    " ".join(row.entities or []),
                ]
            ).lower()
            entity_match = (
                not normalized_entities
                or any(entity in [str(item).strip().lower() for item in (row.entities or [])] for entity in normalized_entities)
            )
            text_match = not normalized_match or normalized_match in row_text
            if not (entity_match and text_match):
                continue
            row.status = "resolved"
            row.resolved_at = datetime.now().isoformat()
            if resolution_note:
                row.resolution_note = resolution_note
            session.add(row)
            resolved.append(row)
        session.commit()
        for row in resolved:
            session.refresh(row)
        return resolved


def get_or_create_situational_profile(ceo_id: str) -> CEOSituationalProfile:
    with Session(engine) as session:
        statement = select(CEOSituationalProfile).where(CEOSituationalProfile.ceo_id == ceo_id)
        profile = session.exec(statement).first()
        if not profile:
            profile = CEOSituationalProfile(ceo_id=ceo_id)
            session.add(profile)
            session.commit()
            session.refresh(profile)
        return profile


def update_situational_profile(
    ceo_id: str,
    *,
    operating_mode: Optional[str] = None,
    add_pressure: Optional[str] = None,
    remove_pressure: Optional[str] = None,
    topic_mention: Optional[str] = None,
    resolve_topic: Optional[str] = None,
    add_obligation: Optional[str] = None,
    updated_by: str = "system",
) -> CEOSituationalProfile:
    with Session(engine) as session:
        statement = select(CEOSituationalProfile).where(CEOSituationalProfile.ceo_id == ceo_id)
        profile = session.exec(statement).first()
        if not profile:
            profile = CEOSituationalProfile(ceo_id=ceo_id)

        if operating_mode:
            profile.operating_mode = operating_mode

        if add_pressure:
            pressures = list(profile.active_pressures or [])
            if add_pressure not in pressures:
                pressures.append(add_pressure)
            profile.active_pressures = pressures[-10:]

        if remove_pressure:
            profile.active_pressures = [p for p in (profile.active_pressures or []) if p != remove_pressure]

        if topic_mention:
            topics = list(profile.recurring_topics or [])
            existing = next((topic for topic in topics if topic.get("topic") == topic_mention), None)
            if existing:
                existing["mention_count"] = int(existing.get("mention_count", 1)) + 1
                existing["last_seen"] = datetime.now().isoformat()[:10]
            else:
                topics.append(
                    {
                        "topic": topic_mention,
                        "mention_count": 1,
                        "last_seen": datetime.now().isoformat()[:10],
                        "resolved": False,
                    }
                )
            profile.recurring_topics = topics[-20:]

        if resolve_topic:
            topics = list(profile.recurring_topics or [])
            for topic in topics:
                if isinstance(topic, dict) and topic.get("topic") == resolve_topic:
                    topic["resolved"] = True
                    topic["resolved_at"] = datetime.now().isoformat()[:10]
                    break
            profile.recurring_topics = topics

        if add_obligation:
            obligations = list(profile.relationship_obligations or [])
            if add_obligation not in obligations:
                obligations.append(add_obligation)
            profile.relationship_obligations = obligations[-10:]

        profile.updated_at = datetime.now().isoformat()
        profile.updated_by = updated_by
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile


def create_assistant_conversation(ceo_id: str, title: str = "New conversation") -> AssistantConversation:
    conversation = AssistantConversation(
        conversation_id=f"conv:{ceo_id}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        ceo_id=ceo_id,
        title=title,
    )
    with Session(engine) as session:
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        return conversation


def get_assistant_conversation(ceo_id: str, conversation_id: str) -> Optional[AssistantConversation]:
    with Session(engine) as session:
        statement = (
            select(AssistantConversation)
            .where(AssistantConversation.ceo_id == ceo_id)
            .where(AssistantConversation.conversation_id == conversation_id)
        )
        return session.exec(statement).first()


def list_assistant_conversations(ceo_id: str) -> list[AssistantConversation]:
    with Session(engine) as session:
        statement = (
            select(AssistantConversation)
            .where(AssistantConversation.ceo_id == ceo_id)
            .where(AssistantConversation.archived == False)  # noqa: E712
            .order_by(AssistantConversation.pinned.desc(), AssistantConversation.updated_at.desc())
        )
        return session.exec(statement).all()


def append_interaction_to_conversation(
    ceo_id: str,
    conversation_id: str,
    interaction_id: int,
    query: Optional[str] = None,
) -> Optional[AssistantConversation]:
    with Session(engine) as session:
        statement = (
            select(AssistantConversation)
            .where(AssistantConversation.ceo_id == ceo_id)
            .where(AssistantConversation.conversation_id == conversation_id)
        )
        conversation = session.exec(statement).first()
        if not conversation:
            return None

        interaction_ids = list(conversation.interaction_ids or [])
        if interaction_id not in interaction_ids:
            interaction_ids.append(interaction_id)
        conversation.interaction_ids = interaction_ids
        conversation.updated_at = datetime.now().isoformat()
        if query and conversation.title == "New conversation":
            conversation.title = query.strip()[:72] or conversation.title
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        return conversation


def get_interactions_for_conversation(ceo_id: str, interaction_ids: list[int]) -> list[SessionInteraction]:
    if not interaction_ids:
        return []
    with Session(engine) as session:
        statement = (
            select(SessionInteraction)
            .where(SessionInteraction.ceo_id == ceo_id)
            .where(SessionInteraction.id.in_(interaction_ids))
        )
        interaction_map = {interaction.id: interaction for interaction in session.exec(statement).all()}
    return [interaction_map[interaction_id] for interaction_id in interaction_ids if interaction_id in interaction_map]


def get_unassigned_session_history(ceo_id: str) -> list[SessionInteraction]:
    with Session(engine) as session:
        conversation_statement = select(AssistantConversation).where(AssistantConversation.ceo_id == ceo_id)
        conversations = session.exec(conversation_statement).all()
        assigned_ids = {
            interaction_id
            for conversation in conversations
            for interaction_id in (conversation.interaction_ids or [])
        }
        statement = select(SessionInteraction).where(SessionInteraction.ceo_id == ceo_id).order_by(SessionInteraction.id.asc())
        interactions = session.exec(statement).all()
    return [interaction for interaction in interactions if interaction.id not in assigned_ids]


def create_assistant_project(ceo_id: str, name: str, description: Optional[str] = None) -> AssistantProject:
    project = AssistantProject(
        project_id=f"proj:{ceo_id}:{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        ceo_id=ceo_id,
        name=name,
        description=description,
    )
    with Session(engine) as session:
        session.add(project)
        session.commit()
        session.refresh(project)
        return project


def list_assistant_projects(ceo_id: str) -> list[AssistantProject]:
    with Session(engine) as session:
        statement = (
            select(AssistantProject)
            .where(AssistantProject.ceo_id == ceo_id)
            .order_by(AssistantProject.updated_at.desc())
        )
        return session.exec(statement).all()


def get_assistant_project(ceo_id: str, project_id: str) -> Optional[AssistantProject]:
    with Session(engine) as session:
        statement = (
            select(AssistantProject)
            .where(AssistantProject.ceo_id == ceo_id)
            .where(AssistantProject.project_id == project_id)
        )
        return session.exec(statement).first()


def update_assistant_project(
    ceo_id: str,
    project_id: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    document_ids: Optional[list[str]] = None,
    conversation_ids: Optional[list[str]] = None,
) -> Optional[AssistantProject]:
    with Session(engine) as session:
        statement = (
            select(AssistantProject)
            .where(AssistantProject.ceo_id == ceo_id)
            .where(AssistantProject.project_id == project_id)
        )
        project = session.exec(statement).first()
        if not project:
            return None

        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        if document_ids is not None:
            project.document_ids = document_ids
        if conversation_ids is not None:
            project.conversation_ids = conversation_ids
        project.updated_at = datetime.now().isoformat()
        session.add(project)
        session.commit()
        session.refresh(project)
        return project


def delete_assistant_project(ceo_id: str, project_id: str) -> bool:
    with Session(engine) as session:
        statement = (
            select(AssistantProject)
            .where(AssistantProject.ceo_id == ceo_id)
            .where(AssistantProject.project_id == project_id)
        )
        project = session.exec(statement).first()
        if not project:
            return False
        session.delete(project)
        session.commit()
        return True


def delete_assistant_conversation(ceo_id: str, conversation_id: str) -> bool:
    with Session(engine) as session:
        statement = (
            select(AssistantConversation)
            .where(AssistantConversation.ceo_id == ceo_id)
            .where(AssistantConversation.conversation_id == conversation_id)
        )
        conversation = session.exec(statement).first()
        if not conversation:
            return False

        projects_statement = select(AssistantProject).where(AssistantProject.ceo_id == ceo_id)
        projects = session.exec(projects_statement).all()
        for project in projects:
            conversation_ids = [item for item in (project.conversation_ids or []) if item != conversation_id]
            if conversation_ids != (project.conversation_ids or []):
                project.conversation_ids = conversation_ids
                project.updated_at = datetime.now().isoformat()
                session.add(project)

        session.delete(conversation)
        session.commit()
        return True


def update_assistant_conversation(
    ceo_id: str,
    conversation_id: str,
    *,
    title: Optional[str] = None,
    pinned: Optional[bool] = None,
    archived: Optional[bool] = None,
) -> Optional[AssistantConversation]:
    with Session(engine) as session:
        statement = (
            select(AssistantConversation)
            .where(AssistantConversation.ceo_id == ceo_id)
            .where(AssistantConversation.conversation_id == conversation_id)
        )
        conversation = session.exec(statement).first()
        if not conversation:
            return None

        if title is not None:
            conversation.title = title
        if pinned is not None:
            conversation.pinned = pinned
        if archived is not None:
            conversation.archived = archived
        conversation.updated_at = datetime.now().isoformat()
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        return conversation


def get_project_context(ceo_id: str, project_id: str) -> dict[str, Any]:
    with Session(engine) as session:
        statement = (
            select(AssistantProject)
            .where(AssistantProject.ceo_id == ceo_id)
            .where(AssistantProject.project_id == project_id)
        )
        project = session.exec(statement).first()
        if not project:
            return {}

        company_name = get_user_by_ceo_id(ceo_id, session=session).company_name if get_user_by_ceo_id(ceo_id, session=session) else None
        state = None
        if company_name:
            state_statement = select(CompanyState).where(CompanyState.company_name == company_name)
            state = session.exec(state_statement).first()

        indexed_documents = state.knowledge_base if state else []
        project_documents = [
            {
                "document_id": document.get("document_id"),
                "title": document.get("title", "Untitled document"),
                "content": document.get("content", ""),
                "summary": document.get("summary"),
                "domains": document.get("domains", []) or [],
            }
            for document in indexed_documents
            if document.get("document_id") in (project.document_ids or [])
        ]

        interaction_ids: list[int] = []
        for conversation_id in project.conversation_ids or []:
            conv_statement = (
                select(AssistantConversation)
                .where(AssistantConversation.ceo_id == ceo_id)
                .where(AssistantConversation.conversation_id == conversation_id)
            )
            conversation = session.exec(conv_statement).first()
            if conversation:
                interaction_ids.extend(conversation.interaction_ids or [])

        history_items: list[dict[str, Any]] = []
        if interaction_ids:
            interaction_statement = (
                select(SessionInteraction)
                .where(SessionInteraction.ceo_id == ceo_id)
                .where(SessionInteraction.id.in_(interaction_ids))
            )
            interaction_map = {item.id: item for item in session.exec(interaction_statement).all()}
            for interaction_id in interaction_ids:
                interaction = interaction_map.get(interaction_id)
                if not interaction:
                    continue
                history_items.append(
                    {
                        "interaction_id": interaction.id,
                        "query": interaction.query,
                        "response": interaction.response,
                        "timestamp": interaction.timestamp,
                        "status": interaction.status,
                    }
                )

        return {
            "project_id": project.project_id,
            "name": project.name,
            "description": project.description,
            "document_ids": project.document_ids or [],
            "conversation_ids": project.conversation_ids or [],
            "documents": project_documents,
            "history": history_items[-8:],
        }


def get_user_by_ceo_id(ceo_id: str, *, session: Optional[Session] = None) -> Optional[User]:
    owns_session = session is None
    active_session = session or Session(engine)
    try:
        statement = select(User).where(User.ceo_id == ceo_id)
        return active_session.exec(statement).first()
    finally:
        if owns_session:
            active_session.close()

def get_user(username: str) -> Optional[User]:
    with Session(engine) as session:
        statement = select(User).where(User.username == username)
        return session.exec(statement).first()


def get_user_by_openclaw_token(token: str) -> Optional[User]:
    """Look up a user by their opaque OpenClaw webhook token."""
    with Session(engine) as session:
        statement = select(User).where(User.openclaw_webhook_token == token)
        return session.exec(statement).first()


def rotate_openclaw_token(ceo_id: str) -> str:
    """Generate (or replace) the OpenClaw webhook token for this CEO.

    Returns the new token.  The old token is immediately invalidated — any
    OpenClaw skill configs pointing at the old URL must be updated.
    """
    new_token = secrets.token_hex(32)   # 64 hex chars, 256 bits of entropy
    with Session(engine) as session:
        user = session.exec(select(User).where(User.ceo_id == ceo_id)).first()
        if not user:
            raise ValueError(f"No user found for ceo_id '{ceo_id}'")
        user.openclaw_webhook_token = new_token
        session.add(user)
        session.commit()
    return new_token

def update_company_state(company_name: str, updates: dict[str, Any]) -> Optional[CompanyState]:
    """
    Partial update for CompanyState primitives (STORY-006).
    """
    with Session(engine) as session:
        statement = select(CompanyState).where(CompanyState.company_name == company_name)
        state = session.exec(statement).first()
        if not state:
            return None
        
        for key, value in updates.items():
            if hasattr(state, key):
                setattr(state, key, value)
        
        from datetime import datetime
        state.last_updated = datetime.now().isoformat()
        
        session.add(state)
        session.commit()
        session.refresh(state)
        return state

def get_session_history(ceo_id: str, limit: int = 10) -> list[SessionInteraction]:
    with Session(engine) as session:
        statement = select(SessionInteraction).where(SessionInteraction.ceo_id == ceo_id).order_by(SessionInteraction.id.desc()).limit(limit)
        return session.exec(statement).all()

def get_previous_conversation_interaction(
    ceo_id: str, conversation_id: str, current_interaction_id: int
) -> Optional[SessionInteraction]:
    """Return the interaction immediately preceding current_interaction_id in this conversation."""
    conversation = get_assistant_conversation(ceo_id, conversation_id)
    if not conversation or not conversation.interaction_ids:
        return None
    ids = list(conversation.interaction_ids)
    try:
        idx = ids.index(current_interaction_id)
    except ValueError:
        return None
    if idx == 0:
        return None
    prev_id = ids[idx - 1]
    with Session(engine) as session:
        return session.exec(select(SessionInteraction).where(SessionInteraction.id == prev_id)).first()


def get_recent_conversation_interactions(
    ceo_id: str,
    conversation_id: str,
    current_interaction_id: int,
    limit: int = 3,
) -> list[SessionInteraction]:
    """Return the last `limit` interactions in a conversation before the current one, oldest-first."""
    conversation = get_assistant_conversation(ceo_id, conversation_id)
    if not conversation or not conversation.interaction_ids:
        return []
    ids = [iid for iid in list(conversation.interaction_ids) if iid != current_interaction_id]
    recent_ids = ids[-limit:] if len(ids) > limit else ids
    if not recent_ids:
        return []
    with Session(engine) as session:
        rows = session.exec(
            select(SessionInteraction)
            .where(SessionInteraction.ceo_id == ceo_id)
            .where(SessionInteraction.id.in_(recent_ids))
        ).all()
    id_map = {r.id: r for r in rows}
    return [id_map[iid] for iid in recent_ids if iid in id_map]


def get_approved_decisions(ceo_id: str) -> list[ApprovedDecision]:
    """STORY-018: Retrieve active strategic decisions."""
    with Session(engine) as session:
        statement = select(ApprovedDecision).where(ApprovedDecision.ceo_id == ceo_id).where(ApprovedDecision.status == "ACTIVE")
        return session.exec(statement).all()

def get_decision(decision_id: int) -> Optional[ApprovedDecision]:
    """STORY-068: Retrieve a specific decision."""
    with Session(engine) as session:
        return session.get(ApprovedDecision, decision_id)

def get_unread_signals(ceo_id: str) -> list[IncomingSignal]:
    """STORY-021: Retrieve unread communication signals."""
    with Session(engine) as session:
        statement = select(IncomingSignal).where(IncomingSignal.ceo_id == ceo_id).where(IncomingSignal.status == "UNREAD")
        return session.exec(statement).all()

def get_recent_signals(ceo_id: str, limit: int = 10) -> list[IncomingSignal]:
    with Session(engine) as session:
        statement = (
            select(IncomingSignal)
            .where(IncomingSignal.ceo_id == ceo_id)
            .order_by(IncomingSignal.id.desc())
            .limit(limit)
        )
        return session.exec(statement).all()

def get_signal(signal_id: int) -> Optional[IncomingSignal]:
    """STORY-067: Retrieve a specific signal."""
    with Session(engine) as session:
        return session.get(IncomingSignal, signal_id)

def clear_demo_signals(ceo_id: str) -> None:
    with Session(engine) as session:
        statement = (
            select(IncomingSignal)
            .where(IncomingSignal.ceo_id == ceo_id)
            .where(IncomingSignal.source.startswith("Demo"))
        )
        for signal in session.exec(statement).all():
            session.delete(signal)
        session.commit()

def save_object(obj: SQLModel):
    with Session(engine) as session:
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj


# ---------------------------------------------------------------------------
# CEO Memory
# ---------------------------------------------------------------------------

def save_ceo_memory(memory: CEOMemory) -> CEOMemory:
    with Session(engine) as session:
        session.add(memory)
        session.commit()
        session.refresh(memory)
        return memory


def get_ceo_memories(
    ceo_id: str,
    *,
    memory_type: Optional[str] = None,
    limit: int = 50,
) -> list[CEOMemory]:
    with Session(engine) as session:
        statement = select(CEOMemory).where(CEOMemory.ceo_id == ceo_id)
        if memory_type:
            statement = statement.where(CEOMemory.memory_type == memory_type)
        statement = statement.order_by(CEOMemory.created_at.desc()).limit(limit)
        return session.exec(statement).all()


def search_ceo_memories(ceo_id: str, query: str, *, limit: int = 10) -> list[CEOMemory]:
    """Case-insensitive substring search across title and content."""
    q = query.lower()
    with Session(engine) as session:
        all_memories = session.exec(
            select(CEOMemory).where(CEOMemory.ceo_id == ceo_id)
        ).all()
        matches = [
            m for m in all_memories
            if q in m.title.lower() or q in m.content.lower()
            or any(q in tag.lower() for tag in (m.tags or []))
        ]
        return matches[:limit]


def delete_ceo_memory(memory_id: str) -> bool:
    with Session(engine) as session:
        memory = session.exec(
            select(CEOMemory).where(CEOMemory.memory_id == memory_id)
        ).first()
        if not memory:
            return False
        session.delete(memory)
        session.commit()
        return True


def get_connected_accounts(ceo_id: str) -> list[ConnectedAccount]:
    with Session(engine) as session:
        statement = select(ConnectedAccount).where(ConnectedAccount.ceo_id == ceo_id)
        return session.exec(statement).all()


def get_connected_account(ceo_id: str, provider: str, service: str) -> Optional[ConnectedAccount]:
    with Session(engine) as session:
        statement = (
            select(ConnectedAccount)
            .where(ConnectedAccount.ceo_id == ceo_id)
            .where(ConnectedAccount.provider == provider)
            .where(ConnectedAccount.service == service)
        )
        return session.exec(statement).first()


def upsert_connected_account(
    ceo_id: str,
    provider: str,
    service: str,
    *,
    access_token: str,
    refresh_token: Optional[str] = None,
    token_type: Optional[str] = None,
    expires_at: Optional[str] = None,
    account_email: Optional[str] = None,
    scopes: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> ConnectedAccount:
    from datetime import datetime

    with Session(engine) as session:
        statement = (
            select(ConnectedAccount)
            .where(ConnectedAccount.ceo_id == ceo_id)
            .where(ConnectedAccount.provider == provider)
            .where(ConnectedAccount.service == service)
        )
        account = session.exec(statement).first()
        if not account:
            account = ConnectedAccount(
                ceo_id=ceo_id,
                provider=provider,
                service=service,
                access_token=access_token,
            )
        account.access_token = access_token
        if refresh_token:
            account.refresh_token = refresh_token
        account.token_type = token_type
        account.expires_at = expires_at
        account.account_email = account_email
        account.scopes = scopes or []
        account.provider_metadata = metadata or {}
        account.updated_at = datetime.now().isoformat()
        session.add(account)
        session.commit()
        session.refresh(account)
        return account

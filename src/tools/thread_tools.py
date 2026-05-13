from __future__ import annotations

from typing import Any

from src.core.database import (
    append_thread_entry,
    get_or_create_live_context,
    get_thread_entries,
    resolve_thread_entries,
    update_live_context,
)
from src.core.models import ConversationThreadEntry

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


class WriteThreadEntryTool(BaseTool):
    metadata = ToolMetadata(
        name="write_thread_entry",
        description=(
            "Write a typed entry to the conversation thread so later turns can reuse the "
            "current schedule, surfaced decisions, commitments, and notable entities."
        ),
        read_only=False,
        side_effects=True,
        tags=["thread", "context", "write"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        conversation_id = kwargs.get("conversation_id") or context.conversation_id
        if not conversation_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="conversation_id required")
        if not context.ceo_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="ceo_id required")

        entry_type = str(kwargs.get("entry_type") or "contribution")
        content = str(kwargs.get("content") or "").strip()
        structured_payload = kwargs.get("structured_payload")
        entities = [str(item).strip() for item in (kwargs.get("entities") or []) if str(item).strip()]
        turn = int(kwargs.get("turn") or 0)

        entry = ConversationThreadEntry(
            conversation_id=conversation_id,
            ceo_id=context.ceo_id,
            turn=turn,
            actor=str(kwargs.get("actor") or "system"),
            entry_type=entry_type,
            content=content,
            structured_payload=structured_payload,
            entities=entities,
            workflow_type=kwargs.get("workflow_type"),
            interaction_id=context.interaction_id,
        )
        saved = append_thread_entry(entry)

        # Index entity links for cross-source retrieval
        try:
            from src.core.entity_extraction import extract_entities_from_text
            from src.core.knowledge import index_entity_link
            auto_entities = extract_entities_from_text(content)
            all_entities = list(set(auto_entities + entities))
            ts = saved.timestamp or ""
            for entity in all_entities[:10]:
                index_entity_link(
                    entity=entity,
                    ceo_id=context.ceo_id,
                    source_type="thread_entry",
                    source_id=str(saved.id),
                    content_snippet=content[:200],
                    timestamp=ts,
                )
        except Exception:
            pass  # never block thread writes due to indexing failure

        if entry_type == "schedule" and isinstance(structured_payload, dict):
            update_live_context(
                conversation_id,
                ceo_id=context.ceo_id,
                current_schedule={"turn": turn, **structured_payload},
            )
        elif entry_type == "decision":
            decision_text = structured_payload.get("decision") if isinstance(structured_payload, dict) else content
            if decision_text:
                update_live_context(conversation_id, ceo_id=context.ceo_id, open_decisions=[str(decision_text)])
        elif entry_type == "commitment":
            commitment_text = structured_payload.get("commitment") if isinstance(structured_payload, dict) else content
            if commitment_text:
                update_live_context(conversation_id, ceo_id=context.ceo_id, open_commitments=[str(commitment_text)])

        if entities:
            entity_map = {entity: f"Turn {turn}: mentioned in {entry_type}" for entity in entities}
            update_live_context(conversation_id, ceo_id=context.ceo_id, entities_update=entity_map)

        update_live_context(
            conversation_id,
            ceo_id=context.ceo_id,
            new_contribution={
                "actor": entry.actor,
                "entry_type": entry_type,
                "content_summary": content[:200],
                "turn": turn,
            },
        )

        from src.workflows.world_simulation import record_world_event

        record_world_event(
            context.ceo_id,
            domain="memory",
            event_type="thread_entry_written",
            description=f"Thread entry written for {entry_type}.",
            source_ids=[str(saved.id)] if saved.id is not None else [],
            payload={
                "conversation_id": conversation_id,
                "entry_id": saved.id,
                "entry_type": entry_type,
                "content_summary": content[:220],
                "entities": entities,
                "workflow_type": kwargs.get("workflow_type"),
                "interaction_id": context.interaction_id,
            },
        )

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"entry_id": saved.id, "entry_type": entry_type},
        )


class GetLiveContextTool(BaseTool):
    metadata = ToolMetadata(
        name="get_live_context",
        description=(
            "Get the live conversation context: current schedule, open decisions, open commitments, "
            "entities in play, and recent agent contributions."
        ),
        read_only=True,
        side_effects=False,
        tags=["thread", "context", "read"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        conversation_id = kwargs.get("conversation_id") or context.conversation_id
        if not conversation_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="conversation_id required")
        if not context.ceo_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="ceo_id required")
        live_context = get_or_create_live_context(context.ceo_id, conversation_id)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"live_context": live_context.model_dump()},
        )


class GetThreadEntriesTool(BaseTool):
    metadata = ToolMetadata(
        name="get_thread_entries",
        description="Read recent typed conversation thread entries for the active conversation.",
        read_only=True,
        side_effects=False,
        tags=["thread", "context", "read"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        conversation_id = kwargs.get("conversation_id") or context.conversation_id
        if not conversation_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="conversation_id required")
        limit = min(int(kwargs.get("limit", 20)), 100)
        entry_types = kwargs.get("entry_types")
        entries = get_thread_entries(conversation_id, limit=limit, entry_types=entry_types)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"entries": [entry.model_dump() for entry in entries]},
        )


class ResolveThreadEntryTool(BaseTool):
    metadata = ToolMetadata(
        name="resolve_thread_entry",
        description=(
            "Mark matching conversation thread entries as resolved and remove resolved decisions or "
            "commitments from the live context."
        ),
        read_only=False,
        side_effects=True,
        tags=["thread", "context", "write", "resolution"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        conversation_id = kwargs.get("conversation_id") or context.conversation_id
        if not conversation_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="conversation_id required")
        if not context.ceo_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="ceo_id required")

        entry_type = kwargs.get("entry_type")
        match_text = kwargs.get("match_text")
        entities = [str(item).strip() for item in (kwargs.get("entities") or []) if str(item).strip()]
        resolution_note = kwargs.get("resolution_note")
        resolved = resolve_thread_entries(
            conversation_id,
            ceo_id=context.ceo_id,
            entry_type=entry_type,
            match_text=match_text,
            entities=entities,
            resolution_note=resolution_note,
        )
        if resolved:
            if entry_type == "decision":
                update_live_context(
                    conversation_id,
                    ceo_id=context.ceo_id,
                    resolved_decisions=[entry.content for entry in resolved],
                    resolved_entities=entities,
                )
            elif entry_type == "commitment":
                update_live_context(
                    conversation_id,
                    ceo_id=context.ceo_id,
                    resolved_commitments=[entry.content for entry in resolved],
                    resolved_entities=entities,
                )
            elif entities:
                update_live_context(
                    conversation_id,
                    ceo_id=context.ceo_id,
                    resolved_entities=entities,
                )
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"resolved_count": len(resolved), "resolved_entry_ids": [entry.id for entry in resolved]},
        )

"""Memory management tool — memory-management skill pattern.

Provides durable, per-CEO long-term memory across sessions. Supports saving
decisions, commitments, preferences, facts, and milestones that should persist
beyond the current conversation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import re
from typing import Any
from uuid import uuid4

from src.core.database import (
    delete_ceo_memory,
    get_ceo_memories,
    save_ceo_memory,
    search_ceo_memories,
)
from src.core.models import CEOMemory

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult

VALID_TYPES = {"decision", "commitment", "preference", "fact", "milestone"}
_AUTO_SAVE_TYPES = {"decision", "commitment", "milestone"}
_SHORT_LIVED_COMMITMENT_MARKERS = (
    "today",
    "tomorrow",
    "this week",
    "next week",
    "within",
    "by ",
    "before ",
    "daily",
    "meeting",
    "review",
    "follow-up",
    "follow up",
)


class MemoryManagementTool(BaseTool):
    metadata = ToolMetadata(
        name="memory_management",
        description=(
            "Save, retrieve, search, and delete long-term memories for the CEO. "
            "Memories persist across sessions. "
            "Actions: save | list | search | delete. "
            "Memory types: decision, commitment, preference, fact, milestone."
        ),
        read_only=False,
        side_effects=True,
        tags=["memory", "persistence", "ceo"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "").strip()
        ceo_id = context.ceo_id or kwargs.get("ceo_id") or ""

        if not ceo_id:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="ceo_id is required (set via ToolContext or kwarg).",
            )

        if action == "save":
            return self._save(ceo_id, kwargs, context)
        elif action == "list":
            return self._list(ceo_id, kwargs)
        elif action == "search":
            return self._search(ceo_id, kwargs)
        elif action == "delete":
            return self._delete(kwargs)
        else:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error=(
                    f"Unknown action: {action!r}. "
                    "Valid actions: save, list, search, delete."
                ),
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _save(self, ceo_id: str, kwargs: dict, context: ToolContext) -> ToolResult:
        title = str(kwargs.get("title") or "").strip()
        content = str(kwargs.get("content") or "").strip()
        memory_type = str(kwargs.get("memory_type") or "fact").strip().lower()
        tags = kwargs.get("tags") or []
        expires_at = kwargs.get("expires_at")
        auto_save = bool(kwargs.get("auto_save")) or "auto" in [str(tag).strip().lower() for tag in tags]
        confidence = str(kwargs.get("confidence") or "").strip().lower()
        confidence_score = kwargs.get("confidence_score")
        evidence_state = str(kwargs.get("evidence_state") or "").strip().lower()
        dedupe_query = str(kwargs.get("dedupe_query") or "").strip()

        if not title or not content:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="save requires 'title' and 'content'.",
            )

        if memory_type not in VALID_TYPES:
            memory_type = "fact"

        normalized_tags = [str(t).strip().lower() for t in tags] if isinstance(tags, list) else []
        if auto_save:
            gate_error = self._auto_save_gate_error(
                title=title,
                content=content,
                memory_type=memory_type,
                confidence=confidence,
                confidence_score=confidence_score,
                evidence_state=evidence_state,
            )
            if gate_error:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    data={"skipped": True, "reason": gate_error},
                    error=gate_error,
                )

            duplicate = self._find_duplicate_memory(
                ceo_id=ceo_id,
                query=dedupe_query or title,
                title=title,
                content=content,
                memory_type=memory_type,
            )
            if duplicate:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=True,
                    data={
                        "memory_id": duplicate.memory_id,
                        "memory_type": duplicate.memory_type,
                        "title": duplicate.title,
                        "created_at": duplicate.created_at,
                        "deduped": True,
                    },
                )

        resolved_expires_at = expires_at or self._default_expiration(
            memory_type=memory_type,
            content=content,
            tags=normalized_tags,
        )

        memory = CEOMemory(
            memory_id=f"mem_{uuid4().hex[:12]}",
            ceo_id=ceo_id,
            memory_type=memory_type,
            title=title,
            content=content,
            tags=normalized_tags,
            source_interaction_id=context.interaction_id,
            expires_at=str(resolved_expires_at) if resolved_expires_at else None,
        )
        saved = save_ceo_memory(memory)

        # Index to Chroma for semantic search
        try:
            from src.core.entity_extraction import extract_entities_from_text
            from src.core.knowledge import index_entity_link, index_memory
            auto_entities = extract_entities_from_text(f"{title} {content}")
            provided_entities = kwargs.get("entities") or []
            all_entities = list(set(auto_entities + [str(e) for e in provided_entities]))
            index_memory(
                memory_id=str(saved.id or saved.memory_id),
                ceo_id=ceo_id,
                title=title,
                content=content,
                memory_type=memory_type,
                entities=all_entities,
                tags=normalized_tags,
            )
            ts = saved.created_at or datetime.now(timezone.utc).isoformat()
            for entity in all_entities[:10]:
                index_entity_link(
                    entity=entity,
                    ceo_id=ceo_id,
                    source_type="memory",
                    source_id=str(saved.id or saved.memory_id),
                    content_snippet=f"{title}: {content[:200]}",
                    timestamp=ts,
                )
        except Exception:
            pass  # never block memory save due to indexing failure

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "memory_id": saved.memory_id,
                "memory_type": saved.memory_type,
                "title": saved.title,
                "created_at": saved.created_at,
                "expires_at": saved.expires_at,
                "deduped": False,
            },
        )

    def _list(self, ceo_id: str, kwargs: dict) -> ToolResult:
        memory_type = kwargs.get("memory_type")
        limit = min(int(kwargs.get("limit") or 20), 100)
        memories = get_ceo_memories(ceo_id, memory_type=memory_type, limit=limit)
        now = datetime.now(timezone.utc).isoformat()
        active = [m for m in memories if not m.expires_at or m.expires_at > now]
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "memories": [_serialize(m) for m in active],
                "total": len(active),
                "filter_type": memory_type,
            },
        )

    def _search(self, ceo_id: str, kwargs: dict) -> ToolResult:
        query = str(kwargs.get("query") or kwargs.get("dedupe_query") or "").strip()
        if not query:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="search requires 'query'.",
            )
        limit = min(int(kwargs.get("limit") or 10), 50)
        memory_types = kwargs.get("memory_types")

        # Try semantic search via Chroma first; fall back to SQLite text search
        try:
            from src.core.knowledge import search_memories
            semantic_results = search_memories(ceo_id, query, limit=limit, memory_types=memory_types)
            if semantic_results:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=True,
                    data={
                        "query": query,
                        "memories": semantic_results,
                        "matches": semantic_results,  # backward compat alias
                        "count": len(semantic_results),
                        "search_mode": "semantic",
                    },
                )
        except Exception:
            pass

        # SQLite text search fallback
        matches = search_ceo_memories(ceo_id, query, limit=limit)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "query": query,
                "memories": [_serialize(m) for m in matches],
                "matches": [_serialize(m) for m in matches],
                "count": len(matches),
                "search_mode": "text",
            },
        )

    def _delete(self, kwargs: dict) -> ToolResult:
        memory_id = str(kwargs.get("memory_id") or "").strip()
        if not memory_id:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="delete requires 'memory_id'.",
            )
        deleted = delete_ceo_memory(memory_id)
        return ToolResult(
            tool_name=self.metadata.name,
            success=deleted,
            data={"memory_id": memory_id, "deleted": deleted},
            error=None if deleted else f"Memory not found: {memory_id}",
        )

    def _auto_save_gate_error(
        self,
        *,
        title: str,
        content: str,
        memory_type: str,
        confidence: str,
        confidence_score: Any,
        evidence_state: str,
    ) -> str | None:
        if memory_type not in _AUTO_SAVE_TYPES:
            return f"Auto-save only allowed for {sorted(_AUTO_SAVE_TYPES)}."
        if confidence not in {"medium", "high"}:
            return "Auto-save requires medium/high confidence."
        try:
            numeric_score = float(confidence_score) if confidence_score is not None else None
        except (TypeError, ValueError):
            numeric_score = None
        if numeric_score is not None and numeric_score < 0.5:
            return "Auto-save requires confidence_score >= 0.5."
        if evidence_state == "sparse":
            return "Auto-save disabled when evidence is sparse."
        if not self._is_specific_candidate(title=title, content=content, memory_type=memory_type):
            return "Auto-save candidate is too vague."
        return None

    def _is_specific_candidate(self, *, title: str, content: str, memory_type: str) -> bool:
        normalized = " ".join(f"{title} {content}".split()).lower()
        if len(normalized) < 32:
            return False
        if memory_type == "decision":
            return any(marker in normalized for marker in ("decide", "approved", "commit", "choose", "will "))
        if memory_type == "milestone":
            return any(marker in normalized for marker in ("by ", "before ", "deadline", "milestone", "target"))
        return any(
            marker in normalized
            for marker in ("owner", "today", "tomorrow", "this week", "next week", "within", "by ", "before ", ":")
        ) or bool(re.search(r"\b(finance|ceo|cfo|engineering|product|operations|sales)\b", normalized))

    def _find_duplicate_memory(
        self,
        *,
        ceo_id: str,
        query: str,
        title: str,
        content: str,
        memory_type: str,
    ) -> CEOMemory | None:
        candidates = search_ceo_memories(ceo_id, query, limit=10)
        normalized_title = title.lower()
        normalized_content = content.lower()
        for candidate in candidates:
            if candidate.memory_type != memory_type:
                continue
            title_similarity = SequenceMatcher(None, normalized_title, candidate.title.lower()).ratio()
            content_similarity = SequenceMatcher(None, normalized_content, candidate.content.lower()).ratio()
            if title_similarity >= 0.88 or content_similarity >= 0.86:
                return candidate
        return None

    def _default_expiration(self, *, memory_type: str, content: str, tags: list[str]) -> str | None:
        now = datetime.now(timezone.utc)
        normalized = content.lower()
        if memory_type == "commitment":
            days = 30 if any(marker in normalized for marker in _SHORT_LIVED_COMMITMENT_MARKERS) else 90
            return (now + timedelta(days=days)).isoformat()
        if memory_type == "milestone":
            return (now + timedelta(days=180)).isoformat()
        if memory_type in {"decision", "preference"}:
            return None
        if "auto" in tags:
            return (now + timedelta(days=120)).isoformat()
        return None


def _serialize(m: CEOMemory) -> dict:
    return {
        "memory_id": m.memory_id,
        "memory_type": m.memory_type,
        "title": m.title,
        "content": m.content,
        "tags": m.tags or [],
        "created_at": m.created_at,
        "expires_at": m.expires_at,
        "source_interaction_id": m.source_interaction_id,
    }

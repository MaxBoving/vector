from __future__ import annotations

from typing import Any

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


class GetEntityContextTool(BaseTool):
    metadata = ToolMetadata(
        name="get_entity_context",
        description=(
            "Retrieve everything known about specific named entities (deals, people, projects) "
            "across all conversations and memories. Use when the CEO mentions a specific name "
            "or entity and you need historical context about it."
        ),
        read_only=True,
        side_effects=False,
        tags=["entity", "memory", "context"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        from src.core.knowledge import search_entity_context

        entities = kwargs.get("entities") or []
        if isinstance(entities, str):
            entities = [e.strip() for e in entities.split(",") if e.strip()]
        limit = int(kwargs.get("limit", 10))

        if not entities:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="entities required (list of strings or comma-separated string)",
            )

        results = search_entity_context(context.ceo_id, entities, limit=limit)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"entity_context": results, "entities_queried": entities},
        )

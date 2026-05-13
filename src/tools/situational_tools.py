from __future__ import annotations

from typing import Any

from src.core.database import get_or_create_situational_profile, update_situational_profile

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


class GetSituationalProfileTool(BaseTool):
    metadata = ToolMetadata(
        name="get_situational_profile",
        description=(
            "Get the CEO's current situational profile: operating mode, active pressures, "
            "recurring topics, open threads, and relationship obligations."
        ),
        read_only=True,
        side_effects=False,
        tags=["situational", "ceo", "context"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        if not ceo_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="ceo_id required")
        profile = get_or_create_situational_profile(ceo_id)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"situational_profile": profile.model_dump()},
        )


class UpdateSituationalProfileTool(BaseTool):
    metadata = ToolMetadata(
        name="update_situational_profile",
        description=(
            "Update the CEO's situational profile based on what was observed in this interaction. "
            "Use it to record active pressures, recurring topics, obligations, and operating mode. "
            "Pass resolve_topic=<topic_name> to mark a recurring topic as resolved."
        ),
        read_only=False,
        side_effects=True,
        tags=["situational", "ceo", "write"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        if not ceo_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="ceo_id required")
        profile = update_situational_profile(
            ceo_id,
            operating_mode=kwargs.get("operating_mode"),
            add_pressure=kwargs.get("add_pressure"),
            remove_pressure=kwargs.get("remove_pressure"),
            topic_mention=kwargs.get("topic_mention"),
            resolve_topic=kwargs.get("resolve_topic"),
            add_obligation=kwargs.get("add_obligation"),
            updated_by=str(kwargs.get("updated_by") or "system"),
        )
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"situational_profile": profile.model_dump()},
        )

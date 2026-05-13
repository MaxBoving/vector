from typing import Any

from src.core.database import (
    get_ceo_preferences,
    get_company_identity_profile,
    get_project_context,
    get_company_state,
    get_recent_conversation_interactions,
    get_recent_signals,
    get_session_history,
    get_unread_signals,
    save_object,
)
from src.core.models import IncomingSignal, normalize_company_state_payload, normalize_preferences_payload

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


class GetCompanyStateTool(BaseTool):
    metadata = ToolMetadata(
        name="get_company_state",
        description="Fetch the persisted CompanyState for a company.",
        read_only=True,
        side_effects=False,
        tags=["state", "database"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        company_name = kwargs.get("company_name") or context.company_name
        if not company_name:
            return ToolResult(tool_name=self.metadata.name, success=False, error="company_name is required")
        state = get_company_state(company_name)
        return ToolResult(
            tool_name=self.metadata.name,
            success=state is not None,
            data={"state": normalize_company_state_payload(state) if state else None},
        )


class GetPreferencesTool(BaseTool):
    metadata = ToolMetadata(
        name="get_preferences",
        description="Fetch CEO preference state for the active executive.",
        read_only=True,
        side_effects=False,
        tags=["preferences", "database"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        if not ceo_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="ceo_id is required")
        prefs = get_ceo_preferences(ceo_id)
        return ToolResult(
            tool_name=self.metadata.name,
            success=prefs is not None,
            data={"preferences": normalize_preferences_payload(prefs) if prefs else None},
        )


class GetCompanyIdentityProfileTool(BaseTool):
    metadata = ToolMetadata(
        name="get_company_identity_profile",
        description="Fetch the persisted company identity profile derived from exemplar materials.",
        read_only=True,
        side_effects=False,
        tags=["identity", "database"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        company_name = kwargs.get("company_name") or context.company_name
        if not company_name:
            return ToolResult(tool_name=self.metadata.name, success=False, error="company_name is required")
        profile = get_company_identity_profile(company_name)
        payload = (
            profile.profile_data
            if profile
            else {
                "has_examples": False,
                "tone": None,
                "preferred_formats": [],
                "section_patterns": [],
                "reference_titles": [],
            }
        )
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"company_identity": payload},
        )


class GetSessionHistoryTool(BaseTool):
    metadata = ToolMetadata(
        name="get_session_history",
        description="Fetch recent CEO interaction history.",
        read_only=True,
        side_effects=False,
        tags=["history", "database"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        limit = int(kwargs.get("limit", 5))
        if not ceo_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="ceo_id is required")

        # Prefer conversation-scoped history so agents see the current thread,
        # not a cross-conversation mix of unrelated prior interactions.
        conversation_id = kwargs.get("conversation_id") or context.metadata.get("conversation_id")
        interaction_id = kwargs.get("interaction_id") or context.interaction_id
        if conversation_id and interaction_id:
            history = get_recent_conversation_interactions(
                ceo_id, conversation_id, interaction_id, limit=limit
            )
        else:
            history = get_session_history(ceo_id, limit=limit)

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"history": [item.model_dump() for item in history]},
        )


class GetProjectContextTool(BaseTool):
    metadata = ToolMetadata(
        name="get_project_context",
        description="Fetch the active project context, including project-scoped documents and conversation history.",
        read_only=True,
        side_effects=False,
        tags=["project", "database", "context"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        project_id = kwargs.get("project_id") or context.metadata.get("project_id")
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        if not ceo_id or not project_id:
            return ToolResult(tool_name=self.metadata.name, success=True, data={"project_context": None})
        project_context = get_project_context(ceo_id, project_id)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"project_context": project_context or None},
        )


class GetUnreadSignalsTool(BaseTool):
    metadata = ToolMetadata(
        name="get_unread_signals",
        description="Fetch unread inbound executive signals.",
        read_only=True,
        side_effects=False,
        tags=["signals", "database"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        if not ceo_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="ceo_id is required")
        signals = get_unread_signals(ceo_id)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"signals": [signal.model_dump() for signal in signals]},
        )


class GetRecentSignalsTool(BaseTool):
    metadata = ToolMetadata(
        name="get_recent_signals",
        description="Fetch recent inbound executive signals regardless of read status.",
        read_only=True,
        side_effects=False,
        tags=["signals", "database"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        limit = int(kwargs.get("limit", 10))
        if not ceo_id:
            return ToolResult(tool_name=self.metadata.name, success=False, error="ceo_id is required")
        signals = get_recent_signals(ceo_id, limit=limit)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"signals": [signal.model_dump() for signal in signals]},
        )


class SaveIncomingSignalTool(BaseTool):
    metadata = ToolMetadata(
        name="save_incoming_signal",
        description="Persist an inbound email-style executive signal.",
        read_only=False,
        side_effects=True,
        tags=["signals", "database", "write"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        sender = kwargs.get("sender")
        subject = kwargs.get("subject")
        content = kwargs.get("content")
        source = kwargs.get("source", "Email")
        if not ceo_id or not sender or not subject or not content:
            return ToolResult(tool_name=self.metadata.name, success=False, error="ceo_id, sender, subject, and content are required")
        signal = IncomingSignal(
            ceo_id=ceo_id,
            source=source,
            sender=sender,
            subject=subject,
            content=content,
        )
        saved = save_object(signal)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"signal": saved.model_dump()},
        )

"""CoauthoringTool — Create, retrieve, edit, and export draft versions.

Wraps the coauthoring workflow for iterative CEO-agent co-authoring of memos,
decks, and canvases via the standard BaseTool interface.
"""

from typing import Any

from src.workflows.coauthoring import (
    apply_edit,
    create_draft_session,
    export_session_summary,
    get_diff,
    get_draft_session,
    list_sessions,
)

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


class CoauthoringTool(BaseTool):
    metadata = ToolMetadata(
        name="coauthor_draft",
        description=(
            "Create, retrieve, edit, and export draft versions for iterative "
            "CEO-agent co-authoring."
        ),
        read_only=False,
        side_effects=True,
        tags=["coauthoring", "draft", "documents"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action")

        if action == "create":
            artifact_id = kwargs.get("artifact_id")
            draft_type = kwargs.get("draft_type")
            title = kwargs.get("title")
            initial_content = kwargs.get("initial_content")
            author = kwargs.get("author", "system")

            if not all([artifact_id, draft_type, title, initial_content is not None]):
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error="create requires: artifact_id, draft_type, title, initial_content",
                )

            session = create_draft_session(
                artifact_id=artifact_id,
                draft_type=draft_type,
                title=title,
                initial_content=initial_content,
                author=author,
            )
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={
                    "session_id": session.session_id,
                    "artifact_id": session.artifact_id,
                    "draft_type": session.draft_type,
                    "title": session.title,
                    "version": session.current_version.version,
                    "author": session.current_version.author,
                    "timestamp": session.created_at,
                },
            )

        elif action == "edit":
            session_id = kwargs.get("session_id")
            new_content = kwargs.get("new_content")
            author = kwargs.get("author", "agent")
            edit_note = kwargs.get("edit_note", "")

            if not session_id or new_content is None:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error="edit requires: session_id, new_content",
                )

            new_ver = apply_edit(
                session_id=session_id,
                new_content=new_content,
                author=author,
                edit_note=edit_note,
            )
            if new_ver is None:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error=f"Session not found: {session_id}",
                )
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={
                    "session_id": session_id,
                    "version": new_ver.version,
                    "author": new_ver.author,
                    "edit_note": new_ver.edit_note,
                    "char_delta": new_ver.char_delta,
                    "timestamp": new_ver.timestamp,
                    "line_diff": new_ver.line_diff,
                },
            )

        elif action == "get":
            session_id = kwargs.get("session_id")
            if not session_id:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error="get requires: session_id",
                )
            session = get_draft_session(session_id)
            if session is None:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error=f"Session not found: {session_id}",
                )
            summary = export_session_summary(session_id)
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={
                    "session_id": session.session_id,
                    "title": session.title,
                    "draft_type": session.draft_type,
                    "version_count": session.version_count,
                    "current_content": session.current_content,
                    "updated_at": session.updated_at,
                    "edit_history": summary.get("edit_history", []),
                },
            )

        elif action == "diff":
            session_id = kwargs.get("session_id")
            from_version = kwargs.get("from_version")
            to_version = kwargs.get("to_version")

            if not session_id or from_version is None or to_version is None:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error="diff requires: session_id, from_version, to_version",
                )

            diff_lines = get_diff(
                session_id=session_id,
                from_version=int(from_version),
                to_version=int(to_version),
            )
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={
                    "session_id": session_id,
                    "from_version": from_version,
                    "to_version": to_version,
                    "diff": diff_lines,
                    "line_count": len(diff_lines),
                },
            )

        elif action == "summary":
            session_id = kwargs.get("session_id")
            if not session_id:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error="summary requires: session_id",
                )
            summary = export_session_summary(session_id)
            if not summary:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error=f"Session not found: {session_id}",
                )
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data=summary,
            )

        elif action == "list":
            sessions = list_sessions()
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={
                    "sessions": [
                        {
                            "session_id": s.session_id,
                            "artifact_id": s.artifact_id,
                            "draft_type": s.draft_type,
                            "title": s.title,
                            "version_count": s.version_count,
                            "updated_at": s.updated_at,
                        }
                        for s in sessions
                    ],
                    "total": len(sessions),
                },
            )

        else:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error=(
                    f"Unknown action: {action!r}. "
                    "Valid actions: create, edit, get, diff, summary, list"
                ),
            )

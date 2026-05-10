"""Google Drive tools — drive-search skill pattern.

GoogleDriveSearchTool searches the CEO's connected Google Drive by query string
and returns file metadata. GoogleDriveReadTool fetches the text content of a
specific Drive file (Google Docs, Sheets, Slides exported as plain text).
"""
from __future__ import annotations

import os
from typing import Any

from src.integrations.providers import (
    ProviderIntegrationError,
    _export_google_drive_doc,
    _get_valid_account,
    _search_google_drive,
)

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult

from src.tools.demo_config import DEV_DEMO_MODE, demo_lookup_id, load_fixture


def _get_demo_documents(ceo_id: str) -> list[dict[str, Any]] | None:
    if not DEV_DEMO_MODE:
        return None
    data = load_fixture("drive_files")
    files = data.get("files")
    return files if files is not None else None

# MIME types that Google Drive can export as plain text
_EXPORTABLE_MIME_TYPES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
}

_MIME_LABELS = {
    "application/vnd.google-apps.document": "Google Doc",
    "application/vnd.google-apps.spreadsheet": "Google Sheet",
    "application/vnd.google-apps.presentation": "Google Slides",
    "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
    "text/plain": "TXT",
    "application/vnd.google-apps.folder": "Folder",
}


def _label_mime(mime: str) -> str:
    return _MIME_LABELS.get(mime, mime.split("/")[-1].upper())


class GoogleDriveSearchTool(BaseTool):
    metadata = ToolMetadata(
        name="google_drive_search",
        description=(
            "Search the CEO's Google Drive for files by name or content query. "
            "Returns file metadata including id, name, type, modified date, and link. "
            "Use google_drive_read to fetch the content of a specific file."
        ),
        read_only=True,
        side_effects=False,
        tags=["connector", "drive", "google", "search"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        query: str = str(kwargs.get("query") or "").strip()
        max_results: int = min(int(kwargs.get("max_results") or 20), 50)
        include_folders: bool = bool(kwargs.get("include_folders", False))
        read_contents_limit: int = max(0, min(int(kwargs.get("read_contents_limit") or 0), 5))
        ceo_id = context.ceo_id or ""

        if not query:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="'query' is required. Use Drive query syntax, e.g. \"name contains 'Q2 board'\".",
            )

        # Demo fallback
        demo_docs = _get_demo_documents(ceo_id)
        if demo_docs is not None:
            q_lower = query.lower()
            matched = [
                d for d in demo_docs
                if q_lower in (d.get("title") or "").lower()
                or q_lower in (d.get("content") or "").lower()
            ][:max_results]
            enriched = [
                {
                    "file_id": d["id"],
                    "name": d["title"],
                    "type": d.get("type", "Google Doc"),
                    "mime_type": "application/vnd.google-apps.document",
                    "modified_at": d.get("modified_at"),
                    "exportable": True,
                    "content_excerpt": (d.get("content") or "")[:1200],
                }
                for d in matched
            ]
            return ToolResult(
                tool_name=self.metadata.name,
                success=True,
                data={"query": query, "files": enriched, "count": len(enriched), "hydrated_count": len(enriched)},
            )

        try:
            account = _get_valid_account(ceo_id, "google", "google_drive")
            if not account:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error=(
                        "No Google Drive account connected. "
                        "Use /connect with service=google_drive to link Drive access."
                    ),
                )
            files = _search_google_drive(
                account,
                query=query,
                max_results=max_results,
                include_folders=include_folders,
            )
        except ProviderIntegrationError as exc:
            return ToolResult(tool_name=self.metadata.name, success=False, error=str(exc))

        enriched = []
        hydrated_count = 0
        for f in files:
            mime_type = f.get("mimeType")
            exportable = mime_type in _EXPORTABLE_MIME_TYPES
            item = {
                "file_id": f.get("id"),
                "name": f.get("name"),
                "type": _label_mime(mime_type or ""),
                "mime_type": mime_type,
                "modified_at": f.get("modifiedTime"),
                "size_bytes": f.get("size"),
                "web_link": f.get("webViewLink"),
                "exportable": exportable,
            }
            if exportable and hydrated_count < read_contents_limit:
                try:
                    content = _export_google_drive_doc(account, f.get("id"))
                except ProviderIntegrationError:
                    content = ""
                if content:
                    item["content"] = content[:50_000]
                    item["content_excerpt"] = content[:1200]
                    item["char_count"] = len(content)
                    hydrated_count += 1
            enriched.append(item)

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "query": query,
                "files": enriched,
                "count": len(enriched),
                "hydrated_count": hydrated_count,
            },
        )


class GoogleDriveReadTool(BaseTool):
    metadata = ToolMetadata(
        name="google_drive_read",
        description=(
            "Fetch the text content of a Google Drive file (Google Docs, Sheets, or Slides). "
            "Requires file_id from google_drive_search. "
            "Returns up to 50 KB of plain text content."
        ),
        read_only=True,
        side_effects=False,
        tags=["connector", "drive", "google", "read"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        file_id: str = str(kwargs.get("file_id") or "").strip()
        mime_type: str = str(kwargs.get("mime_type") or "application/vnd.google-apps.document")
        ceo_id = context.ceo_id or ""

        if not file_id:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="'file_id' is required. Get it from google_drive_search.",
            )

        # Demo fallback
        demo_docs = _get_demo_documents(ceo_id)
        if demo_docs is not None:
            doc = next((d for d in demo_docs if d["id"] == file_id), None)
            if doc:
                content = doc.get("content") or ""
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=True,
                    data={"file_id": file_id, "content": content, "char_count": len(content), "truncated": False},
                )
            return ToolResult(tool_name=self.metadata.name, success=False, error=f"File '{file_id}' not found in demo Drive.")

        if mime_type not in _EXPORTABLE_MIME_TYPES:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error=(
                    f"Cannot export MIME type '{mime_type}' as text. "
                    f"Exportable types: {sorted(_EXPORTABLE_MIME_TYPES)}."
                ),
            )

        try:
            account = _get_valid_account(ceo_id, "google", "google_drive")
            if not account:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error="No Google Drive account connected.",
                )
            content = _export_google_drive_doc(account, file_id)
        except ProviderIntegrationError as exc:
            return ToolResult(tool_name=self.metadata.name, success=False, error=str(exc))

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "file_id": file_id,
                "content": content,
                "char_count": len(content),
                "truncated": len(content) >= 50_000,
            },
        )

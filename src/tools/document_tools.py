import json
from typing import Any

from src.core.database import get_company_state
from .base import BaseTool, ToolContext, ToolMetadata, ToolResult
from src.presentation import DeckSpec, MemoSpec
from src.presentation.render_canvas import CanvasHeroMetric, CanvasSectionSpec, CanvasSpec, render_canvas_file
from src.presentation.render_docx import render_docx_memo
from src.presentation.render_pptx import render_pptx_deck
from src.presentation.render_xlsx import render_xlsx_workbook
from src.workflows.workbook_models import WorkbookSpec
from .artifact_requests import (
    ArtifactRequestConfig,
    build_deck_payload,
    build_memo_payload,
    build_workbook_payload,
    ensure_output_path,
    parse_artifact_request,
)


def _coerce_json_payload(value: Any) -> dict | None:
    """Accept a dict or a JSON string; return a dict or None."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


class ExtractPdfTool(BaseTool):
    metadata = ToolMetadata(
        name="extract_pdf",
        description="Extract text and metadata from a PDF attachment.",
        read_only=True,
        side_effects=False,
        tags=["documents", "pdf", "extraction"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        filename = kwargs.get("filename")
        document_id = kwargs.get("document_id")
        company_name = kwargs.get("company_name") or context.company_name

        # Look up already-ingested document from the knowledge base
        if document_id and company_name:
            state = get_company_state(company_name)
            if state:
                for doc in state.knowledge_base or []:
                    if doc.get("document_id") == document_id:
                        content = doc.get("content", "")
                        word_count = len(content.split())
                        return ToolResult(
                            tool_name=self.metadata.name,
                            success=True,
                            data={
                                "filename": filename or doc.get("title", ""),
                                "document_id": document_id,
                                "title": doc.get("title", ""),
                                "text": content,
                                "word_count": word_count,
                                "domains": doc.get("domains", []),
                                "summary": doc.get("summary", ""),
                                "purpose": doc.get("purpose", "reference"),
                            },
                            metadata={"source": "knowledge_base"},
                        )

        return ToolResult(
            tool_name=self.metadata.name,
            success=False,
            data={"filename": filename, "document_id": document_id, "text": ""},
            error="Document not found. Upload the PDF first via the document upload endpoint.",
        )


class CreateWorkbookTool(BaseTool):
    metadata = ToolMetadata(
        name="create_workbook",
        description="Create an XLSX workbook artifact from structured executive data.",
        read_only=False,
        side_effects=True,
        tags=["documents", "xlsx", "export"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        parsed = parse_artifact_request(
            context=context,
            kwargs=kwargs,
            config=ArtifactRequestConfig(
                tool_name=self.metadata.name,
                payload_kind="workbook",
                model_cls=WorkbookSpec,
                default_filename="executive-report.xlsx",
                payload_builder=build_workbook_payload,
            ),
        )
        if isinstance(parsed, ToolResult):
            return parsed
        path, spec = parsed
        render_result = render_xlsx_workbook(
            path=path,
            spec=spec,
            artifact_id=f"interaction:{context.interaction_id}:analysis_xlsx",
        )
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"path": str(path)},
            metadata=render_result,
        )


class UpdateWorkbookTool(BaseTool):
    metadata = ToolMetadata(
        name="update_workbook",
        description="Update an existing XLSX workbook artifact.",
        read_only=False,
        side_effects=True,
        tags=["documents", "xlsx", "mutation"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        workbook_path = kwargs.get("workbook_path")
        return ToolResult(
            tool_name=self.metadata.name,
            success=False,
            data={"path": workbook_path},
            error="Workbook updating is not implemented yet.",
        )


class CreateDocxMemoTool(BaseTool):
    metadata = ToolMetadata(
        name="create_docx_memo",
        description="Create a DOCX memo artifact from structured content.",
        read_only=False,
        side_effects=True,
        tags=["documents", "docx", "export"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        filename = kwargs.get("filename", "executive-memo.docx")
        path = ensure_output_path(
            output_path=kwargs.get("output_path"),
            output_dir=kwargs.get("output_dir"),
            filename=filename,
        )
        parsed = parse_artifact_request(
            context=context,
            kwargs=kwargs,
            config=ArtifactRequestConfig(
                tool_name=self.metadata.name,
                payload_kind="memo",
                model_cls=MemoSpec,
                default_filename=filename,
                payload_builder=build_memo_payload,
            ),
        )
        if isinstance(parsed, ToolResult):
            return parsed
        _, memo_spec = parsed
        render_metadata = render_docx_memo(path=path, memo_spec=memo_spec)

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"path": str(path)},
            metadata=render_metadata,
        )


class CreatePptxDeckTool(BaseTool):
    metadata = ToolMetadata(
        name="create_pptx_deck",
        description="Create a PPTX deck artifact from structured content.",
        read_only=False,
        side_effects=True,
        tags=["documents", "pptx", "export"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        parsed = parse_artifact_request(
            context=context,
            kwargs=kwargs,
            config=ArtifactRequestConfig(
                tool_name=self.metadata.name,
                payload_kind="deck",
                model_cls=DeckSpec,
                default_filename="executive-deck.pptx",
                payload_builder=build_deck_payload,
            ),
        )
        if isinstance(parsed, ToolResult):
            return parsed
        path, deck_spec = parsed
        render_metadata = render_pptx_deck(path=path, deck_spec=deck_spec)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"path": str(path)},
            metadata=render_metadata,
        )


class CreateCanvasTool(BaseTool):
    metadata = ToolMetadata(
        name="create_canvas",
        description="Create a self-contained HTML executive one-pager from structured content.",
        read_only=False,
        side_effects=True,
        tags=["documents", "canvas", "html", "export"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        filename = kwargs.get("filename", "executive-canvas.html")
        path = ensure_output_path(
            output_path=kwargs.get("output_path"),
            output_dir=kwargs.get("output_dir"),
            filename=filename,
        )

        canvas_payload = _coerce_json_payload(kwargs.get("canvas_spec"))
        if canvas_payload:
            try:
                canvas_spec = CanvasSpec(**canvas_payload)
            except Exception as exc:
                return ToolResult(
                    tool_name=self.metadata.name,
                    success=False,
                    error=f"Invalid canvas_spec: {exc}",
                )
        else:
            title = str(kwargs.get("title") or "Executive Summary")
            summary = str(kwargs.get("summary") or "")
            sections_raw = kwargs.get("sections") or []
            hero_raw = kwargs.get("hero_metric")
            hero = CanvasHeroMetric(**hero_raw) if isinstance(hero_raw, dict) else None
            sections = [
                CanvasSectionSpec(
                    label=str(s.get("label") or "Section"),
                    bullets=[str(b) for b in (s.get("items") or s.get("bullets") or [])],
                    content=s.get("content"),
                    highlight=bool(s.get("highlight", False)),
                )
                for s in sections_raw
            ]
            canvas_spec = CanvasSpec(
                title=title,
                summary=summary,
                hero_metric=hero,
                sections=sections,
                theme_id=kwargs.get("theme_id"),
                source_credit=kwargs.get("source_credit"),
            )

        try:
            render_metadata = render_canvas_file(path=path, spec=canvas_spec)
        except Exception as exc:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error=f"Canvas render failed: {exc}",
            )

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"path": str(path)},
            metadata=render_metadata,
        )

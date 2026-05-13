import logging
from pathlib import Path
from zipfile import ZipFile

from src.presentation import MemoSectionSpec, MemoSpec
from src.presentation.render_docx import render_docx_memo
from src.tools.base import ToolContext
from src.tools.document_tools import CreateDocxMemoTool


def test_render_docx_memo_writes_preview_and_document(tmp_path: Path) -> None:
    path = tmp_path / "memo.docx"
    spec = MemoSpec(
        title="Board Memo",
        summary="Revenue quality improved this quarter.",
        section_order=["Executive Summary", "Recommended Actions"],
        sections=[
            MemoSectionSpec(label="Recommended Actions", items=["Preserve hiring guardrails."]),
        ],
        assumptions=["Board pack reflects the latest close."],
        open_questions=["Need final legal sign-off."],
        metadata={"template_id": "board_memo_v1", "theme_id": "board_formal"},
    )

    result = render_docx_memo(path=path, memo_spec=spec)

    assert path.exists()
    assert result["preview_format"] == "md"
    assert result["preview_metadata"]["template_id"] == "board_memo_v1"
    with ZipFile(path) as archive:
        names = archive.namelist()
        document_xml = archive.read("word/document.xml").decode("utf-8")
        styles_xml = archive.read("word/styles.xml").decode("utf-8")
        rels_xml = archive.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "Board Memo" in document_xml
    assert "Preserve hiring guardrails." in document_xml
    assert "Need final legal sign-off." in document_xml
    assert "1E3A5F" in document_xml
    assert "Cambria" in styles_xml
    assert "6B7280" in styles_xml
    assert "<w:shd" in document_xml
    assert "<w:pBdr>" in document_xml
    assert "word/styles.xml" in names
    assert "styles.xml" in rels_xml
    assert "theme/theme1.xml" in rels_xml


def test_render_docx_memo_uses_template_section_order_when_spec_order_missing(tmp_path: Path) -> None:
    path = tmp_path / "memo-template-order.docx"
    spec = MemoSpec(
        title="Board Memo",
        summary="Revenue quality improved this quarter.",
        sections=[
            MemoSectionSpec(label="Recommended Actions", items=["Preserve hiring guardrails."]),
            MemoSectionSpec(label="Key Findings", items=["Gross margin improved."]),
        ],
        metadata={"template_id": "board_memo_v1", "theme_id": "board_formal"},
    )

    render_docx_memo(path=path, memo_spec=spec)

    with ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert document_xml.index("Key Findings") < document_xml.index("Recommended Actions")


def test_render_docx_cover_style_changes_surface_treatment(tmp_path: Path) -> None:
    board_path = tmp_path / "board-memo.docx"
    operator_path = tmp_path / "operator-memo.docx"
    spec = MemoSpec(
        title="Executive Memo",
        summary="Revenue quality improved this quarter.",
        sections=[MemoSectionSpec(label="Recommended Actions", items=["Preserve hiring guardrails."])],
        assumptions=["Board pack reflects the latest close."],
        open_questions=["Need final legal sign-off."],
        metadata={"template_id": "board_memo_v1"},
    )

    render_docx_memo(path=board_path, memo_spec=spec.model_copy(update={"metadata": {"template_id": "board_memo_v1", "theme_id": "board_formal"}}))
    render_docx_memo(path=operator_path, memo_spec=spec.model_copy(update={"metadata": {"template_id": "board_memo_v1", "theme_id": "operator_modern"}}))

    with ZipFile(board_path) as archive:
        board_xml = archive.read("word/document.xml").decode("utf-8")
        board_styles = archive.read("word/styles.xml").decode("utf-8")
    with ZipFile(operator_path) as archive:
        operator_xml = archive.read("word/document.xml").decode("utf-8")
        operator_styles = archive.read("word/styles.xml").decode("utf-8")

    assert "8B5E34" in board_xml
    assert "F8FAFC" in operator_xml
    assert "D1D5DB" in board_xml
    assert "14B8A6" in operator_xml
    assert "Cambria" in board_styles
    assert "Aptos" in operator_styles


def test_create_docx_memo_tool_accepts_top_level_fields(tmp_path: Path) -> None:
    tool = CreateDocxMemoTool()
    context = ToolContext(interaction_id=77, ceo_id="ceo_test")

    result = tool.invoke(
        context,
        output_path=str(tmp_path / "memo-tool.docx"),
        title="Board Memo",
        summary="Revenue quality improved this quarter.",
        sections=[{"label": "Recommended Actions", "items": ["Preserve hiring guardrails."]}],
        assumptions=["Board pack reflects the latest close."],
        open_questions=["Need final legal sign-off."],
        template_id="board_memo_v1",
        theme_id="board_formal",
    )

    assert result.success is True
    assert Path(result.data["path"]).exists()
    assert result.metadata["preview_metadata"]["template_id"] == "board_memo_v1"


def test_create_docx_memo_logs_invalid_payload_keys(caplog, tmp_path: Path) -> None:
    tool = CreateDocxMemoTool()
    context = ToolContext(interaction_id=78, ceo_id="ceo_test", stage="synthesizer")

    with caplog.at_level(logging.WARNING):
        result = tool.invoke(
            context,
            output_path=str(tmp_path / "invalid-memo.docx"),
            memo_spec={"summary": "Missing title and sections"},
        )

    assert result.success is False
    assert "Invalid memo payload" in (result.error or "")
    assert "Invalid memo payload for create_docx_memo" in caplog.text
    assert "payload_keys=['summary']" in caplog.text
    assert "kwargs_keys=['memo_spec', 'output_path']" in caplog.text

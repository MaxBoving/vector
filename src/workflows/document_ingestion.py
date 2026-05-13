from __future__ import annotations

import hashlib
import json
import re
from typing import List

from fastapi import HTTPException, UploadFile, status
from sqlmodel import Session, select

from src.api.schemas import DocumentSummaryResponse, DocumentUploadResponse
from src.core.database import engine
from src.core.execution import SecurityScan, StrategicTagger
from src.core.knowledge import KnowledgeManager
from src.core.models import CompanyState, User
from src.workflows.company_identity import rebuild_company_identity_profile


def build_document_id(company_name: str, title: str) -> str:
    digest = hashlib.sha256(f"{company_name}:{title}".encode("utf-8")).hexdigest()[:16]
    return f"doc_{digest}"


async def upload_document_to_knowledge_base(
    title: str,
    file: UploadFile,
    current_user: User,
    *,
    purpose: str = "reference",
    identity_role: str | None = None,
) -> DocumentUploadResponse:
    text_content = await _decode_upload(file)
    _validate_document_security(text_content, title)
    tags, summary = await _extract_document_intel(title, text_content)
    document_id = build_document_id(current_user.company_name, title)

    new_doc = {
        "document_id": document_id,
        "title": title,
        "content": text_content,
        "domains": tags,
        "summary": summary,
        "purpose": purpose,
        "identity_role": identity_role,
    }

    manager = KnowledgeManager(company_name=current_user.company_name)
    if not manager.add_documents([new_doc]):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to index document in semantic memory. Transaction aborted.",
        )

    with Session(engine) as session:
        state = session.exec(
            select(CompanyState).where(CompanyState.company_name == current_user.company_name)
        ).first()
        if not state:
            raise HTTPException(status_code=404, detail="Company State not found")

        current_kb = list(state.knowledge_base or [])
        current_kb.append(new_doc)
        state.knowledge_base = current_kb
        session.add(state)
        session.commit()

    if purpose == "example_material":
        rebuild_company_identity_profile(current_user.company_name)

    return DocumentUploadResponse(
        document_id=document_id,
        title=title,
        status="indexed",
        intel_summary=summary,
        purpose=purpose,
        identity_role=identity_role,
    )


def list_documents_for_user(current_user: User) -> List[DocumentSummaryResponse]:
    with Session(engine) as session:
        state = session.exec(
            select(CompanyState).where(CompanyState.company_name == current_user.company_name)
        ).first()
        if not state:
            return []

        return [
            DocumentSummaryResponse(
                document_id=doc.get("document_id") or build_document_id(current_user.company_name, doc.get("title", "Untitled document")),
                title=doc.get("title", "Untitled document"),
                status="indexed",
                intel_summary=doc.get("summary"),
                domains=doc.get("domains", []) or [],
                purpose=doc.get("purpose", "reference"),
                identity_role=doc.get("identity_role"),
            )
            for doc in state.knowledge_base
        ]


async def _decode_upload(file: UploadFile) -> str:
    content = await file.read()
    if content[:4] == b"%PDF":
        return _extract_pdf_bytes(content, file.filename or "upload.pdf")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="Only UTF-8 encoded text files and PDFs are supported.",
        ) from exc


def _extract_pdf_bytes(content: bytes, filename: str) -> str:
    import io
    lines: list[str] = []
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    lines.append(page_text)
                tables = page.extract_tables() or []
                for table in tables:
                    for row in table:
                        row_text = "\t".join(str(cell or "") for cell in row)
                        if row_text.strip():
                            lines.append(row_text)
        if lines:
            return "\n\n".join(lines)
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text.strip():
                lines.append(page_text)
        if lines:
            return "\n\n".join(lines)
    except Exception:
        pass
    raise HTTPException(
        status_code=400,
        detail=f"Could not extract text from PDF '{filename}'. The file may be scanned or encrypted.",
    )


def _validate_document_security(text_content: str, title: str) -> None:
    scan_res = SecurityScan.scan_file(text_content, title)
    if not scan_res["safe"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=scan_res["reason"])


async def _extract_document_intel(title: str, text_content: str) -> tuple[list[str], str]:
    tags = StrategicTagger.tag_document(text_content, title)
    return tags, ""

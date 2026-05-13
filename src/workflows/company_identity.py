from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from src.core.database import engine
from src.core.llm import DEFAULT_OPENAI_MODEL, LLMClient
from src.core.models import CompanyIdentityProfile, CompanyState, User


EXAMPLE_IDENTITY_ROLES = {
    "report_example",
    "workbook_example",
    "brand_reference",
}

EXAMPLE_MARKERS = ("report", "memo", "board", "workbook", "deck", "template", "forecast", "model")


class IdentityExampleDocument(BaseModel):
    document_id: str | None = None
    title: str
    identity_role: str = "brand_reference"
    purpose: str = "example_material"
    content: str = ""


class CompanyIdentityExtraction(BaseModel):
    tone: str = "executive"
    voice_traits: list[str] = Field(default_factory=list)
    brand_keywords: list[str] = Field(default_factory=list)
    preferred_formats: list[str] = Field(default_factory=list)
    section_patterns: list[str] = Field(default_factory=list)
    formatting_rules: list[str] = Field(default_factory=list)
    workbook_conventions: list[str] = Field(default_factory=list)
    document_conventions: list[str] = Field(default_factory=list)
    audience_signals: list[str] = Field(default_factory=list)
    reference_titles: list[str] = Field(default_factory=list)
    summary: str = ""


def infer_document_kind(title: str, identity_role: str) -> str:
    lowered = f"{title} {identity_role}".lower()
    if any(marker in lowered for marker in ("workbook", "model", "forecast", "sheet", "xlsx", "csv")):
        return "workbook"
    if any(marker in lowered for marker in ("memo", "report", "board", "update", "brief", "deck")):
        return "report"
    return "reference"


def _normalized_empty_profile() -> dict[str, Any]:
    return {
        "has_examples": False,
        "tone": None,
        "voice_traits": [],
        "brand_keywords": [],
        "preferred_formats": [],
        "section_patterns": [],
        "formatting_rules": [],
        "workbook_conventions": [],
        "document_conventions": [],
        "audience_signals": [],
        "reference_titles": [],
        "summary": "",
    }


def _truncate_content(content: str, limit: int = 3500) -> str:
    cleaned = (content or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit].rstrip()}\n...[truncated]"


def _build_extraction_prompt(company_name: str, example_documents: list[IdentityExampleDocument]) -> str:
    rendered_examples: list[str] = []
    for index, document in enumerate(example_documents[:4], start=1):
        rendered_examples.append(
            "\n".join(
                [
                    f"Example {index}",
                    f"Title: {document.title}",
                    f"Role: {document.identity_role}",
                    f"Document kind: {infer_document_kind(document.title, document.identity_role)}",
                    "Content:",
                    _truncate_content(document.content),
                ]
            )
        )

    return (
        f"Company: {company_name}\n\n"
        "Analyze the exemplar materials below and infer the company's reporting identity.\n"
        "Focus on reusable style patterns for executive reports and analytical workbooks.\n"
        "Extract tone, voice traits, formatting rules, section conventions, workbook conventions, and audience signals.\n"
        "Do not invent design details that are not evidenced in the examples.\n\n"
        + "\n\n---\n\n".join(rendered_examples)
    )


def _llm_identity_extraction(company_name: str, example_documents: list[IdentityExampleDocument]) -> CompanyIdentityExtraction | None:
    if not example_documents:
        return None

    client = LLMClient(model=DEFAULT_OPENAI_MODEL)
    prompt = _build_extraction_prompt(company_name, example_documents)
    system_prompt = (
        "You are an expert executive communications and corporate branding analyst. "
        "Infer a reusable reporting identity from exemplar materials. "
        "Return only structured data supported by the examples."
    )

    try:
        completion = client.complete_structured(prompt, CompanyIdentityExtraction, system_prompt)
    except Exception:
        return None

    return completion if isinstance(completion, CompanyIdentityExtraction) else None


def extract_identity_signals(company_name: str, example_documents: list[dict[str, Any]]) -> dict[str, Any]:
    if not example_documents:
        return _normalized_empty_profile()

    normalized_examples = [
        IdentityExampleDocument(
            document_id=doc.get("document_id"),
            title=str(doc.get("title", "Untitled")),
            identity_role=str(doc.get("identity_role") or "brand_reference"),
            purpose=str(doc.get("purpose") or "example_material"),
            content=str(doc.get("content", "")),
        )
        for doc in example_documents
        if isinstance(doc, dict)
    ]

    extraction = _llm_identity_extraction(company_name, normalized_examples)
    if extraction is None:
        return _normalized_empty_profile()

    return {
        "has_examples": True,
        "tone": extraction.tone,
        "voice_traits": extraction.voice_traits,
        "brand_keywords": extraction.brand_keywords,
        "preferred_formats": extraction.preferred_formats,
        "section_patterns": extraction.section_patterns,
        "formatting_rules": extraction.formatting_rules,
        "workbook_conventions": extraction.workbook_conventions,
        "document_conventions": extraction.document_conventions,
        "audience_signals": extraction.audience_signals,
        "reference_titles": extraction.reference_titles,
        "summary": extraction.summary,
    }


def rebuild_company_identity_profile(company_name: str) -> CompanyIdentityProfile:
    with Session(engine) as session:
        state = session.exec(select(CompanyState).where(CompanyState.company_name == company_name)).first()
        knowledge_base = list(state.knowledge_base or []) if state else []
        example_documents = [
            doc
            for doc in knowledge_base
            if isinstance(doc, dict) and doc.get("purpose") == "example_material"
        ]
        profile_payload = extract_identity_signals(company_name, example_documents)
        example_ids = [str(doc.get("document_id")) for doc in example_documents if doc.get("document_id")]

        profile = session.exec(
            select(CompanyIdentityProfile).where(CompanyIdentityProfile.company_name == company_name)
        ).first()
        if not profile:
            profile = CompanyIdentityProfile(company_name=company_name)

        profile.last_updated = datetime.now().isoformat()
        profile.profile_data = profile_payload
        profile.example_material_ids = example_ids
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile


def get_or_build_company_identity_profile(current_user: User) -> CompanyIdentityProfile:
    with Session(engine) as session:
        profile = session.exec(
            select(CompanyIdentityProfile).where(CompanyIdentityProfile.company_name == current_user.company_name)
        ).first()
        if profile:
            return profile
    return rebuild_company_identity_profile(current_user.company_name)

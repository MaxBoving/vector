from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from src.core.company_profile import CompanyProfile
from src.core.database import engine
from src.core.models import CompanyProfileRecord


def upsert_company_profile(*, ceo_id: str, company_name: str, profile: CompanyProfile) -> CompanyProfileRecord:
    payload = profile.model_dump(mode="json")
    readiness_summary = profile.minimum_readiness_summary()
    authoritative_coverage_ratio = profile.authoritative_coverage_ratio

    with Session(engine) as session:
        record = session.exec(
            select(CompanyProfileRecord).where(CompanyProfileRecord.ceo_id == ceo_id)
        ).first()
        if not record:
            record = CompanyProfileRecord(ceo_id=ceo_id, company_name=company_name)

        record.company_name = company_name
        record.last_updated = datetime.now().isoformat()
        record.profile_data = payload
        record.readiness_summary = readiness_summary
        record.authoritative_coverage_ratio = authoritative_coverage_ratio
        session.add(record)
        session.commit()
        session.refresh(record)
        return record


def get_company_profile(ceo_id: str) -> CompanyProfileRecord | None:
    with Session(engine) as session:
        return session.exec(
            select(CompanyProfileRecord).where(CompanyProfileRecord.ceo_id == ceo_id)
        ).first()


def company_profile_identity_view(profile_data: dict[str, Any]) -> dict[str, Any]:
    identity = dict(profile_data.get("company_identity", {}) or {})
    preferences = dict(profile_data.get("executive_preferences", {}) or {})
    canonical_memory = list(profile_data.get("canonical_memory", []) or [])
    document_titles = [
        str(item.get("title"))
        for item in canonical_memory
        if isinstance(item, dict) and item.get("category") == "document" and item.get("title")
    ]
    return {
        "company_name": identity.get("operating_name") or identity.get("legal_name") or "",
        "has_examples": bool(document_titles),
        "tone": preferences.get("preferred_tone"),
        "preferred_formats": list(preferences.get("preferred_artifacts", []) or []),
        "section_patterns": [],
        "reference_titles": document_titles[:8],
    }

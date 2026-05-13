import asyncio
import sys
from pathlib import Path

from sqlmodel import Session, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.api.routes.documents import get_company_profile_route, get_identity_profile, upsert_company_profile_route
from src.core.company_profile import CompanyProfile
from src.core.database import engine, init_db
from src.core.models import CompanyProfileRecord, User


def _user(ceo_id: str, company_name: str) -> User:
    return User(
        id=999,
        username=f"user_{ceo_id}",
        hashed_password="x",
        ceo_id=ceo_id,
        company_name=company_name,
    )


def _profile() -> CompanyProfile:
    return CompanyProfile(
        company_identity={
            "legal_name": "Acme Software, Inc.",
            "operating_name": "Acme",
            "industry": "B2B SaaS",
            "business_model": "Enterprise subscriptions",
            "company_stage": "Series B",
            "fiscal_year_definition": "Calendar year",
            "reporting_cadence": ["weekly", "monthly"],
            "strategic_priorities": ["Protect renewals"],
            "metadata": {
                "source": "onboarding interview",
                "owner": "chief_of_staff",
                "freshness": "weekly",
                "evidence_tier": "authoritative",
                "confidence": 0.95,
            },
        },
        executive_preferences={
            "preferred_tone": "direct",
            "preferred_artifacts": ["briefing", "memo"],
            "metadata": {
                "source": "ceo interview",
                "owner": "chief_of_staff",
                "freshness": "monthly",
                "evidence_tier": "authoritative",
                "confidence": 0.9,
            },
        },
        executive_team=[
            {
                "name": "Maya Chen",
                "title": "CEO",
                "decision_scope": ["Executive hiring"],
                "metadata": {
                    "source": "org chart",
                    "owner": "people_ops",
                    "freshness": "monthly",
                    "evidence_tier": "authoritative",
                    "confidence": 0.95,
                },
            }
        ],
        kpis=[
            {
                "name": "ARR",
                "definition": "Annual recurring revenue",
                "owner_role": "CFO",
                "reporting_frequency": "monthly",
                "source_of_truth": "finance workbook",
                "metadata": {
                    "source": "kpi glossary",
                    "owner": "fp&a",
                    "freshness": "monthly",
                    "evidence_tier": "authoritative",
                    "confidence": 0.95,
                },
            }
        ],
        canonical_memory=[
            {
                "category": "document",
                "title": "Board memo template",
                "summary": "Current board memo structure",
                "metadata": {
                    "source": "shared drive",
                    "owner": "chief_of_staff",
                    "freshness": "monthly",
                    "evidence_tier": "derived",
                    "confidence": 0.85,
                },
            }
        ],
        time_bound_events=[
            {
                "title": "Board review",
                "event_type": "board",
                "starts_at": "2026-04-03T15:00:00-07:00",
                "metadata": {
                    "source": "calendar",
                    "owner": "chief_of_staff",
                    "freshness": "live",
                    "evidence_tier": "authoritative",
                    "confidence": 0.98,
                },
            }
        ],
        source_systems=[
            {
                "system_name": "NetSuite",
                "function": "finance",
                "source_of_truth": True,
                "export_formats": ["csv"],
                "integration_status": "file_drop",
                "metadata": {
                    "source": "systems review",
                    "owner": "finance_systems",
                    "freshness": "monthly",
                    "evidence_tier": "authoritative",
                    "confidence": 0.9,
                },
            }
        ],
    )


def _clear_record(ceo_id: str) -> None:
    with Session(engine) as session:
        record = session.exec(select(CompanyProfileRecord).where(CompanyProfileRecord.ceo_id == ceo_id)).first()
        if record:
            session.delete(record)
            session.commit()


def test_company_profile_routes_store_and_read_ceo_scoped_profile() -> None:
    init_db()
    ceo_id = "ceo_company_profile_route_test"
    user = _user(ceo_id, "Acme")
    _clear_record(ceo_id)
    try:
        upsert_response = asyncio.run(upsert_company_profile_route(_profile(), user))
        get_response = asyncio.run(get_company_profile_route(user))
        identity_response = asyncio.run(get_identity_profile(user))

        assert upsert_response.ceo_id == ceo_id
        assert get_response.company_name == "Acme"
        assert get_response.readiness_summary["has_kpis"] is True
        assert identity_response.company_name == "Acme"
        assert identity_response.tone == "direct"
        assert "briefing" in identity_response.preferred_formats
        assert "Board memo template" in identity_response.reference_titles
    finally:
        _clear_record(ceo_id)

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.company_profile import CompanyProfile


def _metadata(source: str = "test") -> dict:
    return {
        "source": source,
        "owner": "ops",
        "freshness": "weekly",
        "evidence_tier": "authoritative",
        "confidence": 0.9,
    }


def test_company_profile_accepts_minimum_ready_payload() -> None:
    profile = CompanyProfile(
        company_identity={
            "legal_name": "Acme Software, Inc.",
            "operating_name": "Acme",
            "industry": "B2B SaaS",
            "business_model": "Enterprise subscriptions",
            "company_stage": "Series B",
            "fiscal_year_definition": "Calendar year",
            "reporting_cadence": ["weekly", "monthly"],
            "strategic_priorities": ["Protect renewals"],
            "metadata": _metadata("onboarding interview"),
        },
        executive_team=[
            {
                "name": "Maya Chen",
                "title": "CEO",
                "decision_scope": ["Executive hiring"],
                "metadata": _metadata("org chart"),
            }
        ],
        kpis=[
            {
                "name": "ARR",
                "definition": "Annual recurring revenue",
                "owner_role": "CFO",
                "reporting_frequency": "monthly",
                "source_of_truth": "finance workbook",
                "metadata": _metadata("kpi glossary"),
            }
        ],
        time_bound_events=[
            {
                "title": "Board review",
                "event_type": "board",
                "starts_at": "2026-04-03T15:00:00-07:00",
                "metadata": _metadata("calendar"),
            }
        ],
        source_systems=[
            {
                "system_name": "NetSuite",
                "function": "finance",
                "source_of_truth": True,
                "export_formats": ["csv"],
                "integration_status": "file_drop",
                "metadata": _metadata("systems review"),
            }
        ],
    )

    assert profile.minimum_readiness_summary()["has_identity"] is True
    assert profile.minimum_readiness_summary()["has_executive_team"] is True
    assert profile.authoritative_coverage_ratio == 1.0


def test_company_profile_requires_priorities_executive_team_and_kpis() -> None:
    with pytest.raises(ValueError):
        CompanyProfile(
            company_identity={
                "legal_name": "Acme Software, Inc.",
                "industry": "B2B SaaS",
                "business_model": "Enterprise subscriptions",
                "company_stage": "Series B",
                "fiscal_year_definition": "Calendar year",
                "reporting_cadence": ["weekly"],
                "strategic_priorities": [],
                "metadata": _metadata("onboarding interview"),
            },
            executive_team=[],
            kpis=[],
        )

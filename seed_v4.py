"""Realistic one-quarter seed for prototype demos.

CEO: Marcus Webb, Kepler Systems (B2B SaaS, healthcare compliance)
Period: Q1 2026 (January - March 2026)

Creates:
  - User + CEOPreferences
  - CompanyState (ARR, burn, headcount, initiatives)
  - CEOSituationalProfile
  - CEOMemory (past decisions / commitments)
  - ConnectedAccount[demo, crm] with 4 open deals
  - Email + calendar + signals via finance_close_week scenario

Usage:
  python seed_v4.py            # idempotent -- safe to re-run
  python seed_v4.py --wipe     # wipe all tables for this CEO first
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from src.core.database import (
    engine,
    init_db,
    save_object,
    upsert_connected_account,
)
from src.core.models import (
    CEOMemory,
    CEOPreferences,
    CEOSituationalProfile,
    CompanyState,
    User,
)
from src.workflows.demo_executive_context import seed_demo_executive_context
from sqlmodel import Session, delete, select


CEO_ID = "marcus_webb_ceo"
USERNAME = "marcus.webb"
COMPANY = "Kepler Systems"
ANCHOR_DATE = "2026-03-28"


def _hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert(session: Session, model_cls, unique_filters: dict, obj):
    stmt = select(model_cls)
    for attr, val in unique_filters.items():
        stmt = stmt.where(getattr(model_cls, attr) == val)
    existing = session.exec(stmt).first()
    if existing:
        session.delete(existing)
        session.commit()
    session.add(obj)
    session.commit()


def seed_user(session: Session) -> None:
    _upsert(session, User, {"ceo_id": CEO_ID}, User(
        username=USERNAME,
        hashed_password=_hash_password("demo-password-2026"),
        ceo_id=CEO_ID,
        company_name=COMPANY,
    ))
    print(f"  ok User: {USERNAME} ({CEO_ID})")


def seed_preferences(session: Session) -> None:
    _upsert(session, CEOPreferences, {"ceo_id": CEO_ID}, CEOPreferences(
        ceo_id=CEO_ID,
        preferred_tone="direct",
        max_summary_length=400,
        risk_profile={
            "product": "balanced",
            "finance": "conservative",
            "hiring": "aggressive",
            "m_and_a": "conservative",
        },
        decision_velocity=7,
        agent_traits={
            "librarian": {"depth": 70, "citation_style": "inline"},
            "quant": {"confidence_threshold": 0.8, "show_assumptions": True},
        },
        meeting_logic=[
            "Decline recurring syncs with no agenda",
            "Board prep meetings always accepted",
            "Investor calls always accepted",
            "Customer escalation calls always accepted",
        ],
        priority_senders=[
            "maya@northstarhealth.com",
            "partner@horizonventures.vc",
            "david@keplersystems.com",
            "sarah@keplersystems.com",
        ],
        priority_domains=["horizonventures.vc", "northstarhealth.com"],
        ignored_senders=["noreply@linkedin.com"],
        ignored_domains=["newsletterservice.com"],
        low_trust_metrics=["Sales Pipeline", "NPS Score"],
    ))
    print("  ok CEOPreferences")


def seed_company_state(session: Session) -> None:
    _upsert(session, CompanyState, {"company_name": COMPANY}, CompanyState(
        company_name=COMPANY,
        last_updated=_now(),
        revenue_segmentation={
            "enterprise": 62_400_000.0,
            "mid_market": 12_500_000.0,
            "smb": 3_500_000.0,
        },
        cost_structure={
            "r_and_d": 1_100_000.0,
            "sales_marketing": 780_000.0,
            "g_and_a": 290_000.0,
            "cogs": 130_000.0,
        },
        capital_position={
            "cash_on_hand": 18_700_000.0,
            "monthly_burn": 2_300_000.0,
            "runway_months": 8.1,
            "arr": 78_400_000.0,
            "arr_growth_yoy": 0.42,
            "nrr": 1.18,
            "gross_margin": 0.74,
        },
        strategic_initiatives=[
            {
                "name": "Series D Close",
                "owner": "Marcus Webb",
                "status": "in_progress",
                "priority": 1,
                "target_date": "2026-04-15",
                "description": "Close $28M Series D led by Horizon Ventures at $180M pre-money.",
            },
            {
                "name": "SOC 2 Type II Certification",
                "owner": "Sarah Kim (CTO)",
                "status": "in_progress",
                "priority": 2,
                "target_date": "2026-06-30",
                "description": "Required by Northstar Health and Memorial Medical before expansion.",
            },
            {
                "name": "Northstar Health Expansion",
                "owner": "David Osei (VP Sales)",
                "status": "negotiation",
                "priority": 3,
                "target_date": "2026-03-31",
                "description": "$2.4M expansion from 4 to 12 hospital sites. Final MSA markup in review.",
            },
            {
                "name": "Kepler v3 Launch",
                "owner": "Sarah Kim (CTO)",
                "status": "on_track",
                "priority": 4,
                "target_date": "2026-05-01",
                "description": "AI-assisted compliance workflow engine. Core feature for enterprise upsell.",
            },
            {
                "name": "Cloud Cost Covenant Remediation",
                "owner": "David Chen (CFO)",
                "status": "at_risk",
                "priority": 5,
                "target_date": "2026-04-01",
                "description": "Series C docs cap cloud at $1.1M/mo. Currently $1.32M. Need 17% cut.",
            },
        ],
        org_structure={
            "ceo": "Marcus Webb",
            "cto": "Sarah Kim",
            "cfo": "David Chen",
            "vp_sales": "David Osei",
            "vp_customer_success": "Priya Mehta",
            "vp_engineering": "Tom Blackwell",
            "head_of_people": "Aisha Reyes",
        },
        regulatory_footprint=[
            "HIPAA (US healthcare data)",
            "SOC 2 Type I (current)",
            "SOC 2 Type II (in progress, target June 2026)",
            "GDPR (EU customers)",
        ],
        knowledge_base=[
            {
                "title": "Kepler Systems Product Overview",
                "content": (
                    "Kepler Systems is a B2B SaaS company providing AI-assisted compliance "
                    "workflow software for US healthcare providers. ARR $78.4M, 42% YoY growth."
                ),
            },
            {
                "title": "Series D Funding Context",
                "content": (
                    "Raising $28M Series D led by Horizon Ventures (James Park). "
                    "Pre-money $180M. Funds: $12M R&D, $10M sales, $6M working capital. "
                    "Term sheet signed 2026-03-10. Close target: 2026-04-15."
                ),
            },
            {
                "title": "Northstar Health Account",
                "content": (
                    "Largest customer. $2.4M expansion in negotiation (4 to 12 hospital sites). "
                    "Champion: Maya Chen, VP Operations. Gated on SOC 2 Type II + 99.9% uptime SLA."
                ),
            },
        ],
    ))
    print("  ok CompanyState")


def seed_situational_profile(session: Session) -> None:
    _upsert(session, CEOSituationalProfile, {"ceo_id": CEO_ID}, CEOSituationalProfile(
        ceo_id=CEO_ID,
        operating_mode="fundraising+close",
        active_pressures=[
            "Series D closing window -- investor due diligence active",
            "Cloud cost covenant at risk -- 17% reduction required by April 1",
            "Northstar Health MSA in final markup -- deal cannot slip",
            "SOC 2 Type II audit preparation ongoing",
            "Q1 board presentation in 3 days",
        ],
        recurring_topics=[
            {"topic": "Series D diligence", "frequency": "daily", "last_raised": "2026-03-27"},
            {"topic": "Northstar Health expansion", "frequency": "daily", "last_raised": "2026-03-27"},
            {"topic": "Cloud spend", "frequency": "weekly", "last_raised": "2026-03-25"},
            {"topic": "SOC 2 audit progress", "frequency": "weekly", "last_raised": "2026-03-24"},
        ],
        open_threads=[
            {"thread": "Cloud covenant remediation", "owner": "David Chen", "due": "2026-04-01", "status": "in_progress"},
            {"thread": "Northstar MSA redlines", "owner": "Rachel Torres", "due": "2026-03-31", "status": "awaiting_response"},
            {"thread": "Series D wire confirmation", "owner": "James Park (Horizon)", "due": "2026-04-15", "status": "open"},
        ],
        relationship_obligations=[
            "Board update due 2026-03-31 -- Q1 financial summary",
            "Maya Chen (Northstar) -- CEO-to-CEO commitment on SOC 2 timeline",
            "James Park (Horizon) -- weekly diligence check-in Thursdays",
        ],
        inferred_blind_spots=[
            "EU GDPR exposure not factored into Series D risk disclosure",
            "SMB churn rate not reviewed in Q4 board materials",
        ],
    ))
    print("  ok CEOSituationalProfile")


def seed_memories() -> None:
    memories = [
        CEOMemory(
            memory_id="mem-001-series-d-term",
            ceo_id=CEO_ID,
            memory_type="decision",
            title="Accepted Horizon Ventures Series D term sheet",
            content=(
                "On 2026-03-10 Marcus accepted Horizon Ventures term sheet for $28M Series D "
                "at $180M pre-money. Anti-dilution: weighted average broad-based. "
                "Board seat: 1 new independent director nominated by Horizon. "
                "Chose Horizon over Sequoia ($165M offer) for healthcare vertical expertise."
            ),
            tags=["fundraising", "series-d", "board", "horizon-ventures"],
            created_at="2026-03-10T14:30:00+00:00",
        ),
        CEOMemory(
            memory_id="mem-002-northstar-commitment",
            ceo_id=CEO_ID,
            memory_type="commitment",
            title="Committed SOC 2 Type II delivery to Northstar by June 2026",
            content=(
                "CEO-to-CEO call 2026-02-18: committed to Maya Chen (Northstar Health) "
                "that SOC 2 Type II certification will be complete by June 30, 2026. "
                "This gates the $2.4M expansion. Sarah Kim (CTO) owns the timeline."
            ),
            tags=["northstar-health", "soc2", "commitment", "enterprise"],
            created_at="2026-02-18T16:00:00+00:00",
        ),
        CEOMemory(
            memory_id="mem-003-cto-hire",
            ceo_id=CEO_ID,
            memory_type="milestone",
            title="Promoted Sarah Kim to CTO",
            content=(
                "2026-01-15: Promoted Sarah Kim from VP Engineering to CTO. "
                "Rationale: Kepler v3 AI product pivot requires C-suite engineering leadership. "
                "Tom Blackwell retained as VP Engineering. Comp: $320K + 0.4% equity."
            ),
            tags=["hiring", "cto", "sarah-kim", "org"],
            created_at="2026-01-15T09:00:00+00:00",
        ),
        CEOMemory(
            memory_id="mem-004-cloud-covenant",
            ceo_id=CEO_ID,
            memory_type="fact",
            title="Cloud cost covenant limits spend to $1.1M/month",
            content=(
                "Series C investment agreement (2024) caps monthly cloud spend at $1.1M. "
                "Current spend March 2026: $1.32M -- 17% over covenant. "
                "Breach triggers investor notification. David Chen leading remediation: "
                "reserved instances migration, target savings $260K/mo."
            ),
            tags=["cloud", "covenant", "finance", "risk"],
            created_at="2026-03-01T08:00:00+00:00",
        ),
        CEOMemory(
            memory_id="mem-005-q4-board",
            ceo_id=CEO_ID,
            memory_type="decision",
            title="Board approved 2026 headcount plan: +18 engineering, +6 sales",
            content=(
                "Q4 2025 board meeting (2025-12-10): approved +18 engineering and +6 "
                "enterprise sales roles. Engineering focused on Kepler v3 AI engine (8 roles) "
                "and platform reliability (10 roles). Current headcount: 142, target EOY: 166."
            ),
            tags=["hiring", "headcount", "board"],
            created_at="2025-12-10T17:00:00+00:00",
        ),
        CEOMemory(
            memory_id="mem-006-memorial-intro",
            ceo_id=CEO_ID,
            memory_type="fact",
            title="Memorial Medical Center intro via Northstar referral",
            content=(
                "2026-03-05: Maya Chen introduced Marcus to Lisa Park (CIO, Memorial Medical). "
                "6-hospital California network, ~$840K budget. Intro call 2026-04-02. "
                "SOC 2 Type II also required."
            ),
            tags=["memorial-medical", "pipeline", "referral"],
            created_at="2026-03-05T11:00:00+00:00",
        ),
    ]

    with Session(engine) as session:
        existing = session.exec(select(CEOMemory).where(CEOMemory.ceo_id == CEO_ID)).all()
        for m in existing:
            session.delete(m)
        session.commit()
        for m in memories:
            session.add(m)
        session.commit()
    print(f"  ok CEOMemory ({len(memories)} records)")


def seed_crm_deals() -> None:
    deals = [
        {
            "deal_id": "demo-deal-001",
            "crm": "demo",
            "name": "Northstar Health -- Enterprise Expansion",
            "amount": 2_400_000.0,
            "stage": "Negotiation",
            "pipeline": "enterprise",
            "close_date": "2026-03-31",
            "win_probability": 0.75,
            "last_modified": "2026-03-26T14:00:00Z",
            "description": (
                "Expansion from 4 to 12 hospital sites. Gated on SOC 2 Type II and 99.9% "
                "API uptime SLA. MSA final redlines with Rachel Torres (outside counsel). "
                "Champion: Maya Chen, VP Operations."
            ),
            "account_name": "Northstar Health",
            "owner_id": CEO_ID,
            "contacts": [
                {"name": "Maya Chen", "email": "maya@northstarhealth.com", "title": "VP Operations", "role": "Champion"},
                {"name": "Rachel Torres", "email": "rtorres@counsel.com", "title": "Outside Counsel", "role": "Legal"},
            ],
        },
        {
            "deal_id": "demo-deal-002",
            "crm": "demo",
            "name": "Memorial Medical Center -- Compliance Suite",
            "amount": 840_000.0,
            "stage": "Demo",
            "pipeline": "enterprise",
            "close_date": "2026-04-30",
            "win_probability": 0.40,
            "last_modified": "2026-03-20T10:00:00Z",
            "description": (
                "6-hospital California network, Northstar referral. Champion: Lisa Park, CIO. "
                "Requires SOC 2 Type II. Intro call 2026-04-02."
            ),
            "account_name": "Memorial Medical Center",
            "owner_id": "david_osei",
            "contacts": [
                {"name": "Lisa Park", "email": "lpark@memorialmed.org", "title": "CIO", "role": "Champion"},
            ],
        },
        {
            "deal_id": "demo-deal-003",
            "crm": "demo",
            "name": "Pacific Health Systems -- Mid-Market",
            "amount": 1_200_000.0,
            "stage": "Proposal",
            "pipeline": "mid_market",
            "close_date": "2026-05-15",
            "win_probability": 0.55,
            "last_modified": "2026-03-22T09:00:00Z",
            "description": (
                "9-clinic Texas network. Evaluating Kepler vs ComplyIQ. SOC 2 Type I sufficient. "
                "Champion: Brett Nguyen, Compliance Director."
            ),
            "account_name": "Pacific Health Systems",
            "owner_id": "david_osei",
            "contacts": [
                {"name": "Brett Nguyen", "email": "bnguyen@pacifichealthsys.com", "title": "Compliance Director", "role": "Champion"},
            ],
        },
        {
            "deal_id": "demo-deal-004",
            "crm": "demo",
            "name": "Coastal Clinic Group -- SMB",
            "amount": 180_000.0,
            "stage": "Discovery",
            "pipeline": "smb",
            "close_date": "2026-06-30",
            "win_probability": 0.25,
            "last_modified": "2026-03-18T08:00:00Z",
            "description": "4-clinic Florida practice, inbound from website. Discovery stage.",
            "account_name": "Coastal Clinic Group",
            "owner_id": "david_osei",
            "contacts": [
                {"name": "Sandra Webb", "email": "swebb@coastalclinic.com", "title": "Office Manager", "role": "Evaluator"},
            ],
        },
    ]

    upsert_connected_account(
        CEO_ID,
        "demo",
        "crm",
        access_token="demo-seeded",
        account_email=f"{CEO_ID}.demo@agenticmind.local",
        scopes=["demo:read"],
        metadata={"demo": True, "deals": deals, "seeded_at": _now()},
    )
    print(f"  ok CRM deals ({len(deals)} deals in ConnectedAccount)")


def seed_email_calendar_signals() -> None:
    result = seed_demo_executive_context(
        ceo_id=CEO_ID,
        company_name=COMPANY,
        scenario="finance_close_week",
        anchor_date=ANCHOR_DATE,
    )
    threads = (result.get("email_event") or {}).get("ranked_threads", [])
    events = (result.get("calendar_event") or {}).get("upcoming_events", [])
    signals = result.get("signals", [])
    print(f"  ok Email: {len(threads)} threads  |  Calendar: {len(events)} events  |  Signals: {len(signals)}")


def wipe_ceo(session: Session) -> None:
    from src.core.models import IncomingSignal, ConnectedAccount
    for model, field in [
        (User, "ceo_id"),
        (CEOPreferences, "ceo_id"),
        (CEOSituationalProfile, "ceo_id"),
        (CEOMemory, "ceo_id"),
        (IncomingSignal, "ceo_id"),
        (ConnectedAccount, "ceo_id"),
    ]:
        session.exec(delete(model).where(getattr(model, field) == CEO_ID))  # type: ignore[arg-type]
    session.exec(delete(CompanyState).where(CompanyState.company_name == COMPANY))  # type: ignore[arg-type]
    session.commit()
    print(f"  ok Wiped all records for {CEO_ID} / {COMPANY}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed realistic Q1 2026 CEO demo data")
    parser.add_argument("--wipe", action="store_true", help="Wipe CEO records before seeding")
    args = parser.parse_args()

    init_db()
    print(f"\nSeeding: {CEO_ID} | {COMPANY} | anchor {ANCHOR_DATE}\n")

    with Session(engine) as session:
        if args.wipe:
            print("[wipe]")
            wipe_ceo(session)
            print()

        print("[user + prefs]")
        seed_user(session)
        seed_preferences(session)

        print("\n[company state]")
        seed_company_state(session)

        print("\n[situational profile]")
        seed_situational_profile(session)

    print("\n[memories]")
    seed_memories()

    print("\n[crm deals]")
    seed_crm_deals()

    print("\n[email / calendar / signals]")
    seed_email_calendar_signals()

    print("\n[chroma index]")
    index_memories_in_chroma()

    print(f"\nDone. Run a trace:\n  python scripts/query_trace.py --ceo {CEO_ID} \"What's in my pipeline?\"\n")


def index_memories_in_chroma() -> None:
    """Index seeded CEOMemory records in Chroma for semantic search."""
    try:
        from src.core.knowledge import index_memory
    except ImportError:
        print("  skip (knowledge module unavailable)")
        return

    from src.core.database import engine
    from src.core.models import CEOMemory
    from sqlmodel import Session, select

    ENTITY_MAP = {
        "mem-001-series-d-term": ["Horizon Ventures", "Series D"],
        "mem-002-northstar-commitment": ["Northstar Health", "Maya Chen", "SOC 2"],
        "mem-003-cto-hire": ["Sarah Kim", "Tom Blackwell"],
        "mem-004-cloud-covenant": ["David Chen", "AWS"],
        "mem-005-q4-board": ["Board"],
        "mem-006-memorial-intro": ["Memorial Medical Center", "Lisa Park", "Northstar Health"],
    }

    with Session(engine) as session:
        memories = session.exec(select(CEOMemory).where(CEOMemory.ceo_id == CEO_ID)).all()
        for m in memories:
            index_memory(
                memory_id=m.memory_id,
                ceo_id=CEO_ID,
                title=m.title,
                content=m.content,
                memory_type=m.memory_type,
                entities=ENTITY_MAP.get(m.memory_id, []),
                tags=m.tags or [],
            )
    print(f"  ok indexed {len(memories)} memories in Chroma")


if __name__ == "__main__":
    main()

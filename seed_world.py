"""Persistent world seed — Vela / Jordan Kessler.

CEO: Jordan Kessler, Vela (GTM intelligence platform, Series B)
Dates are generated from the current local date at seed time so the demo world stays current.

Usage:
    python seed_world.py              # idempotent, safe to re-run
    python seed_world.py --wipe       # wipe all records for this CEO first
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
from passlib.context import CryptContext

from src.core.database import engine, init_db, save_object
from src.core.models import (
    AssistantConversation,
    CEOMemory,
    CEOPreferences,
    CEOSituationalProfile,
    CompanyIdentityProfile,
    CompanyState,
    IncomingSignal,
    SessionInteraction,
    User,
)
from src.workflows.world_simulation import build_seed_world_snapshot
from sqlmodel import Session, delete, select

CEO_ID = "ceo_001"
USERNAME = "jordan.kessler"
COMPANY = "Vela"
_FIXTURES = Path(__file__).parent / "src" / "dev" / "fixtures"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _today() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _iso_date(days_offset: int = 0) -> str:
    return (_today().date() + timedelta(days=days_offset)).isoformat()


def _iso_timestamp(days_offset: int = 0, *, hour: int = 0, minute: int = 0) -> str:
    dt = _today().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (dt + timedelta(days=days_offset)).isoformat()


def _hash(pw: str) -> str:
    return pwd_context.hash(pw)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upsert(session: Session, model_cls, filter_field: str, filter_val, obj) -> None:
    stmt = select(model_cls).where(getattr(model_cls, filter_field) == filter_val)
    existing = session.exec(stmt).first()
    if existing:
        session.delete(existing)
        session.commit()
    session.add(obj)
    session.commit()


def _load_fixture(name: str) -> dict:
    path = _FIXTURES / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _seed_user(session: Session, ceo_id: str, username: str, password: str) -> None:
    user = User(
        ceo_id=ceo_id,
        username=username,
        hashed_password=_hash(password),
        company_name=COMPANY,
    )
    _upsert(session, User, "ceo_id", ceo_id, user)


def _seed_preferences(session: Session, ceo_id: str) -> None:
    prefs = CEOPreferences(
        ceo_id=ceo_id,
        preferred_tone="concise",
        tone="concise",
        risk_profile={
            "deals": "risk_tolerant",
            "financials": "risk_averse",
        },
        decision_velocity=8,
        learned_defaults={
            "email_approval": "always_show_subject_line_first",
            "board_comms": "always_include_financial_context",
            "briefing_format": "bullets_not_prose",
            "email_length": "concise_prefer_short",
        },
    )
    _upsert(session, CEOPreferences, "ceo_id", ceo_id, prefs)


def _seed_company_state(session: Session, ceo_id: str) -> None:
    fin = _load_fixture("financials")
    metrics = fin.get("current_metrics", {})
    monthly_pnl = fin.get("monthly_pnl", [])

    state = CompanyState(
        company_name=COMPANY,
        ceo_id=ceo_id,
        last_updated=_now(),
        arr=metrics.get("arr"),
        mrr=metrics.get("mrr"),
        headcount=metrics.get("headcount"),
        burn_monthly=metrics.get("burn_monthly"),
        runway_months=metrics.get("runway_months"),
        revenue_segmentation={
            "nrr": metrics.get("nrr", 1.08),
            "gross_margin": metrics.get("gross_margin", 0.74),
            "sales_efficiency": metrics.get("sales_efficiency", 0.71),
            "yoy_growth": 0.68,
            "q1_vs_plan": 0.03,
            "q4_vs_plan": -0.07,
        },
        knowledge_base=[
            {"title": f"P&L {entry['month']}", "content": json.dumps(entry)}
            for entry in monthly_pnl
        ],
    )
    stmt = select(CompanyState).where(CompanyState.company_name == COMPANY)
    existing = session.exec(stmt).first()
    if existing:
        session.delete(existing)
        session.commit()
    session.add(state)
    session.commit()


def _seed_company_identity(session: Session, ceo_id: str) -> None:
    profile_data = {
        "board_members": [
            {"name": "Dana Whitfield", "firm": "Emergence Capital", "role": "Lead investor"},
            {"name": "Marcus Tell", "firm": "Angel", "role": "Advisor / Angel"},
            {"name": "Priya Anand", "firm": "Independent", "role": "Independent director"},
        ],
        "executive_team": [
            {"name": "Jordan Kessler", "title": "CEO"},
            {"name": "Sam", "title": "CTO"},
            {"name": "Rachel", "title": "VP Sales"},
            {"name": "Tyler", "title": "VP Customer Success"},
            {"name": "Nina", "title": "VP Engineering"},
            {"name": "Farrukh", "title": "Head of Finance"},
        ],
        "key_customers": [
            "Ironstone Partners",
            "Omni Logistics",
            "Apex Manufacturing",
            "Northbridge Capital",
            "Crestline Technologies",
            "Fortis Group",
        ],
        "icp": "Mid-market B2B SaaS and tech-enabled services companies with 100-1000 employees",
        "products": [
            "Vela GTM Intelligence Platform",
            "Vela Forecast",
            "Vela Signals",
        ],
    }
    identity = CompanyIdentityProfile(
        company_name=COMPANY,
        last_updated=_now(),
        profile_data=profile_data,
    )
    _upsert(session, CompanyIdentityProfile, "company_name", COMPANY, identity)


def _seed_situational_profile(session: Session, ceo_id: str) -> None:
    profile = CEOSituationalProfile(
        ceo_id=ceo_id,
        operating_mode="board_prep",
        active_pressures=[
            "Board packet due in 5 days — deck not started",
            "Ironstone EU data residency requirement blocking renewal",
            "Omni Logistics churn risk — 42% usage drop",
            "CFO vacancy — 3 months open, Farrukh interim",
            "Board meeting in 18 days — NRR story needs to hold",
        ],
        inferred_blind_spots=[
            "Drexel silent for 3 weeks — no outreach logged",
            "Summit Ventures action items 2 weeks overdue",
            "Board deck location confusion — Google Drive vs local",
            "Headcount model in financial model is wrong (eng count off by 2)",
        ],
        recurring_topics=[
            {"topic": "NRR gap vs plan", "mention_count": 4, "last_seen": _iso_date(0), "resolved": False},
            {"topic": "Omni churn risk", "mention_count": 3, "last_seen": _iso_date(0), "resolved": False},
            {"topic": "Apex stalled deal", "mention_count": 2, "last_seen": _iso_date(-3), "resolved": False},
            {"topic": "Crestline bug", "mention_count": 2, "last_seen": _iso_date(-8), "resolved": False},
            {"topic": "CFO vacancy", "mention_count": 5, "last_seen": _iso_date(0), "resolved": False},
            {"topic": "Series C prep", "mention_count": 3, "last_seen": _iso_date(0), "resolved": False},
            {"topic": "Rep performance Q1", "mention_count": 2, "last_seen": _iso_date(-2), "resolved": False},
        ],
        open_threads=[
            {"thread": "Ironstone EU data residency — Sam owns eng scoping", "owner": "Sam", "due": _iso_date(6)},
            {"thread": "Omni EBR scheduled — Tyler to prep", "owner": "Tyler", "due": _iso_date(3)},
            {"thread": "CFO search — final interview Sandra Meir pending", "owner": "Jordan", "due": _iso_date(11)},
        ],
        relationship_obligations=[
            "Dana Whitfield — board update call before packet goes out",
            "Priya Anand — NRR context note requested",
            "Marcus Tell — intro to Drexel CRO pending",
        ],
        updated_at=_now(),
    )
    _upsert(session, CEOSituationalProfile, "ceo_id", ceo_id, profile)


def _seed_signals(ceo_id: str) -> None:
    signals = [
        IncomingSignal(
            ceo_id=ceo_id,
            source="product_analytics",
            sender="analytics-bot@vela.ai",
            subject="Omni Logistics usage down 42%",
            content="Weekly product analytics alert: Omni Logistics seat utilization dropped from 74% to 43% over the last 21 days. Primary drop in Signals module. Last active user login: 6 days ago.",
            importance="critical",
            strategic_concepts=["churn_risk", "customer_health", "nrr"],
            talking_points=["Usage collapse in Signals module", "No active login in 6 days", "EBR overdue"],
            status="UNREAD",
        ),
        IncomingSignal(
            ceo_id=ceo_id,
            source="cs_platform",
            sender="gainsight@vela.ai",
            subject="Omni Logistics usage alert [DUPLICATE]",
            content="[Gainsight] Health score dropped to RED for Omni Logistics. Duplicate of product analytics signal.",
            importance="high",
            strategic_concepts=["churn_risk", "customer_health"],
            talking_points=["Duplicate signal", "Gainsight health score RED"],
            status="UNREAD",
        ),
        IncomingSignal(
            ceo_id=ceo_id,
            source="news_monitor",
            sender="news@vela.ai",
            subject="Gong acquired ForecastAI — $180M [3 weeks delayed]",
            content="Gong announced acquisition of ForecastAI for $180M. Signal delayed 3 weeks due to monitor misconfiguration. Competitive overlap with Vela Forecast.",
            importance="high",
            strategic_concepts=["competitive_threat", "market_intelligence", "positioning"],
            talking_points=["Gong enters forecast intelligence", "3-week delay", "Positioning question for board"],
            status="UNREAD",
        ),
        IncomingSignal(
            ceo_id=ceo_id,
            source="crm_system",
            sender="salesforce@vela.ai",
            subject="Apex Manufacturing — close date passed, no activity",
            content="Deal: Apex Manufacturing (opp_187, $420K ARR). Close date was 2026-03-15. No activity in 18 days.",
            importance="high",
            strategic_concepts=["pipeline_risk", "deal_stall", "revenue"],
            talking_points=["$420K deal stalled", "No rep activity", "Needs exec intervention"],
            status="UNREAD",
        ),
        IncomingSignal(
            ceo_id=ceo_id,
            source="crm_system",
            sender="salesforce@vela.ai",
            subject="Stale reference: deal ID opp_209 not found",
            content="CRM integrity alert: workflow referenced deal opp_209 which does not exist. Possible data hygiene issue.",
            importance="low",
            strategic_concepts=["data_quality"],
            talking_points=["Stale CRM reference"],
            status="UNREAD",
        ),
        IncomingSignal(
            ceo_id=ceo_id,
            source="finance",
            sender="farrukh@vela.ai",
            subject="February burn 10% over plan — $34K misc unexplained",
            content="February actual burn: $934K vs plan $848K (+10.1%). Misc line: $34K with no category code. Needs explanation before board packet.",
            importance="medium",
            strategic_concepts=["burn_rate", "financial_control", "board_prep"],
            talking_points=["$34K unexplained misc", "Hosting normalized in March", "Need explanation before board"],
            status="UNREAD",
        ),
        IncomingSignal(
            ceo_id=ceo_id,
            source="hr",
            sender="hr@vela.ai",
            subject="Two reps below 70% attainment Q1",
            content="Q1 attainment report: 2 of 6 AEs below 70% threshold. Rep A: 58%. Rep B: 64%. Rachel reviewing.",
            importance="medium",
            strategic_concepts=["sales_performance", "team_health", "quota_attainment"],
            talking_points=["2 reps below PIP threshold", "Rachel owns review"],
            status="UNREAD",
        ),
        IncomingSignal(
            ceo_id=ceo_id,
            source="market",
            sender="news@vela.ai",
            subject="ICP segment growth: mid-market B2B SaaS +18% YoY",
            content="Mid-market B2B SaaS segment grew 18% YoY in 2025. TAM expansion validates Series C narrative.",
            importance="low",
            strategic_concepts=["tam", "market_growth", "series_c_narrative"],
            talking_points=["ICP segment +18% YoY", "Supports Series C story"],
            status="UNREAD",
        ),
    ]
    with Session(engine) as session:
        for sig in signals:
            session.add(sig)
        session.commit()


def _seed_memories(ceo_id: str) -> None:
    memories = [
        CEOMemory(
            memory_id=f"mem_{ceo_id}_001",
            ceo_id=ceo_id,
            memory_type="fact",
            title="Ironstone EU data residency requirement",
            content="Ironstone Partners requires EU data residency for renewal (April 15). Sam scoping. Jordan committed to feasibility answer by April 5. Blocking $280K ARR.",
            tags=["ironstone", "data_residency", "renewal"],
            created_at=_now(),
        ),
        CEOMemory(
            memory_id=f"mem_{ceo_id}_002",
            ceo_id=ceo_id,
            memory_type="fact",
            title="Omni Logistics renewal situation",
            content="Omni Logistics ($190K ARR) at churn risk. Usage dropped 42%. Tyler scheduling EBR. No exec sponsor in 60 days.",
            tags=["omni", "churn_risk", "renewal"],
            created_at=_now(),
        ),
        CEOMemory(
            memory_id=f"mem_{ceo_id}_003",
            ceo_id=ceo_id,
            memory_type="decision",
            title="Fortis loss lesson — no deep discount without exec sponsor",
            content="Fortis churned after 30% discount didn't close. Policy: >20% discount requires Jordan approval.",
            tags=["fortis", "churn", "pricing_policy"],
            created_at=_now(),
        ),
        CEOMemory(
            memory_id=f"mem_{ceo_id}_004",
            ceo_id=ceo_id,
            memory_type="preference",
            title="Dana Whitfield NRR story preference",
            content="Dana (Emergence) pushes back on defensive NRR narrative. Always lead with root cause and fix-in-motion. Never appear surprised.",
            tags=["dana_whitfield", "board", "nrr"],
            created_at=_now(),
        ),
        CEOMemory(
            memory_id=f"mem_{ceo_id}_005",
            ceo_id=ceo_id,
            memory_type="fact",
            title="CFO search — Sandra Meir final interview",
            content="Final CFO candidate: Sandra Meir (ex-Zendesk). Final interview not scheduled. Korn Ferry waiting on Jordan's calendar week of April 14.",
            tags=["cfo_search", "sandra_meir", "hiring"],
            created_at=_now(),
        ),
    ]
    with Session(engine) as session:
        for mem in memories:
            existing = session.exec(select(CEOMemory).where(CEOMemory.memory_id == mem.memory_id)).first()
            if existing:
                session.delete(existing)
                session.commit()
            session.add(mem)
        session.commit()


def _seed_interaction_history(ceo_id: str) -> None:
    conv_id = f"conv:{ceo_id}:seed_world_20260328"
    with Session(engine) as session:
        existing_conv = session.exec(
            select(AssistantConversation).where(AssistantConversation.conversation_id == conv_id)
        ).first()
        if existing_conv:
            for iid in (existing_conv.interaction_ids or []):
                existing_int = session.get(SessionInteraction, iid)
                if existing_int:
                    session.delete(existing_int)
            session.delete(existing_conv)
            session.commit()

        conv = AssistantConversation(
            conversation_id=conv_id,
            ceo_id=ceo_id,
            title="Morning briefing — Q1 close week",
            created_at=_iso_timestamp(0, hour=7),
            updated_at=_iso_timestamp(0, hour=9, minute=45),
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)

        interactions_data = [
            {
                "query": "Give me a quick briefing on the top 3 things I need to handle today.",
                "response": "1. Omni churn signal — usage down 42%.\n2. Board packet — due in 5 days, not started.\n3. Apex deal — $420K stalled past close date.",
                "intent": "daily_briefing",
            },
            {
                "query": "Draft an email to the Omni Logistics account executive asking for a status update.",
                "response": "Subject: Quick check-in — Omni Logistics partnership\n\nHi [AE], wanted to connect on Omni before end of week. Can you share where the relationship stands?\n\nJordan",
                "intent": "email_draft",
            },
            {
                "query": "What is our current ARR and how does it compare to plan?",
                "response": "Current ARR: $14.2M. Q1 ended 3% above plan. NRR at 108%, 2pts below board target of 110%.",
                "intent": "financial_query",
            },
            {
                "query": "Pull together a board packet outline for the April 15 meeting.",
                "response": "Board Packet Outline: 1. Q1 Highlights 2. Financials 3. Customer Health 4. GTM Performance 5. Product 6. Org Updates 7. Series C Prep 8. Q2 Outlook",
                "intent": "document_creation",
            },
            {
                "query": "Explain the February burn overrun in plain English.",
                "response": "February burn $934K vs $848K plan (+10%). Causes: $48K ML hosting one-time, $34K misc unexplained. March normalized to $890K.",
                "intent": "financial_explanation",
            },
            {
                "query": "Add a calendar block for a 30-minute CFO candidate call next Thursday at 2pm.",
                "response": "Calendar event created: CFO Interview — Sandra Meir, Thursday April 4, 2:00-2:30pm.",
                "intent": "calendar_action",
            },
            {
                "query": "Summarize the Gong ForecastAI news and what it means for us.",
                "response": "Gong acquired ForecastAI for $180M — direct overlap with Vela Forecast. Our angle: mid-market focus vs Gong enterprise. Worth a board slide.",
                "intent": "market_intelligence",
            },
            {
                "query": "What is the status of the Ironstone renewal and what is blocking it?",
                "response": "Ironstone ($280K ARR, renews April 15) blocked by EU data residency requirement. Sam scoping. Jordan committed to answer by April 5.",
                "intent": "account_query",
            },
        ]

        interaction_ids = []
        for idx, data in enumerate(interactions_data):
            hour = 7 + idx // 2
            minute = (idx % 2) * 30
            interaction = SessionInteraction(
                ceo_id=ceo_id,
                query=data["query"],
                response=data["response"],
                intent=data.get("intent"),
                status="COMPLETED",
                timestamp=_iso_timestamp(0, hour=hour, minute=minute),
            )
            session.add(interaction)
            session.commit()
            session.refresh(interaction)
            interaction_ids.append(interaction.id)

        conv.interaction_ids = interaction_ids
        conv.updated_at = _iso_timestamp(0, hour=9, minute=45)
        session.add(conv)
        session.commit()


def wipe_ceo(ceo_id: str) -> None:
    """Delete all records for this ceo_id from every table that has one."""
    with Session(engine) as session:
        conversations = session.exec(
            select(AssistantConversation).where(AssistantConversation.ceo_id == ceo_id)
        ).all()
        for conv in conversations:
            for iid in (conv.interaction_ids or []):
                interaction = session.get(SessionInteraction, iid)
                if interaction:
                    session.delete(interaction)
            session.delete(conv)

        session.exec(delete(SessionInteraction).where(SessionInteraction.ceo_id == ceo_id))

        for model_cls in (
            User,
            CEOPreferences,
            CEOSituationalProfile,
            IncomingSignal,
            CEOMemory,
        ):
            session.exec(delete(model_cls).where(model_cls.ceo_id == ceo_id))

        session.exec(delete(CompanyState).where(CompanyState.company_name == COMPANY))
        session.exec(delete(CompanyIdentityProfile).where(CompanyIdentityProfile.company_name == COMPANY))

        session.commit()
    print(f"[seed_world] Wiped ceo_id={ceo_id}")


def seed_world(
    ceo_id: str = CEO_ID,
    username: str = USERNAME,
    password: str = "demo-password-2026",
) -> None:
    init_db()
    with Session(engine) as session:
        _seed_user(session, ceo_id, username, password)
        _seed_preferences(session, ceo_id)
        _seed_company_state(session, ceo_id)
        _seed_company_identity(session, ceo_id)
        _seed_situational_profile(session, ceo_id)
    _seed_signals(ceo_id)
    _seed_memories(ceo_id)
    _seed_interaction_history(ceo_id)
    build_seed_world_snapshot(ceo_id)
    print(f"[seed_world] Seeded ceo_id={ceo_id} company={COMPANY}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the Vela/Jordan Kessler world")
    parser.add_argument("--wipe", action="store_true", help="Wipe existing records before seeding")
    parser.add_argument("--ceo-id", default=CEO_ID)
    parser.add_argument("--username", default=USERNAME)
    parser.add_argument("--password", default="demo-password-2026")
    args = parser.parse_args()

    if args.wipe:
        init_db()
        wipe_ceo(args.ceo_id)

    seed_world(ceo_id=args.ceo_id, username=args.username, password=args.password)

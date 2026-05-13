from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannerSemanticSignals:
    planning: bool
    email: bool
    calendar: bool
    documents: bool
    watch: bool
    finance_analysis: bool
    strategic_analysis: bool
    action_plan: bool
    escalation: bool
    recommendation: bool

    def as_metadata(self) -> dict[str, bool]:
        return {
            "planning": self.planning,
            "email": self.email,
            "calendar": self.calendar,
            "documents": self.documents,
            "watch": self.watch,
            "finance_analysis": self.finance_analysis,
            "strategic_analysis": self.strategic_analysis,
            "action_plan": self.action_plan,
            "escalation": self.escalation,
            "recommendation": self.recommendation,
        }


def infer_planner_semantics(
    *,
    workflow: str,
    needs_email: bool,
    needs_calendar: bool,
    needs_documents: bool,
    time_horizon: str,
) -> PlannerSemanticSignals:
    workflow = str(workflow or "").strip()
    planning = workflow in {"schedule_planning", "morning_brief", "weekly_recap", "meeting_prep"}
    email = bool(needs_email)
    calendar = bool(needs_calendar)
    documents = bool(needs_documents)
    watch = workflow in {"morning_brief", "weekly_recap", "email_watcher", "calendar_briefing"} or (
        workflow == "schedule_planning" and time_horizon in {"today", "tomorrow", "this_week", "next_week", "week_after_next"}
    )

    return PlannerSemanticSignals(
        planning=planning,
        email=email,
        calendar=calendar,
        documents=documents,
        watch=watch,
        finance_analysis=workflow == "report_generation",
        strategic_analysis=workflow in {"report_generation", "meeting_prep", "weekly_recap"},
        action_plan=workflow in {"schedule_planning", "report_generation", "meeting_prep"},
        escalation=workflow == "report_generation",
        recommendation=workflow == "report_generation",
    )

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


FinancialTaskType = Literal[
    "runway_analysis",
    "cost_containment",
    "pricing_response",
    "renewal_contingency",
    "board_financial_packet",
    "variance_analysis",
    "scenario_model",
    "operating_review",
]


class FinancialMetricSlot(BaseModel):
    label: str
    current_value: Optional[str] = None
    comparison_value: Optional[str] = None
    delta: Optional[str] = None
    unit: Optional[str] = None


class FinancialActionSlot(BaseModel):
    owner: str
    action: str
    deadline: Optional[str] = None
    impact: Optional[str] = None
    risk: Optional[str] = None


class FinancialAnalysisTask(BaseModel):
    task_type: FinancialTaskType
    decision_goal: str
    analysis_scope: str
    time_horizon: Optional[str] = None
    entities: List[str] = Field(default_factory=list)
    metrics_requested: List[str] = Field(default_factory=list)
    deliverable_mode: str = "report"
    required_sections: List[str] = Field(default_factory=list)
    required_tables: List[str] = Field(default_factory=list)
    required_actions: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    confidence_gaps: List[str] = Field(default_factory=list)
    rationale: str = ""


class FinancialWorkspace(BaseModel):
    task_type: FinancialTaskType
    headline: str = ""
    core_question: str = ""
    base_case: str = ""
    downside_case: str = ""
    upside_case: str = ""
    key_metrics: List[FinancialMetricSlot] = Field(default_factory=list)
    owner_actions: List[FinancialActionSlot] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    evidence_map: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    recommendation: str = ""


class FinancialWorkspaceValidation(BaseModel):
    task_type: FinancialTaskType
    passed: bool = True
    missing_slots: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


FINANCIAL_TOPICS = {
    "cost_containment",
    "pricing_response",
    "renewal_contingency",
}


def build_financial_analysis_task(
    *,
    task_input: str,
    intent_state: Dict[str, Any] | None,
    unified_memory: Dict[str, Any] | None,
    finance_template: Optional[str] = None,
) -> FinancialAnalysisTask | None:
    lowered = task_input.lower()
    intent_state = intent_state or {}
    unified_memory = unified_memory or {}
    working = unified_memory.get("working_memory") if isinstance(unified_memory, dict) else {}
    session = unified_memory.get("session_memory") if isinstance(unified_memory, dict) else {}
    task_topic = str(intent_state.get("task_topic") or (working or {}).get("task_topic") or "")
    entities = _entities_from_memory(intent_state=intent_state, session_memory=session)
    time_horizon = (
        intent_state.get("deadline")
        or intent_state.get("timeframe")
        or (working or {}).get("deadline")
        or (working or {}).get("timeframe")
    )

    if _is_renewal_contingency_request(lowered, task_topic):
        return FinancialAnalysisTask(
            task_type="renewal_contingency",
            decision_goal="Assess downside impact if named renewals fail and define rescue actions this week.",
            analysis_scope="Named enterprise renewals, runway and revenue exposure, and immediate rescue plan.",
            time_horizon=time_horizon,
            entities=entities,
            metrics_requested=["arr_at_risk", "runway_impact", "rescue_timeline"],
            deliverable_mode="report",
            required_sections=["Runway Impact", "Rescue Actions This Week", "Fallback Plan"],
            required_tables=["at_risk_accounts", "owner_timeline"],
            required_actions=["calculate downside impact", "name rescue owners", "set this-week deadlines"],
            constraints=["Do not repeat already-approved cost cuts as the main answer."],
            rationale="The CEO is asking for downside revenue-risk math and a save-the-deals plan, not more cost containment detail.",
        )

    if task_topic == "pricing_response" or _is_pricing_response_request(lowered):
        return FinancialAnalysisTask(
            task_type="pricing_response",
            decision_goal="Translate pricing strategy into an execution package without triggering a broader price war.",
            analysis_scope="Competitive pricing response, guardrails, approvals, customer scripts, and success metrics.",
            time_horizon=time_horizon,
            entities=entities,
            metrics_requested=["margin_impact", "win_rate", "discount_band", "exception_rate"],
            deliverable_mode="execution_bundle" if _asks_for_execution(lowered) else "report",
            required_sections=["Approval Workflow", "Discount Guardrails", "Customer Script"],
            required_tables=["discount_thresholds", "regional_containment"],
            required_actions=["set approval path", "contain geography", "define success metrics"],
            constraints=["Keep the response tied to competitive pricing implementation, not generic finance cuts."],
            rationale="The CEO is asking how to operationalize a pricing response with containment guardrails.",
        )

    if task_topic == "cost_containment" or _is_cost_containment_request(lowered):
        return FinancialAnalysisTask(
            task_type="cost_containment",
            decision_goal="Translate cost savings targets into owner-ready actions and timing.",
            analysis_scope="Savings actions, owners, deadlines, and operating risks.",
            time_horizon=time_horizon,
            entities=entities,
            metrics_requested=["monthly_savings", "burn_rate", "runway_extension"],
            deliverable_mode="report",
            required_sections=["Cost Actions", "Owners & Timeline", "Operating Risks"],
            required_tables=["savings_by_action", "owner_deadlines"],
            required_actions=["name owners", "assign deadlines", "state risks"],
            constraints=[],
            rationale="The CEO is asking for concrete cost actions and ownership.",
        )

    if finance_template:
        return FinancialAnalysisTask(
            task_type="board_financial_packet",
            decision_goal="Prepare a board-usable finance narrative with the most important operating and financial signals.",
            analysis_scope="Board-facing summary, metrics, risks, and actions.",
            time_horizon=time_horizon,
            entities=entities,
            metrics_requested=["growth", "margin", "burn", "runway"],
            deliverable_mode="report",
            required_sections=["Key Finding", "Business Implications", "Recommended Actions"],
            required_tables=["board_metrics"],
            required_actions=["state decisions", "name owners"],
            constraints=[],
            rationale="This is a finance-oriented board or executive review request.",
        )

    if any(marker in lowered for marker in ("burn", "runway", "cash position", "cash on hand")):
        return FinancialAnalysisTask(
            task_type="runway_analysis",
            decision_goal="Explain cash, burn, and runway clearly enough to support a decision.",
            analysis_scope="Cash position, monthly burn, runway, and main drivers.",
            time_horizon=time_horizon,
            entities=entities,
            metrics_requested=["cash", "burn", "runway"],
            deliverable_mode="report",
            required_sections=["Runway Snapshot", "Drivers", "Actions"],
            required_tables=["burn_drivers"],
            required_actions=["name top variance drivers"],
            constraints=[],
            rationale="The request centers on runway or cash position.",
        )

    return None


def build_financial_workspace(
    *,
    task: FinancialAnalysisTask,
    payload: Any,
    unified_memory: Dict[str, Any] | None,
) -> FinancialWorkspace:
    unified_memory = unified_memory or {}
    text_parts: List[str] = []
    sources: List[str] = []
    assumptions: List[str] = []
    gaps: List[str] = []
    risks: List[str] = []
    actions: List[FinancialActionSlot] = []
    metrics = _extract_metrics_from_payload(payload)

    title = getattr(getattr(payload, "answer", None), "title", "") or ""
    summary = getattr(getattr(payload, "answer", None), "summary", "") or ""
    sections = getattr(getattr(payload, "answer", None), "sections", None) or []
    text_parts.extend([title, summary])

    for section in sections:
        label = getattr(section, "label", "") or ""
        items = list(getattr(section, "items", None) or [])
        if any(token in label.lower() for token in ("risk", "gap", "fallback")):
            risks.extend(items[:3])
        for item in items[:4]:
            text_parts.append(str(item))
            action = _extract_action_from_text(str(item))
            if action:
                actions.append(action)

    trust = getattr(payload, "trust", None)
    if trust is not None:
        assumptions = list(getattr(trust, "assumptions", None) or [])
        gaps = list(getattr(trust, "missing_context", None) or [])
    for source in list(getattr(payload, "sources", None) or [])[:5]:
        title = getattr(source, "title", "") or ""
        if title:
            sources.append(title)

    combined = "\n".join(text_parts).strip()
    return FinancialWorkspace(
        task_type=task.task_type,
        headline=getattr(getattr(payload, "answer", None), "title", "") or task.decision_goal,
        core_question=task.decision_goal,
        base_case=summary,
        downside_case=_infer_downside_case(task=task, combined_text=combined, user_message=(unified_memory.get("working_memory") or {}).get("last_user_message", "")),
        upside_case="",
        key_metrics=metrics,
        owner_actions=actions[:8],
        risks=risks[:6],
        assumptions=assumptions[:6],
        evidence_map=sources,
        gaps=gaps[:6],
        recommendation=summary,
    )


def validate_financial_workspace(
    *,
    task: FinancialAnalysisTask,
    workspace: FinancialWorkspace,
) -> FinancialWorkspaceValidation:
    text = "\n".join(
        [
            workspace.headline,
            workspace.core_question,
            workspace.base_case,
            workspace.downside_case,
            workspace.recommendation,
            *workspace.risks,
            *workspace.assumptions,
            *[f"{a.owner} {a.action} {a.deadline or ''} {a.impact or ''} {a.risk or ''}" for a in workspace.owner_actions],
            *[f"{m.label} {m.current_value or ''} {m.delta or ''}" for m in workspace.key_metrics],
        ]
    ).lower()
    missing: List[str] = []
    warnings: List[str] = []

    if task.task_type == "renewal_contingency":
        if "runway" not in text:
            missing.append("runway_impact")
        if not any(token in text for token in ("alphasystems", "redwood", "renewal", "arr at risk", "$1.22m", "1.22m")):
            missing.append("at_risk_entities")
        if not any(token in text for token in ("rescue", "save those deals", "this week", "owner", "within 24 hours", "deadline")):
            missing.append("this_week_rescue_actions")
        if any(token in text for token in ("s&m freeze", "cloud reduction", "hiring deferrals")) and not any(
            token in text for token in ("alphasystems", "redwood", "renewal", "rescue")
        ):
            warnings.append("Response appears dominated by cost-containment content rather than renewal contingency planning.")

    elif task.task_type == "pricing_response":
        if "discount" not in text and "pricing" not in text:
            missing.append("pricing_move")
        if not any(token in text for token in ("approval", "authority", "guardrail", "threshold")):
            missing.append("approval_guardrails")
        if not any(token in text for token in ("customer script", "talk track", "position as", "say:", "script")):
            missing.append("customer_script")
        if not any(token in text for token in ("margin", "win rate", "conversion", "success metrics")):
            missing.append("success_metrics")

    elif task.task_type == "cost_containment":
        if not any(token in text for token in ("cut", "savings", "reduction", "monthly")):
            missing.append("savings_actions")
        if "owner" not in text and not workspace.owner_actions:
            missing.append("owners")
        if not any(token in text for token in ("today", "tomorrow", "this week", "deadline", "by ")) and not any(a.deadline for a in workspace.owner_actions):
            missing.append("timing")

    elif task.task_type == "runway_analysis":
        if "runway" not in text:
            missing.append("runway")
        if "burn" not in text and "monthly burn" not in text:
            missing.append("burn")
        if "cash" not in text:
            missing.append("cash_position")

    return FinancialWorkspaceValidation(
        task_type=task.task_type,
        passed=not missing,
        missing_slots=missing,
        warnings=warnings,
    )


def financial_task_prompt_block(task: FinancialAnalysisTask | None) -> str:
    if task is None:
        return ""
    return (
        "=== FINANCIAL TASK CONTRACT ===\n"
        f"Task type: {task.task_type}\n"
        f"Decision goal: {task.decision_goal}\n"
        f"Scope: {task.analysis_scope}\n"
        f"Time horizon: {task.time_horizon or 'not specified'}\n"
        f"Entities: {', '.join(task.entities) if task.entities else 'none explicitly named'}\n"
        f"Metrics requested: {', '.join(task.metrics_requested) if task.metrics_requested else 'none'}\n"
        f"Required sections: {', '.join(task.required_sections) if task.required_sections else 'none'}\n"
        f"Required tables/workspace views: {', '.join(task.required_tables) if task.required_tables else 'none'}\n"
        f"Required actions: {', '.join(task.required_actions) if task.required_actions else 'none'}\n"
        f"Constraints: {', '.join(task.constraints) if task.constraints else 'none'}\n"
        f"Rationale: {task.rationale}\n"
        "Use this contract to keep the analysis semantically aligned. Do not drift into a different finance template just because the payload still contains finance keywords.\n\n"
    )


def _entities_from_memory(*, intent_state: Dict[str, Any], session_memory: Dict[str, Any] | None) -> List[str]:
    session_memory = session_memory or {}
    entities = [str(item) for item in (intent_state.get("entities") or []) if str(item)]
    previous_title = str(session_memory.get("previous_response_title") or "")
    if previous_title:
        entities.append(previous_title)
    return list(dict.fromkeys(entities))[:8]


def _extract_metrics_from_payload(payload: Any) -> List[FinancialMetricSlot]:
    metrics: List[FinancialMetricSlot] = []
    text = "\n".join(
        [
            getattr(getattr(payload, "answer", None), "summary", "") or "",
            *[
                str(item)
                for section in (getattr(getattr(payload, "answer", None), "sections", None) or [])
                for item in (getattr(section, "items", None) or [])
            ],
        ]
    )
    for match in re.finditer(r"(\$[\d.,]+[MKmk]?|\d+(?:\.\d+)?%)", text):
        value = match.group(1)
        window = text[max(0, match.start() - 40): match.end() + 40].strip()
        label = window[:80] if window else "metric"
        metrics.append(FinancialMetricSlot(label=label, current_value=value))
        if len(metrics) >= 8:
            break
    return metrics


def _extract_action_from_text(text: str) -> FinancialActionSlot | None:
    owner_match = re.search(r"([A-Z][a-z]+(?: [A-Z][a-z]+)?(?: \+ [A-Z][a-z]+(?: [A-Z][a-z]+)?)?)\s+\|", text)
    deadline_match = re.search(r"\b(today|tomorrow|this week|within \d+ hours?|by [A-Za-z0-9 ,]+|end of day)\b", text, re.I)
    if not owner_match and "owner:" not in text.lower():
        return None
    owner = owner_match.group(1) if owner_match else "Owner"
    action = text.split("|", 1)[1].strip() if "|" in text else text.strip()
    return FinancialActionSlot(owner=owner, action=action[:220], deadline=deadline_match.group(1) if deadline_match else None)


def _infer_downside_case(*, task: FinancialAnalysisTask, combined_text: str, user_message: str) -> str:
    if task.task_type != "renewal_contingency":
        return ""
    lowered = f"{combined_text}\n{user_message}".lower()
    if "lose both" in lowered or "if we lose both" in lowered:
        return "Downside case centers on losing both named renewals and recalculating the near-term runway and revenue exposure."
    return "Downside case should quantify the runway and revenue impact if the named renewals fail."


def _asks_for_execution(lowered: str) -> bool:
    return any(
        marker in lowered
        for marker in ("execution package", "implement", "approval workflow", "customer scripts", "success metrics", "guardrails")
    )


def _is_cost_containment_request(lowered: str) -> bool:
    return any(marker in lowered for marker in ("cost containment", "monthly cuts", "s&m cut", "cloud reduction", "hiring deferrals"))


def _is_pricing_response_request(lowered: str) -> bool:
    return any(marker in lowered for marker in ("pricing strategy", "competitive response", "alphasystems", "price war", "dach"))


def _is_renewal_contingency_request(lowered: str, task_topic: str) -> bool:
    if task_topic == "renewal_contingency":
        return True
    return (
        any(marker in lowered for marker in ("contingency", "if we lose both", "lose both renewals", "at risk with alphasystems and redwood", "arr at risk"))
        and any(marker in lowered for marker in ("runway", "rescue", "renewal", "this week"))
    )

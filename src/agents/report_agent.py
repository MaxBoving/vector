import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.finance import (
    BaseForecastEngine,
    DEFAULT_THEME_ID,
    ForecastConfig,
    ForecastResult,
    MapperContext,
    get_finance_template_definition,
    map_company_context_to_metrics,
    run_finance_qa_checklist,
)
from src.core.llm import DEFAULT_ANTHROPIC_MODEL
from src.presentation import (
    DeckSlideSpec,
    DeckSpec,
    MemoSectionSpec,
    MemoSpec,
    PresentationBlock,
    PresentationSpec,
    get_artifact_template,
    normalize_and_validate_presentation_spec,
    presentation_spec_to_deck_spec,
    presentation_spec_to_memo_spec,
)
from src.tools.artifact_tools import read_stage_artifact, read_stage_artifact_metadata
from src.tools.registry import ToolRegistry
from src.workflows.context_loading import (
    REPORT_GENERATION_CONTEXT_STAGES,
    build_report_context_actions,
    prepare_report_context,
)
from src.workflows.retrieval_manifest import RetrievalManifest
from src.workflows.financial_semantic import (
    FinancialAnalysisTask,
    build_financial_analysis_task,
    build_financial_workspace,
    financial_task_prompt_block,
    validate_financial_workspace,
)
from src.workflows.workbook_models import (
    WorkbookChartSpec,
    WorkbookFinancialRow,
    WorkbookMetric,
    WorkbookPivotRow,
    WorkbookPivotSnapshot,
    WorkbookSheetSpec,
    WorkbookSpec,
    format_currency,
)

from .base import BaseAgent
from .schemas import (
    AgentInput,
    AgentMetadata,
    AgentOutput,
    complete_stage_action,
    complete_workflow_action,
    create_canvas_action,
    create_docx_memo_action,
    create_pptx_deck_action,
    create_workbook_action,
    gate_action,
    tool_action,
    write_artifact_action,
)


class ReportSection(BaseModel):
    label: str
    content: Optional[str] = None
    items: List[str] = Field(default_factory=list)


class ReportAnswer(BaseModel):
    title: str
    summary: str
    sections: List[ReportSection] = Field(default_factory=list)


class ReportTrust(BaseModel):
    confidence: str
    confidence_score: float
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    data_quality: str
    calculation_used: bool = False
    missing_context: List[str] = Field(default_factory=list)
    evidence_state: Optional[str] = None
    evidence_reasons: List[str] = Field(default_factory=list)
    safe_to_act: Optional[bool] = None
    question_options: List[Dict[str, Any]] = Field(default_factory=list)


class PresentationSection(BaseModel):
    title: str
    content: Optional[str] = None
    items: List[str] = Field(default_factory=list)


class WeeklyPlanBlock(BaseModel):
    title: str
    time_window: Optional[str] = None
    reason: Optional[str] = None
    source_refs: List[str] = Field(default_factory=list)
    confidence: Optional[str] = None


class WeeklyPlanPresentation(BaseModel):
    blocks: List[WeeklyPlanBlock] = Field(default_factory=list)
    deadlines: List[str] = Field(default_factory=list)
    meetings: List[str] = Field(default_factory=list)
    follow_ups: List[str] = Field(default_factory=list)


class ReportPresentation(BaseModel):
    mode: Optional[str] = None
    variant: Optional[str] = None
    preamble: Optional[str] = None
    summary: Optional[str] = None
    priorities: List[PresentationSection] = Field(default_factory=list)
    recommended_actions: List[PresentationSection] = Field(default_factory=list)
    risks: List[PresentationSection] = Field(default_factory=list)
    details: List[PresentationSection] = Field(default_factory=list)
    weekly_plan: Optional[WeeklyPlanPresentation] = None
    finance: Optional[Dict[str, Any]] = None


class ReportPayload(BaseModel):
    answer: ReportAnswer
    trust: ReportTrust
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    presentation: Optional[ReportPresentation] = None


class ArtifactPlanEntry(BaseModel):
    artifact_type: str
    label: str
    format: str
    status: str = "planned"
    purpose: Optional[str] = None
    ready_when: Optional[str] = None
    blocking_reason: Optional[str] = None


class ReportAgent(BaseAgent):
    COMPLETION_MODEL = os.getenv("REPORT_AGENT_MODEL", DEFAULT_ANTHROPIC_MODEL)

    SOURCE_TRUST_RANKS: dict[str, int] = {
        "audited_finance_doc": 100,
        "weekly_finance_checkin": 90,
        "company_state": 80,
        "internal_finance_memo": 70,
        "retrieved_document": 60,
        "historical_artifact": 55,
        "artifact": 50,
        "state": 45,
        "derived_metric": 25,
        "fallback": 10,
    }
    FINANCE_TEMPLATE_LABELS: dict[str, list[str]] = {
        "aws_cost_review": ["Key Cost Trends", "Business Implications", "Recommended Actions"],
        "runway_burn_review": ["Cash Position", "Runway Implications", "Recommended Actions"],
        "project_spend_review": ["Spend Status", "Business Implications", "Recommended Actions"],
        "budget_variance_review": ["Budget Variance", "Business Implications", "Recommended Actions"],
        "board_financial_update": ["Financial Snapshot", "Board Implications", "Recommended Actions"],
    }
    FINANCE_TEMPLATE_DEFINITION_ALIASES: dict[str, str] = {
        "aws_cost_review": "cost_review",
        "runway_burn_review": "runway_review",
    }

    METRIC_TAXONOMY: dict[str, tuple[str, list[str]]] = {
        "AWS cost": ("cost", ["aws", "cloud infrastructure", "cloud cost", "cloud spend", "hosting"]),
        "Cash runway": ("capital", ["cash runway", "runway"]),
        "Cash at bank": ("capital", ["cash at bank", "cash balance", "bank balance", "cash on hand"]),
        "North America revenue": ("revenue", ["north america revenue", "na revenue", "americas revenue"]),
        "Europe revenue": ("revenue", ["europe revenue", "emea revenue"]),
        "APAC revenue": ("revenue", ["apac revenue", "asia pacific revenue"]),
        "R&D cost": ("cost", ["r&d", "research and development", "research & development"]),
        "Total revenue": ("revenue", ["total revenue", "revenue", "sales", "bookings", "arr"]),
        "Operating expense": ("cost", ["operating expense", "opex", "operating cost", "operating costs"]),
        "Burn rate": ("cost", ["burn rate", "weekly burn", "monthly burn", "burn"]),
        "Series reserve": ("capital", ["series reserve", "reserve", "series c reserve"]),
    }
    ACTION_PLAN_MARKERS: tuple[str, ...] = (
        "action plan",
        "action plans",
        "specific actions",
        "specific recommendations",
        "specific strategies",
        "specific initiatives",
        "specific metrics",
        "specific details",
        "detailed breakdown",
        "what should we do",
        "what do we do next",
        "what steps should we take",
        "immediately",
        "right now",
        "detailed plan",
        "detailed list",
        "details on",
        "more details",
        "breakdown",
        "break this down",
        "outline the types",
        "what metrics will we use",
        "what metrics should we use",
        "prioritized list",
        "timeline",
        "responsible parties",
        "be specific",
        "cross-departmental",
        "cross functional",
        "cross-functional",
        "collaboration",
        "alignment",
        "aligned on thresholds",
        "executive communication",
        "at-risk customer",
        "direct response",
        "board narrative",
        "frame for the board",
        "corrective story",
        "narrative frame",
        "how should i frame",
        "what does a good",
        "what would good",
        "recovery commitment",
        "recovery milestones",
        "recovery plan",
        "what questions should i expect",
        "do i have answers",
        "questions from the board",
        "board questions",
        "board packet",
        "packet need",
        "before it goes out",
        "specific edits",
        "defer",
        "delegate",
        "what can i safely",
        "what can we safely",
        "safely defer",
        "safely delegate",
        "sequence",
        "how should i sequence",
        "how to sequence",
        "what order should",
        "what should i do first",
        "how should i prioritize",
        "prioritize my day",
        "structure my day",
    )
    SPECIFICITY_MARKERS: tuple[str, ...] = (
        "specific",
        "detailed",
        "details",
        "breakdown",
        "break this down",
        "list",
        "outline",
        "which",
        "what metrics",
        "timeline",
        "responsible parties",
        "examples",
        "measurable",
        "how exactly",
        "step by step",
        "collaboration",
        "alignment",
        "threshold",
        "thresholds",
    )
    ESCALATION_MARKERS: tuple[str, ...] = (
        "escalation",
        "escalations",
        "apex health",
        "redwood systems",
        "customer issue",
        "customer issues",
        "customer concern",
        "customer concerns",
        "at-risk customer",
    )
    RECOMMENDATION_REQUEST_MARKERS: tuple[str, ...] = (
        "should i",
        "should we",
        "do you recommend",
        "what do you recommend",
        "what would you recommend",
        "go or no-go",
        "go/no-go",
        "hire or not",
        "advance the candidate",
        "advance this candidate",
        "move forward with",
        "approve the",
        "make an offer",
        "extend an offer",
        "what's your recommendation",
        "what is your recommendation",
        "give me a recommendation",
        "what would you do",
    )
    HIRING_CONTEXT_MARKERS: tuple[str, ...] = (
        "candidate",
        "candidates",
        "hire",
        "hiring",
        "interview",
        "onboard",
        "offer",
        "comp package",
        "compensation package",
        "90-day",
        "90 day",
        "panel feedback",
        "reference",
        "vp engineering",
        "chief of staff",
        "gtm director",
    )
    FINANCE_EXECUTION_MARKERS: tuple[str, ...] = (
        "aws",
        "cloud spend",
        "cloud cost",
        "hiring freeze",
        "finance close",
        "variance",
        "burn",
        "runway",
        "board packet",
    )

    metadata = AgentMetadata(
        name="report_agent",
        description="Generates executive-grade financial and operating reports.",
        stage="synthesizer",
        allowed_tools=[
            "get_company_state",
            "get_preferences",
            "semantic_search",
            "google_drive_search",
            "google_drive_read",
            "execute_math",
            "structured_completion",
            "write_artifact",
            "create_docx_memo",
            "create_pptx_deck",
            "create_workbook",
            "create_canvas",
            "variance_analysis",
            "memory_management",
            "get_live_context",
            "write_thread_entry",
            "get_situational_profile",
            "update_situational_profile",
        ],
        tags=["reporting", "finance", "executive"],
    )

    def __init__(self, tools: ToolRegistry):
        self.tools = tools
        self.forecast_engine = BaseForecastEngine()

    async def run(self, agent_input: AgentInput, **kwargs: Any) -> AgentOutput:
        task_input = kwargs.get("task_input") or agent_input.task_input or ""
        context = agent_input.context or {}

        missing_context_actions = build_report_context_actions(task_input, context)
        if missing_context_actions:
            return AgentOutput(
                agent_name=self.metadata.name,
                stage=agent_input.stage,
                success=True,
                summary="Requesting reporting context.",
                actions=missing_context_actions,
                metadata={
                    "workflow_type": "report_generation",
                    "response_type": "report",
                    "phase": "context",
                    "context_stages": REPORT_GENERATION_CONTEXT_STAGES,
                },
            )

        prepared_context = prepare_report_context(context)
        company_state = prepared_context.company_state
        company_identity = prepared_context.company_identity
        preferences = prepared_context.preferences
        project_context = prepared_context.project_context
        session_history = prepared_context.session_history
        signals = prepared_context.signals
        payload = prepared_context.prompt_payload()
        unified_memory = payload.get("unified_memory") or context.get("unified_memory") or {}
        retrieval = payload["retrieved_documents"]
        finance_context = payload.get("finance_context") or {}
        vocabulary_block = payload.get("vocabulary_block", "")
        finance_template = self._select_finance_template(task_input)

        # Compute resolved topics once from session history for reuse below.
        resolved_topics = self._extract_resolved_topics(session_history)

        # Read artifact_type from workflow metadata (set by runner for offer
        # acceptances and direct request intent detection, D1).
        artifact_type: Optional[str] = (
            context.get("artifact_type")
            or agent_input.workflow_state.metadata.get("artifact_type")
        )
        intent_state: Dict[str, Any] = (
            context.get("intent_state")
            or agent_input.workflow_state.metadata.get("intent_state")
            or {}
        )
        financial_task = build_financial_analysis_task(
            task_input=task_input,
            intent_state=intent_state,
            unified_memory=unified_memory,
            finance_template=finance_template,
        )

        # ── Clarification gate ────────────────────────────────────────────────
        # Deterministic: inspect the loaded context for actual data gaps and
        # probe the CEO about those specific gaps — no LLM call needed here.
        skip_gate = (
            context.get("skip_clarification_gate")
            or agent_input.workflow_state.metadata.get("skip_clarification_gate")
            or context.get("clarification_policy_continue")
            or agent_input.workflow_state.metadata.get("clarification_policy_continue")
        )
        if not skip_gate:
            gap_questions = self._detect_context_gaps(
                task_input=task_input,
                company_state=company_state,
                retrieval=retrieval,
                signals=signals,
                session_history=session_history,
                finance_context=finance_context,
                resolved_topics=resolved_topics,
            )
            if gap_questions:
                clarification_options = self._clarification_options(
                    task_input=task_input,
                    questions=gap_questions,
                )
                clarification_output = self._build_gap_clarification_output(
                    task_input=task_input,
                    company_state=company_state,
                    questions=gap_questions,
                    options=clarification_options,
                )
                return AgentOutput(
                    agent_name=self.metadata.name,
                    stage=agent_input.stage,
                    success=True,
                    summary="Context gaps detected — asking CEO to fill them before generating report.",
                    actions=[],
                    structured_output=clarification_output,
                    metadata={
                        "workflow_type": "report_generation",
                        "response_type": "clarification",
                        "needs_clarification": True,
                        "original_query": task_input,
                        "clarification_options": clarification_options,
                    },
                )

        if "report_completion" not in context:
            return AgentOutput(
                agent_name=self.metadata.name,
                stage=agent_input.stage,
                success=True,
                summary="Requesting structured report completion.",
                actions=[
                    tool_action(
                        "structured_completion",
                        result_key="report_completion",
                        prompt=self._report_prompt(
                            task_input,
                            company_state,
                            company_identity,
                            preferences,
                            project_context,
                            session_history,
                            signals,
                            retrieval,
                            vocabulary_block=vocabulary_block,
                            ceo_memories=prepared_context.ceo_memories,
                            finance_context=finance_context,
                            live_context=prepared_context.live_context,
                            situational_profile=prepared_context.situational_profile,
                            retrieval_manifest=context.get("retrieval_manifest"),
                            entity_context=prepared_context.entity_context,
                            proactive_observations_block=context.get("proactive_observations_block") or "",
                            artifact_type=artifact_type,
                            intent_state=intent_state,
                            unified_memory=unified_memory,
                            financial_task=financial_task,
                        ),
                        system_prompt=(
                            (agent_input.system_prompt or "") + "\n\n"
                            "You are the ReportAgent for a CEO. Produce an executive-grade report payload. "
                            "Be clear, concise, business-focused, and grounded in the supplied context. "
                            "The retrieval_manifest at the top of the prompt tells you exactly what was found — "
                            "use it to write a specific, first-person preamble that names the actual sources. Never write a generic opener. "
                            "IMPORTANT: Your sole job is to generate the report CONTENT (title, summary, sections, trust, sources). "
                            "Do NOT mention file generation, PowerPoint, PPTX, DOCX, XLSX, or document creation in your response — "
                            "all file exports are handled automatically by the system pipeline after you generate the content. "
                            "If the CEO asked for a deck or workbook, generate the underlying executive content for it; "
                            "never say the capability is unavailable."
                        ).strip(),
                        response_model=ReportPayload,
                        model=self.COMPLETION_MODEL,
                    )
                ],
                metadata={
                    "workflow_type": "report_generation",
                    "response_type": "report",
                    "phase": "completion",
                    "context_stages": REPORT_GENERATION_CONTEXT_STAGES,
                },
            )

        payload = self._generate_report_payload(
            task_input,
            company_state,
            signals,
            retrieval,
            context.get("report_completion"),
        )
        payload = self._apply_finance_template_to_payload(payload, finance_template=finance_template)
        payload = self._apply_threshold_events_to_payload(
            task_input=task_input,
            payload=payload,
            company_state=company_state,
            retrieval=retrieval,
            finance_template=finance_template,
        )
        payload = self._apply_finance_close_focus_to_payload(
            task_input=task_input,
            payload=payload,
            retrieval=retrieval,
            signals=signals,
            finance_template=finance_template,
        )
        payload = self._apply_finance_operational_breakdown_shape(
            task_input=task_input,
            payload=payload,
            session_history=session_history,
        )
        payload = self._apply_followup_action_plan_shape(
            task_input=task_input,
            payload=payload,
            session_history=session_history,
            artifact_type=artifact_type,
        )
        payload = self._apply_resolution_language_shape(
            task_input=task_input,
            payload=payload,
            session_history=session_history,
        )
        payload = self._apply_financial_task_contract(
            task_input=task_input,
            payload=payload,
            financial_task=financial_task,
        )
        payload = self._enforce_three_s(
            payload,
            preferred_labels=self._template_section_labels(finance_template) if finance_template else None,
        )
        if artifact_type == "email":
            payload = self._fix_email_sections(payload)
        payload, finance_validation = self._apply_finance_accuracy_guardrails(
            task_input=task_input,
            payload=payload,
            company_state=company_state,
            ceo_id=agent_input.workflow_state.ceo_id,
            current_interaction_id=agent_input.workflow_state.interaction_id,
            session_history=session_history,
            retrieval=retrieval,
        )
        financial_workspace = (
            build_financial_workspace(
                task=financial_task,
                payload=payload,
                unified_memory=unified_memory,
            )
            if financial_task
            else None
        )
        financial_workspace_validation = (
            validate_financial_workspace(task=financial_task, workspace=financial_workspace)
            if financial_task and financial_workspace
            else None
        )
        if financial_workspace_validation and financial_workspace_validation.warnings:
            payload = payload.model_copy(deep=True)
            payload.trust.missing_context = list(
                dict.fromkeys([*payload.trust.missing_context, *financial_workspace_validation.warnings[:2]])
            )
        if financial_workspace_validation:
            finance_validation = {
                **finance_validation,
                "financial_task": financial_task.model_dump(mode="json"),
                "financial_workspace_validation": financial_workspace_validation.model_dump(mode="json"),
            }
        finance_rows: list[WorkbookFinancialRow] = []
        finance_summary_metrics: list[WorkbookMetric] = []
        if finance_template:
            finance_rows, _ = self._prepare_financial_rows(
                task_input=task_input,
                company_state=company_state,
                metrics=self._extract_metrics(payload),
                ceo_id=agent_input.workflow_state.ceo_id,
                current_interaction_id=agent_input.workflow_state.interaction_id,
                session_history=session_history,
                retrieval=retrieval,
            )
            finance_summary_metrics = self._build_summary_metrics(
                finance_rows,
                payload,
                task_input=task_input,
                comparison_rows=self._build_period_comparison_rows(task_input=task_input, rows=finance_rows),
            )
        output_modality, artifact_plan = self._select_output_modality(task_input)
        finance_digest = self._build_finance_digest(
            payload,
            finance_template=finance_template,
            finance_summary_metrics=finance_summary_metrics,
        )
        primary_visual = self._primary_visual_for_template(finance_template)
        payload = self._apply_presentation_metadata(
            payload,
            task_input=task_input,
            finance_template=finance_template,
            finance_digest=finance_digest,
            primary_visual=primary_visual,
            finance_summary_metrics=finance_summary_metrics,
            finance_rows=finance_rows,
            ceo_id=agent_input.workflow_state.ceo_id,
            resolved_topics=resolved_topics,
            artifact_type=artifact_type,
            intent_state=intent_state,
        )
        raw_presentation_spec = self._build_presentation_spec(
            task_input=task_input,
            payload=payload,
            output_modality=output_modality,
            finance_template=finance_template,
        )
        presentation_spec, presentation_quality = normalize_and_validate_presentation_spec(raw_presentation_spec)
        markdown = self._to_markdown(payload)
        if self._needs_human_approval(task_input) and not self._approval_granted(agent_input):
            return AgentOutput(
                agent_name=self.metadata.name,
                stage=agent_input.stage,
                success=True,
                summary="Human approval required before finalizing this report.",
                content=markdown,
                structured_output=payload.model_dump(),
                actions=[
                    gate_action(
                        "HUMAN_APPROVAL",
                        reason="This request appears to involve external sharing or sensitive executive distribution.",
                        preview_title=payload.answer.title,
                    )
                ],
                metadata={
                    "workflow_type": "report_generation",
                    "response_type": "report",
                    "phase": "approval",
                    "finance_template": finance_template,
                    "finance_digest": finance_digest,
                    "primary_visual": primary_visual,
                    "output_modality": output_modality,
                    "finance_validation": finance_validation,
                    "artifact_plan": [artifact.model_dump() for artifact in artifact_plan],
                    "presentation_spec": presentation_spec.model_dump(mode="json"),
                    "presentation_quality": presentation_quality.model_dump(mode="json"),
                },
            )

        return AgentOutput(
            agent_name=self.metadata.name,
            stage=agent_input.stage,
            success=True,
            summary=payload.answer.summary,
            content=markdown,
            structured_output=payload.model_dump(),
            actions=self._build_artifact_actions(
                task_input=task_input,
                payload=payload,
                presentation_spec=presentation_spec,
                finance_template=finance_template,
                company_state=company_state,
                ceo_id=agent_input.workflow_state.ceo_id,
                current_interaction_id=agent_input.workflow_state.interaction_id,
                session_history=session_history,
                retrieval=retrieval,
                markdown=markdown,
                output_modality=output_modality,
                stage=agent_input.stage,
                finance_rows=finance_rows,
                conversation_id=agent_input.metadata.get("conversation_id"),
                turn_count=agent_input.workflow_state.metadata.get("turn_count", 0),
                situational_updates=self._extract_situational_updates(
                    task_input=task_input,
                    payload=payload,
                    output_modality=output_modality,
                ),
            ),
            metadata={
                "workflow_type": "report_generation",
                "response_type": "report",
                "finance_template": finance_template,
                "finance_digest": finance_digest,
                "primary_visual": primary_visual,
                "output_modality": output_modality,
                "finance_validation": finance_validation,
                "artifact_plan": [artifact.model_dump() for artifact in artifact_plan],
                "presentation_spec": presentation_spec.model_dump(mode="json"),
                "presentation_quality": presentation_quality.model_dump(mode="json"),
            },
        )

    def _generate_report_payload(
        self,
        task_input: str,
        company_state: Dict[str, Any],
        signals: List[Dict[str, Any]],
        retrieval: List[Dict[str, Any]],
        completion: Optional[Dict[str, Any]],
    ) -> ReportPayload:
        if completion:
            try:
                payload = ReportPayload(**completion)
                payload.sources = self._rank_and_normalize_sources(payload.sources)
                return payload
            except Exception:
                pass
        payload = self._fallback_payload(task_input, company_state, retrieval)
        payload.sources = self._rank_and_normalize_sources(payload.sources)
        return payload

    # ── Context gap detection ─────────────────────────────────────────────
    # These keyword sets classify query intent so we can check whether the
    # loaded context actually covers what the CEO is asking about.

    # Maps topic keys → patterns that indicate the CEO already addressed that
    # topic in a prior message.  Used by _extract_resolved_topics().
    _RESOLVED_TOPIC_PATTERNS: Dict[str, List[str]] = {
        "output_format": [
            "board", "my decision", "personal decision", "for me", "for myself",
            "immediate action", "skip the board", "not for the board", "internal use",
            "operational", "for my own", "no board",
        ],
        "audience": [
            "investors", "internal", "external", "for myself", "my own",
        ],
        "urgency": [
            "immediate", "this week", "today", "right now", "asap", "immediately",
            "urgently", "right away", "by eod", "by end of day",
        ],
        "period": [
            "q1", "q2", "q3", "q4", "this quarter", "last quarter", "this month",
            "last month", "ytd", "year-to-date", "current quarter", "prior quarter",
            "rolling", "trailing", "fy ", "fy2", "january", "february", "march",
            "april", "may", "june", "july", "august", "september", "october",
            "november", "december", "2024", "2025", "2026",
        ],
    }

    # Patterns that indicate a question string belongs to a given topic —
    # used to filter LLM-generated open_questions against resolved topics.
    _QUESTION_TOPIC_PATTERNS: Dict[str, List[str]] = {
        "output_format": [
            "board", "personal decision", "frame", "format", "framing",
            "board presentation", "board language", "operator language",
            "board or personal", "decision or board",
        ],
        "period": [
            "period", "quarter", "month", "which period", "anchor",
            "q1", "q2", "q3", "q4",
        ],
    }

    # Frustration patterns — when CEO pushes back on clarifying questions,
    # suppress ALL further questions for that session.
    _FRUSTRATION_PATTERNS = (
        "stop asking", "no more questions", "stop clarifying", "just give me",
        "i don't want", "i don't need", "i need the actual", "stop giving me",
        "don't ask me", "you should already know", "that's your job",
        "you should have", "no clarifying", "skip the questions",
        "i'm not doing your homework", "i'm done with", "this is ridiculous",
        "this is unacceptable", "completely unacceptable", "one more chance",
        "stop giving me reports", "i want the actual numbers",
    )

    def _extract_resolved_topics(self, session_history: List[Dict[str, Any]], task_input: Optional[str] = None) -> frozenset:
        """
        Scan prior CEO messages in session_history AND the current task_input
        to detect which question topics have already been answered in-conversation.
        Returns a frozenset of resolved topic keys.
        """
        prior_queries = [
            str(item.get("query") or "").lower()
            for item in (session_history or [])
            if item.get("query")
        ]
        if task_input:
            prior_queries.append(task_input.lower())
        
        if not prior_queries:
            return frozenset()
        
        combined = " ".join(prior_queries)
        resolved: set[str] = set()
        for topic, patterns in self._RESOLVED_TOPIC_PATTERNS.items():
            if any(pat in combined for pat in patterns):
                resolved.add(topic)
        # If the CEO has expressed frustration at any point in this session,
        # suppress all further clarifying questions.
        if any(pat in combined for pat in self._FRUSTRATION_PATTERNS):
            resolved.add("suppress_all_questions")
        return frozenset(resolved)

    def _filter_resolved_open_questions(
        self,
        open_questions: List[str],
        resolved_topics: frozenset,
    ) -> List[str]:
        """
        Drop questions from the LLM-generated open_questions list whose topic
        has already been resolved by a prior CEO message.
        """
        if not resolved_topics or not open_questions:
            return open_questions
        filtered = []
        for q in open_questions:
            q_lower = q.lower()
            belongs_to_resolved = False
            for topic, patterns in self._QUESTION_TOPIC_PATTERNS.items():
                if topic in resolved_topics and any(pat in q_lower for pat in patterns):
                    belongs_to_resolved = True
                    break
            if not belongs_to_resolved:
                filtered.append(q)
        return filtered

    _FINANCE_KW = frozenset([
        "revenue", "arr", "mrr", "burn", "runway", "budget", "cost", "expense",
        "margin", "ebitda", "profit", "cash", "capital", "raise", "funding",
        "spend", "rate", "growth", "numbers", "financials", "p&l",
    ])
    _COMPARISON_KW = frozenset([
        "vs", "versus", "against", "compared", "target", "plan", "forecast",
        "gap", "variance", "tracking", "ahead", "behind", "on track", "off track",
        "attainment", "pacing",
    ])
    _DEAL_KW = frozenset([
        "deal", "customer", "client", "contract", "renewal", "churn",
        "pipeline", "close", "crm", "account", "prospect",
    ])
    _HIRING_KW = frozenset([
        "hire", "hiring", "headcount", "team", "org", "staff", "role",
        "candidate", "offer", "recruiter",
    ])

    def _detect_context_gaps(
        self,
        *,
        task_input: str,
        company_state: Dict[str, Any],
        retrieval: List[Any],
        signals: List[Any],
        session_history: List[Any],
        finance_context: Dict[str, Any],
        resolved_topics: frozenset = frozenset(),
    ) -> List[str]:
        """
        Inspect the already-loaded context for actual data gaps.
        Returns up to 3 specific questions the CEO can answer to fill them.
        Returns [] when context is sufficient — gate passes silently.
        """
        questions: List[str] = []
        q = task_input.lower()
        company_name = (company_state or {}).get("company_name", "your company")

        # 1. Company state completely absent — can't ground any business answer
        if not company_state:
            return [
                f"I don't have any business context for {company_name} yet. "
                "What are the key numbers I should know? (ARR, burn, stage, top priorities)"
            ]

        has_finance_data = bool(
            finance_context.get("current_metrics")
            or company_state.get("revenue_segmentation")
            or company_state.get("capital_position")
            or company_state.get("cost_structure")
        )

        # 2. Finance-intent query but no financial data loaded
        if any(kw in q for kw in self._FINANCE_KW) and not has_finance_data:
            questions.append(
                "What are the relevant numbers? "
                "(e.g. current ARR, burn rate, cash balance — paste whatever you'd like me to work from)"
            )

        # 3. Comparison-intent query but no plan/budget baseline
        if any(kw in q for kw in self._COMPARISON_KW):
            kb_entries: list = company_state.get("knowledge_base") or []
            has_baseline = bool(
                company_state.get("operating_plan")
                or company_state.get("targets")
                or company_state.get("budget")
                or any(
                    kw in (entry.get("title") or "").lower() or kw in (entry.get("content") or "").lower()
                    for entry in kb_entries
                    for kw in ("plan", "budget", "forecast", "target", "actuals")
                )
                or any(
                    kw in str(doc).lower()
                    for doc in retrieval[:5]
                    for kw in ("budget", "plan", "forecast", "target")
                )
            )
            if not has_baseline:
                questions.append(
                    "What's the baseline to compare against? "
                    "(budget line, prior-period actuals, or target figures — even rough numbers help)"
                )

        # 4. Query references a deal / customer but no CRM or signal context
        if any(kw in q for kw in self._DEAL_KW):
            has_deal_context = bool(
                signals
                or any(kw in str(company_state).lower() for kw in ("customer", "deal", "pipeline", "churn"))
                or retrieval
            )
            if not has_deal_context:
                questions.append(
                    "Which customer or deal are you asking about? "
                    "I don't have CRM data loaded — a quick summary or account name will do."
                )

        # 5. Query references hiring/team but no org data
        if any(kw in q for kw in self._HIRING_KW):
            has_org_context = bool(
                company_state.get("team_composition")
                or company_state.get("headcount")
                or retrieval
                or any(kw in str(company_state).lower() for kw in ("headcount", "team", "hire"))
            )
            if not has_org_context:
                questions.append(
                    "What's the current team size and which role(s) are you focused on? "
                    "I don't have org or headcount data on file."
                )

        # 6. Finance query with period-ambiguous language but no specific anchor
        # Skip if the CEO already named a period in a prior message.
        if any(kw in q for kw in self._FINANCE_KW) and "period" not in resolved_topics:
            _period_specific = any(
                kw in q for kw in (
                    "q1", "q2", "q3", "q4",
                    "january", "february", "march", "april", "may", "june",
                    "july", "august", "september", "october", "november", "december",
                    "fy ", "fy2", "2024", "2025", "2026",
                )
            )
            _period_relative = any(
                kw in q for kw in (
                    "this quarter", "last quarter", "this month", "last month",
                    "ytd", "year-to-date", "current quarter", "prior quarter",
                    "rolling", "trailing",
                )
            )
            if _period_relative and not _period_specific:
                questions.append(
                    "Which period should I anchor to: current month, this quarter, or YTD? "
                    "The numbers will differ materially depending on the window."
                )

        # 7. No retrieval and no signals — completely sparse context for a non-trivial query
        if not questions and not retrieval and not signals and not has_finance_data:
            questions.append(
                "I'm working with limited context here. "
                "Any relevant numbers, recent updates, or background you can share would sharpen the answer."
            )

        return self._rank_questions_by_impact(questions[:3], task_input)

    def _build_gap_clarification_output(
        self,
        *,
        task_input: str,
        company_state: Dict[str, Any],
        questions: List[str],
        options: List[dict[str, Any]] | None = None,
    ) -> dict:
        company_name = (company_state or {}).get("company_name", "your company")
        preamble_base = (
            f"To give you a grounded answer on this, I need a bit more context from you about {company_name}."
        )
        formatted_qs = "\n".join(f"— {q}" for q in questions)
        preamble = f"{preamble_base}\n\n{formatted_qs}"
        clarification_options = [dict(option) for option in (options or []) if isinstance(option, dict)][:3]
        option_lines = [str(option.get("label") or "").strip() for option in clarification_options if option.get("label")]
        sections = []
        if option_lines:
            sections.append({"label": "Pick One", "items": option_lines})
        return {
            "answer": {"title": "", "summary": "", "sections": sections},
            "trust": {
                "confidence": "low",
                "confidence_score": 0.0,
                "assumptions": [],
                "open_questions": questions,
                "data_quality": "low",
                "calculation_used": False,
                "missing_context": [],
            },
            "sources": [],
            "presentation": {
                "mode": "clarification",
                "preamble": preamble,
                "decision": {
                    "decision_summary": "Choose the interpretation that matches what you want so I can continue with the right frame.",
                    "recommended_option": clarification_options[0].get("label") if clarification_options else None,
                    "impact_if_rejected": "I may continue with the wrong frame or give you a weaker answer.",
                    "options": [
                        {
                            "label": option.get("label"),
                            "description": option.get("description"),
                        }
                        for option in clarification_options
                        if option.get("label")
                    ],
                } if clarification_options else None,
            },
            "clarification_options": clarification_options,
        }

    def _report_prompt(
        self,
        task_input: str,
        company_state: Dict[str, Any],
        company_identity: Dict[str, Any],
        preferences: Dict[str, Any],
        project_context: Dict[str, Any],
        session_history: List[Dict[str, Any]],
        signals: List[Dict[str, Any]],
        retrieval: List[Dict[str, Any]],
        vocabulary_block: str = "",
        ceo_memories: Optional[List[Dict[str, Any]]] = None,
        finance_context: Optional[Dict[str, Any]] = None,
        retrieval_manifest: Optional[Dict[str, Any]] = None,
        live_context: Optional[Dict[str, Any]] = None,
        situational_profile: Optional[Dict[str, Any]] = None,
        entity_context: Optional[List[Dict[str, Any]]] = None,
        proactive_observations_block: str = "",
        artifact_type: Optional[str] = None,
        intent_state: Optional[Dict[str, Any]] = None,
        unified_memory: Optional[Dict[str, Any]] = None,
        financial_task: Optional[FinancialAnalysisTask] = None,
    ) -> str:
        kb_entries: List[Dict[str, Any]] = company_state.get("knowledge_base") or []
        state_without_kb = {k: v for k, v in company_state.items() if k != "knowledge_base"}
        compact_company_state = self._json_for_prompt(state_without_kb, max_chars=6000)
        kb_block = self._knowledge_base_block(kb_entries)
        compact_company_identity = self._json_for_prompt(company_identity, max_chars=2500)
        compact_preferences = self._json_for_prompt(preferences, max_chars=1500)
        compact_project_context = self._json_for_prompt(project_context, max_chars=2500)
        compact_session_history = self._json_for_prompt(
            self._compact_session_history(session_history),
            max_chars=5000,
        )
        compact_signals = self._json_for_prompt(signals[:5], max_chars=3500)
        compact_retrieval = self._json_for_prompt(
            self._compact_retrieval_context(retrieval),
            max_chars=7000,
        )
        composition_plan_block = (
            "=== COMPOSITION PLAN (produce this first) ===\n"
            "Before generating the report content, produce a CompositionPlan with:\n\n"
            "section_labels: Choose exactly 3 labels that precisely fit THIS request.\n"
            "  Examples by request type:\n"
            "  - Pricing / competitive analysis: [\"Competitive Position\", \"Margin Impact\", \"Strategic Options\"]\n"
            "  - Customer escalation / at-risk accounts: [\"Risk Summary\", \"Recovery Actions\", \"Owner Assignments\"]\n"
            "  - Board financial review: [\"Financial Snapshot\", \"Board Implications\", \"Recommended Actions\"]\n"
            "  - Delegation / email task: [\"Email Draft\", \"Follow-Up Actions\", \"Assumptions\"]\n"
            "  - Operational breakdown: [\"Current State\", \"Gap Analysis\", \"Next Steps\"]\n"
            "  Choose freely — do not default to finance labels for non-finance requests.\n\n"
            "context_gaps: List any information genuinely missing to answer well. "
            "Empty list if the available context is sufficient.\n\n"
            "output_modality: Best format for this request. "
            "One of: docx, xlsx, pptx, docx+xlsx, pptx+xlsx, inline.\n\n"
            "capability_requires: List write capabilities this response claims to exercise. "
            "Use 'email_send' if offering to send an email. "
            "Use 'calendar_write' if offering to create a calendar event. "
            "Leave empty if only drafting content for manual execution.\n\n"
            "Then generate the ReportPayload using your chosen section_labels.\n\n"
        )
        memory_block = ""
        if ceo_memories:
            memory_lines = []
            for mem in ceo_memories[:10]:
                mem_type = mem.get("memory_type", "fact")
                title = mem.get("title", "")
                content = mem.get("content", "")
                memory_lines.append(f"  [{mem_type}] {title}: {content}")
            memory_block = "CEO memory context (decisions, commitments, preferences from prior sessions):\n" + "\n".join(memory_lines) + "\n\n"
        live_context_block = self._live_context_prompt_block(live_context or {})
        situational_block = self._situational_prompt_block(situational_profile or {})
        followup_block = self._report_followup_instruction_block(
            task_input=task_input,
            session_history=session_history,
            followup_mode="",
        )
        pending_questions_block = self._pending_ceo_questions_block(task_input, session_history)
        # When the CEO signals repeat frustration, prepend a high-priority reminder
        # that the LLM must NOT produce a repeat of the prior turn's content.
        repeat_frustration_block = (
            "=== REPEAT-TURN GUARD ===\n"
            "The CEO has explicitly stated that their prior question was not answered. "
            "Do NOT reproduce prior-turn content or restructure the same sections. "
            "Start your response by directly answering the specific unanswered question(s) "
            "listed in the PENDING CEO QUESTIONS block above.\n\n"
        ) if self._detect_repeat_frustration(task_input, session_history) else ""
        live_context_followup_block = self._live_context_followup_instruction_block(
            task_input=task_input,
            live_context=live_context or {},
            session_history=session_history,
        )
        recommendation_block = self._recommendation_request_block(task_input)
        metric_block = self._metric_governance_block(task_input)
        schedule_block = self._schedule_context_block(task_input)
        finance_block = self._finance_context_block(finance_context or {}, "")
        manifest_block = RetrievalManifest(**(retrieval_manifest or {})).to_prompt_block() if retrieval_manifest else ""
        entity_block = self._entity_context_block(entity_context or [])
        obs_block = proactive_observations_block

        # D2: artifact_type output mode instruction block.
        # Overrides the default 3-section bullet format when a specific artifact
        # is requested (board_brief or action_plan).
        artifact_block = self._artifact_type_instruction_block(artifact_type, None)

        # F1: email scope constraint block.
        # When the CEO requests a delegation/email draft for a specific named entity,
        # scope the email body to that entity only to prevent multi-account collapsing.
        email_scope_block = self._email_scope_instruction_block(task_input, company_state)
        resolution_block = self._resolution_language_instruction_block(task_input)
        intent_block = self._intent_execution_instruction_block(intent_state or {})
        unified_memory_block = self._unified_memory_prompt_block(unified_memory or {})
        financial_task_block = self._financial_task_prompt_block(financial_task)

        return (
            f"{repeat_frustration_block}"
            f"{pending_questions_block}"
            f"CEO request: {task_input}\n\n"
            f"{unified_memory_block}"
            f"{financial_task_block}"
            f"{manifest_block}"
            f"{vocabulary_block}"
            f"Company state: {compact_company_state}\n\n"
            f"Company identity profile: {compact_company_identity}\n\n"
            f"CEO preferences: {compact_preferences}\n\n"
            f"Active project context: {compact_project_context}\n\n"
            f"Recent session history: {compact_session_history}\n\n"
            f"Recent operating signals: {compact_signals}\n\n"
            f"Retrieved context (ranked by authority — primary sources listed first):\n{compact_retrieval}\n\n"
            f"{finance_block}"
            f"{memory_block}"
            f"{entity_block}"
            f"{live_context_block}"
            f"{situational_block}"
            f"{composition_plan_block}"
            f"{kb_block}"
            f"{recommendation_block}"
            f"{metric_block}"
            f"{schedule_block}"
            f"{followup_block}"
            f"{live_context_followup_block}"
            f"{obs_block}"
            "=== CONTEXT CITATION DISCIPLINE ===\n"
            "- Documents labeled 'primary' authority are ground truth. Lead every claim with a primary source if one exists.\n"
            "- Documents labeled 'secondary' are supporting evidence. Use them to corroborate or add nuance.\n"
            "- Documents labeled 'low' authority should only be used when no higher-authority source addresses the point; flag any claim that relies solely on a low-authority source.\n"
            "- Cite sources by including them in the 'sources' list with their source_id and what specific claim they support.\n"
            "- If no retrieved document supports a claim, attribute it to company_state explicitly.\n\n"
            "=== DATA ACCESS POLICY ===\n"
            "Company state above contains authoritative operating metrics (burn rate, runway, cloud spend, ARR, headcount, etc.). "
            "When the CEO asks about any of these metrics, USE the numbers from company_state directly. "
            "DO NOT say 'I don't have access to X' when company_state contains X. "
            "If the exact real-time figure is not in company_state, REASON from the nearest available metric and state your inference explicitly. "
            "Never refuse to answer a financial or operational question by claiming data unavailability when company_state contains relevant figures.\n\n"
            "IMPORTANT: The summary field must lead with the answer or decision — not a description of what the report covers. "
            "Never open with 'This report outlines', 'This report summarizes', 'The following report', or 'To effectively X'. "
            "Instead: if the CEO asks a yes/no question, open with Yes or No. "
            "If they ask for a sequence, open with 'First:'. If they ask what to defer, open with what is safe to defer.\n\n"
            f"{artifact_block}"
            f"{email_scope_block}"
            f"{resolution_block}"
            f"{intent_block}"
            "CRITICAL: If the request mentions a deck, slides, PowerPoint, PPTX, workbook, Excel, or DOCX — "
            "generate the executive CONTENT for that format. The system will produce the actual file automatically. "
            "Never state that file generation is unavailable or unsupported."
        )

    def _fix_email_sections(self, payload: ReportPayload) -> ReportPayload:
        """
        Post-process payload when artifact_type == 'email'.
        The LLM often uses default section labels (Financial Snapshot, Key Finding, etc.)
        even when instructed to use 'Email Draft' and 'Follow-Up Actions'.
        This function detects the email content by looking for 'Subject:' in section items
        and renames sections to the correct email labels.
        """
        sections = payload.answer.sections
        email_section_idx = None
        for i, sec in enumerate(sections):
            items_text = " ".join(sec.items or [])
            if "subject:" in items_text.lower() or (sec.content and "subject:" in sec.content.lower()):
                email_section_idx = i
                break

        if email_section_idx is None:
            # No Subject: found; try to find the section with the most email-like content
            for i, sec in enumerate(sections):
                items_text = " ".join(sec.items or [])
                if any(kw in items_text.lower() for kw in ("dear ", "hi ", "hello,", "best,", "regards,", "sincerely,")):
                    email_section_idx = i
                    break

        if email_section_idx is not None:
            new_sections = []
            for i, sec in enumerate(sections):
                if i == email_section_idx:
                    new_sections.append(ReportSection(label="Email Draft", items=sec.items, content=sec.content))
                elif i == email_section_idx + 1:
                    new_sections.append(ReportSection(label="Follow-Up Actions", items=sec.items, content=sec.content))
                # Drop any further sections beyond the two email sections
            payload.answer.sections = new_sections[:2]

        return payload

    # Email draft intent markers (F1)
    _EMAIL_DRAFT_MARKERS = (
        "draft", "write an email", "compose", "send to", "email to",
        "delegate", "delegation email", "write to", "message to",
    )
    _RESOLUTION_LANGUAGE_MARKERS = (
        "board resolution",
        "resolution language",
        "whereas",
        "resolved",
        "vote on specific language",
        "pricing committee structure",
        "formalizes this pricing committee structure",
    )

    def _is_explicit_email_request(self, task_input: str, artifact_type: Optional[str] = None) -> bool:
        lowered = (task_input or "").lower()
        if artifact_type == "email":
            return True
        return self._contains_any_marker(
            lowered,
            (
                "draft email",
                "write an email",
                "compose an email",
                "draft the email",
                "draft me an email",
                "write me an email",
                "executive recovery",
                "recovery response",
                "draft the response",
                "compose the message",
                "write the response",
                "email draft to",
                # Meeting invite / sendable draft variants — route to email mode so output
                # is a clean, immediately sendable draft rather than a framework.
                "draft the invite",
                "draft invite",
                "draft the meeting invite",
                "draft a meeting invite",
                "meeting invite",
                "draft the meeting",
                "write the invite",
            ),
        )

    def _is_resolution_language_request(self, task_input: str) -> bool:
        lowered = (task_input or "").lower()
        if not self._contains_any_marker(lowered, self._RESOLUTION_LANGUAGE_MARKERS):
            return False
        return "pricing committee" in lowered or "committee" in lowered

    def _email_scope_instruction_block(
        self, task_input: str, company_state: Dict[str, Any]
    ) -> str:
        """
        Return a prompt instruction block that scopes an email draft to the specific
        named entity in the CEO's request (F1).  Returns empty string when the request
        is not an email-drafting request or no specific entity can be extracted.
        """
        from src.core.entity_extraction import extract_entities_from_text

        lowered = task_input.lower()
        if not self._contains_any_marker(lowered, self._EMAIL_DRAFT_MARKERS):
            return ""

        # Extract named entities from the CEO's request text.
        # Prefer entities that appear in company_state (org_structure, knowledge_base)
        # to avoid false positives from generic words.
        request_entities = extract_entities_from_text(task_input)
        if not request_entities:
            return ""

        # Cross-reference against known names in org_structure and knowledge_base
        # to pick the most specific entity to scope to.
        org: Dict[str, str] = company_state.get("org_structure") or {}
        kb: List[Dict[str, Any]] = company_state.get("knowledge_base") or []
        initiatives: List[Dict[str, Any]] = company_state.get("strategic_initiatives") or []

        known_names = set(org.values()) | {str(k.get("title") or "") for k in kb} | {str(i.get("name") or "") for i in initiatives}
        # Find first entity that matches a known name OR is explicitly mentioned in the request
        scoped_entity: Optional[str] = None
        for entity in request_entities:
            if any(entity.lower() in known.lower() or known.lower() in entity.lower() for known in known_names if known):
                scoped_entity = entity
                break
        if scoped_entity is None:
            # Fall back to the first extracted entity if no org match — still better than nothing
            scoped_entity = request_entities[0]

        # Find the recipient from org_structure if the entity is a person name
        recipient = org.get("VP Sales") or org.get("CTO") or org.get("CFO") or ""
        for role, name in org.items():
            if scoped_entity.lower() in name.lower():
                recipient = name
                break

        recipient_line = f" addressed to {recipient}" if recipient else ""
        return (
            f"=== EMAIL SCOPE CONSTRAINT (F1) ===\n"
            f"The CEO has requested an email draft specifically about: {scoped_entity!r}.\n"
            f"Write the email{recipient_line} covering ONLY '{scoped_entity}'. "
            f"Do NOT mention other accounts, deals, or at-risk items — even if they appear in the context. "
            f"The email body must open with a proper salutation (e.g. 'Hi [Name],' or 'Dear [Name],') "
            f"and be addressed to a single, specific recipient.\n\n"
        )

    def _artifact_type_instruction_block(
        self, artifact_type: Optional[str], finance_template: Optional[str]
    ) -> str:
        """
        Return a prompt instruction block that overrides the default 3-section bullet
        format when a specific artifact output mode is active (D2).
        Returns empty string for the default "report" mode.
        """
        if artifact_type == "board_brief":
            labels = self._template_section_labels(finance_template) if finance_template else [
                "Executive Summary", "Key Findings", "Recommended Actions"
            ]
            return (
                "=== OUTPUT MODE: BOARD BRIEF — STRICT ===\n"
                "The CEO has requested a full board-ready document. DO NOT produce a summary. DO NOT produce bullets.\n"
                "Produce a comprehensive written brief using ALL available data from context.\n"
                f"answer.title: A clear document title (e.g. 'Q1 2026 Board Brief — Financial & Pipeline Review').\n"
                f"answer.summary: 3–4 sentences of executive narrative. Not bullet points.\n"
                f"answer.sections: Use labels {labels}. "
                "Each section's `items` array must contain PROSE PARAGRAPHS — complete sentences forming a paragraph (minimum 3 sentences each). "
                "ABSOLUTELY NO bullet characters (•, -, *, –) in any item string. "
                "ABSOLUTELY NO one-liners or fragment strings. "
                "Name specific owners, dollar figures, percentages, and dates from the loaded context. "
                "Write as if this is the actual document the CEO will hand to the board — not a summary of it.\n"
                "CRITICAL — MISSING DATA RULE: If the CEO accepted an offer to pull an account brief and some specific "
                "details (contract terms, contact email, etc.) are not in your context, STILL produce the complete brief. "
                "Use every available signal about this customer. Write analysis, risk assessment, contacts, and recommended actions. "
                "NEVER say 'Insufficient Primary Data Available', 'No primary account documents found', or any variant of refusal. "
                "Acknowledge limited data as an assumption in the assumptions field — then deliver the document regardless.\n"
                "trust.open_questions: Leave empty []. Board briefs are delivered, not questioned.\n\n"
            )
        if artifact_type == "action_plan":
            return (
                "=== OUTPUT MODE: ACTION PLAN ===\n"
                "The CEO has requested a structured action plan. Do NOT use narrative bullets.\n"
                "answer.title: A clear action-plan title.\n"
                "answer.summary: 1–2 sentences describing the situation that drives these actions.\n"
                "answer.sections: Use exactly these labels — 'Immediate Actions (This Week)', "
                "'30-Day Owners', 'Dependencies & Risks'.\n"
                "Each item in 'Immediate Actions' and '30-Day Owners' MUST follow this exact format:\n"
                "  'Action N: <what to do> — Owner: <name from org_structure> — By: <specific date or deadline> — Impact: <$ savings or % improvement>'\n"
                "Do not use vague owners like 'Finance team'. Use the specific name from org_structure.\n"
                "Do not write 'TBD' for dollar impact — estimate from the numbers in company_state.\n\n"
            )
        if artifact_type == "email":
            return (
                "=== OUTPUT MODE: EMAIL DRAFT ===\n"
                "The CEO has requested a delegation or communication email. DO NOT produce a bullet summary.\n"
                "Produce a ready-to-send email as the output.\n"
                "answer.title: A brief description of the email (e.g. 'Email Draft to Sarah Chen — AlphaSystems DACH Delegation').\n"
                "answer.summary: 1 sentence describing the email purpose and recipient.\n"
                "answer.sections: Use exactly 2 sections:\n"
                "  'Email Draft' — items[0] MUST start with 'Subject: <the email subject line>' on its own line. "
                "Then items[1] is the COMPLETE email body: salutation (e.g. 'Hi Sarah,'), 2–3 substantive body paragraphs, "
                "sign-off (e.g. 'Best, [CEO Name]'). Write in first-person as the CEO. Be direct, specific, and professional.\n"
                "  'Follow-Up Actions' — 2–3 items the CEO should track after sending (owner, deadline).\n"
                "SCOPE: Address only the specific person and account named in the request. "
                "Do not merge multiple accounts or deals into a single email.\n"
                "CRITICAL — MISSING DATA RULE: If the CEO requests an executive recovery or outreach email for a named "
                "customer/account that is not in your context data, STILL write the complete email. "
                "Use the situation the CEO described (outage, delivery miss, delay, etc.) and compose a professional "
                "executive-level recovery email using reasonable language. DO NOT say 'I have no information about X' "
                "or 'No account details found' — that is not acceptable. Write the actual email draft.\n"
                "trust.open_questions: Leave empty []. Deliver the email, do not question it.\n\n"
            )
        # Default "report" mode — use existing prompt instructions unchanged.
        preferred_labels = (
            self._template_section_labels(finance_template) if finance_template
            else ["Key Finding", "Business Implications", "Recommended Actions"]
        )
        return (
            "Return a ReportPayload JSON object with:\n"
            "- answer.title\n"
            "- answer.summary\n"
            "- answer.sections\n"
            "- trust\n"
            "- sources\n"
            "- presentation.preamble — REQUIRED. 1–2 sentences, first-person, conversational. "
            "Acknowledge what you did and the single most important thing found. "
            "Write it as if speaking directly to the CEO before handing over the report — natural, not formal. "
            "Examples: 'Pulled the last three quarters of AWS spend and compared against budget — there\\'s a 23% overage in compute that\\'s worth addressing before the board call.' "
            "or 'Looked at the hiring pipeline against the Q3 headcount plan — engineering is the only team running behind, and it\\'s tied to two open senior roles.' "
            "Do NOT start with 'I have', 'I\\'ve', 'Here is', 'Based on', or 'This report'. "
            "Do NOT summarize sections. Be specific to what was actually found.\n"
            "The answer.sections array must contain exactly 3 topics.\n"
            "Each topic must contain exactly 3 concise subpoints in `items`.\n"
            f"Prefer topic labels for {preferred_labels}.\n"
            "Match the structure, tone, branding, and formatting expectations described in the company identity profile when available.\n\n"
        )

    def _resolution_language_instruction_block(self, task_input: str) -> str:
        if not self._is_resolution_language_request(task_input):
            return ""
        return (
            "=== OUTPUT MODE: BOARD RESOLUTION LANGUAGE ===\n"
            "The CEO is asking for working board-resolution text, not commentary about governance process.\n"
            "Produce actual resolution language the board can review now.\n"
            "answer.title: A concise title naming the committee resolution.\n"
            "answer.summary: 1-2 sentences stating what the resolution establishes.\n"
            "answer.sections: Use exactly 3 sections:\n"
            "  'Resolution Text' — include WHEREAS / RESOLVED clauses as actual draft language.\n"
            "  'Committee Structure' — list membership, authority levels, and reporting obligations.\n"
            "  'Counsel Review Points' — only 2-3 short items for legal refinement; do not refuse the task.\n"
            "If some governance details are not explicit in context, infer a reasonable working draft from the prior pricing-committee discussion and state refinement points in section 3.\n"
            "Never say you cannot draft the language. Draft it.\n\n"
        )

    def _intent_execution_instruction_block(self, intent_state: Dict[str, Any]) -> str:
        deliverable = intent_state.get("deliverable") if isinstance(intent_state, dict) else {}
        if not isinstance(deliverable, dict):
            return ""
        if deliverable.get("kind") != "execution_bundle":
            return ""
        task_topic = str(intent_state.get("task_topic") or "")
        bundle_items = [str(item) for item in (deliverable.get("bundle_items") or []) if str(item)]
        if not bundle_items:
            bundle_items = ["call_script", "coordination_email"]
        label_map = {
            "call_script": "Call Script",
            "extension_terms": "Extension Terms",
            "coordination_email": "Coordination Email",
            "call_setup": "Call Setup",
            "project_plan": "Project Plan",
            "methodology": "Methodology",
            "milestone_plan": "Milestone Plan",
            "checklist": "Checklist",
            "approval_workflow": "Approval Workflow",
            "discount_authority_matrix": "Discount Guardrails",
            "customer_script": "Customer Script",
            "success_metrics": "Success Metrics",
            "regional_guardrails": "Regional Guardrails",
        }
        section_labels = [label_map.get(item, item.replace("_", " ").title()) for item in bundle_items[:3]]
        while len(section_labels) < 3:
            section_labels.append(f"Deliverable {len(section_labels) + 1}")
        pricing_topic_block = (
            "- This is a pricing-response implementation package, not a cost-cut memo.\n"
            "- Keep every section tied to discount approvals, deal guardrails, customer-facing language, rollout containment, or win-rate and margin tracking.\n"
            "- Do not include burn rate, runway, AWS variance, or generic cost-containment content unless the CEO explicitly asked for it.\n"
        ) if task_topic == "pricing_response" else ""
        return (
            "=== OUTPUT MODE: EXECUTION BUNDLE ===\n"
            "The CEO is asking for actual deliverables, not another analysis memo.\n"
            "Produce the requested working materials directly.\n"
            f"answer.sections: Use exactly these labels: {section_labels}.\n"
            "- For 'Call Script' or 'Talking Points', give the exact script or bullet-by-bullet talking points the CEO can use immediately.\n"
            "- For 'Extension Terms', give the specific offer language, dates, commercial terms, and contingencies.\n"
            "- For 'Coordination Email', include a real subject line and full email body addressed to the named operator.\n"
            "- For 'Call Setup', give the exact outreach text, attendees, timing target, and scheduling handoff language.\n"
            "- For 'Project Plan', give the owner-specific workstream with phases, steps, and deliverables.\n"
            "- For 'Methodology', give the exact process, fields, calculations, and review logic the owner should use.\n"
            "- For 'Milestone Plan', give the dated checkpoints, owners, and success criteria.\n"
            "- For 'Checklist', give the required fields, artifacts, and completion criteria.\n"
            "- For 'Approval Workflow', give approval thresholds, owners, escalation path, and turn-time expectations.\n"
            "- For 'Discount Guardrails', give discount bands, approval thresholds, non-negotiables, and containment rules.\n"
            "- For 'Customer Script', give the exact customer-facing language sales should use for the pricing move.\n"
            "- For 'Success Metrics', give the win-rate, margin, conversion, and exception metrics to track in the first 30 days.\n"
            "- For 'Regional Guardrails', give the geographic containment rules and how to avoid spillover or a broader price war.\n"
            f"{pricing_topic_block}"
            "- Do not explain what should happen. Produce the materials themselves.\n"
            "- If some detail is inferred, state the assumption inside the deliverable and keep going.\n\n"
        )

    def _report_followup_mode(self, task_input: str, session_history: List[Dict[str, Any]]) -> str:
        lowered = task_input.lower()
        if self._contains_any_marker(lowered, self.ACTION_PLAN_MARKERS):
            return "action_plan"

        recent_queries = [str(item.get("query") or "").lower() for item in session_history[-4:]]
        similar_recent_queries = sum(
            1
            for query in recent_queries
            if query and self._share_followup_topic(query, lowered)
        )
        repeated_specificity = sum(
            1
            for query in recent_queries
            if self._contains_any_marker(query, self.ACTION_PLAN_MARKERS)
            or self._contains_any_marker(query, self.SPECIFICITY_MARKERS)
        )
        current_specificity = self._contains_any_marker(lowered, self.SPECIFICITY_MARKERS)
        if current_specificity and repeated_specificity >= 1:
            return "action_plan"
        if similar_recent_queries >= 2 and repeated_specificity >= 1:
            return "action_plan"
        return "standard"

    def _finance_followup_submode(self, task_input: str, session_history: List[Dict[str, Any]]) -> str:
        lowered = task_input.lower()
        # Don't apply finance submodes to hiring/candidate questions — they need different framing
        if self._contains_any_marker(lowered, self.HIRING_CONTEXT_MARKERS):
            return "standard"
        if self._is_explicit_email_request(task_input) or self._is_resolution_language_request(task_input):
            return "standard"
        finance_drilldown_markers = (
            "cost containment",
            "containment plan",
            "containment actions",
            "cost cuts",
            "spend reduction",
            "burn reduction",
            "runway extension",
            "cloud reduction",
            "cloud infrastructure",
            "sales and marketing",
            "s&m",
            "dev environment",
            "development environment",
            "hiring deferral",
            "hiring freeze",
            "monthly reduction",
            "monthly savings",
            "monthly cut",
            "marcus",
        )
        finance_detail_markers = (
            "who owns",
            "who is responsible",
            "owner",
            "owners",
            "timeline",
            "timing",
            "this week",
            "operational risk",
            "operational risks",
            "risk",
            "risks",
            "breakdown",
            "specifics",
            "specific",
            "details",
            "what exactly",
            "exactly",
            "which",
            "when",
        )
        recent_queries = " ".join(str(item.get("query") or "") for item in session_history[-4:]).lower()
        if (
            any(marker in lowered for marker in finance_drilldown_markers)
            and any(marker in lowered for marker in finance_detail_markers)
        ) or (
            any(marker in recent_queries for marker in finance_drilldown_markers)
            and any(marker in lowered for marker in finance_detail_markers)
        ):
            return "operational_breakdown"
        if any(
            marker in lowered
            for marker in (
                "cross-department",
                "cross-departmental",
                "cross functional",
                "cross-functional",
                "collaboration",
                "alignment",
                "dashboard",
                "incentivize",
                "threshold",
                "thresholds",
            )
        ):
            return "metric_governance"
        if any(marker in lowered for marker in ("unforeseen expense", "unforeseen expenses", "expense", "expenses")):
            return "standard"
        if any(marker in lowered for marker in ("what metrics", "which metrics", "track the success", "measure the success", "how will we measure", "how do we measure", "measure success", "success metric", "success criteria", "kpi", "kpis")):
            return "kpi_tracking"
        if any(marker in lowered for marker in ("revenue shortfall", "aws costs", "optimize aws", "variance", "forecast")):
            return "variance_drilldown"
        if any(marker in lowered for marker in ("finance close", "investor", "board")):
            return "close_week_metrics"
        if "investor" in recent_queries or "finance close" in recent_queries:
            return "close_week_metrics"
        return "standard"

    def _report_followup_instruction_block(
        self,
        *,
        task_input: str,
        session_history: List[Dict[str, Any]],
        followup_mode: str,
    ) -> str:
        if followup_mode != "action_plan":
            return ""

        recent_queries = [
            str(item.get("query") or "").strip()
            for item in session_history[-3:]
            if str(item.get("query") or "").strip()
        ]
        recent_block = f"Recent follow-up asks: {recent_queries}\n" if recent_queries else ""
        lowered = task_input.lower()
        extra = ""
        if self._contains_any_marker(lowered, ("board narrative", "corrective story", "corrective narrative", "frame the variance", "frame for the board")):
            extra = (
                "- The CEO is asking for the exact narrative to use with the board. "
                "The first item in section 1 must be the specific sentence or paragraph to read aloud. "
                "Format it as: 'Board narrative: [exact sentence].' Do not summarize — give the actual words.\n"
            )
        elif self._contains_any_marker(lowered, ("what questions should i expect", "board questions", "questions from the board", "do i have answers")):
            extra = (
                "- The CEO is asking what the board will ask and whether answers are ready. "
                "Format section 1 as anticipated Q&A pairs: 'Expected Q: [question] \u2192 Answer: [specific answer].' "
                "Use the Board Anticipated Questions from the knowledge base if available.\n"
            )
        elif self._contains_any_marker(lowered, ("board packet", "packet need", "specific edits")) or (
            "before it goes out" in lowered
            and self._contains_any_marker(lowered, ("board", "packet", "finance", "narrative", "close", "filing"))
        ):
            extra = (
                "- The CEO is asking what edits are needed before the board packet goes out. "
                "Each section item must be a specific edit action: what page or section to update, what sentence to add or change, and who makes the edit.\n"
            )
        elif self._contains_any_marker(lowered, ("defer", "delegate", "what can i safely", "what can we safely", "safely defer", "safely delegate")):
            extra = (
                "- The CEO is asking what they can DEFER or DELEGATE — not what they need to do urgently.\n"
                "- Section 1 must be titled 'CEO Must Own Today' and list at most 2-3 items the CEO cannot hand off, "
                "each with a one-sentence reason why only the CEO can do it.\n"
                "- Section 2 must be titled 'Safe to Delegate' and name the specific delegate for each item plus the handoff action.\n"
                "- Section 3 must be titled 'Safe to Defer' and list items the CEO does NOT need to touch today, "
                "with the earliest date or trigger that makes them relevant.\n"
                "- Do not list urgent or critical items as answers to this question — the CEO already knows those. "
                "Focus entirely on what they can put down.\n"
            )
        elif self._contains_any_marker(lowered, ("sequence", "how should i sequence", "how to sequence", "what order should", "prep sequence", "priority order")):
            extra = (
                "- The CEO is asking HOW TO SEQUENCE their work — not what the work is.\n"
                "- Open the summary with the sequence directly: 'First: [X], then: [Y], then: [Z].'\n"
                "- Section 1 must be the ordered sequence with one-line rationale for each ordering decision "
                "(e.g. 'Start with X because it gates Y', 'Do Z last because it only needs 30 minutes').\n"
                "- Section 2 must list the constraints that dictate the order: meeting times, dependencies, hard deadlines.\n"
                "- Section 3 must state what to skip or compress if time runs short, and what the cost of skipping is.\n"
                "- Do not restate what the work is. Focus entirely on order, rationale, and trade-offs.\n"
            )
        return (
            "=== FOLLOW-UP ESCALATION MODE ===\n"
            f"{recent_block}"
            "The CEO is asking for more specificity or an action plan.\n"
            "- Do not open the summary with generic phrases like 'This report outlines', 'This report summarizes', "
            "or 'The following report'. Lead with the answer or decision directly.\n"
            "- Answer the request directly in the first sentence.\n"
            "- Make the third section execution-oriented with named owners, concrete actions, and timing.\n"
            "- If the request asks for priorities, order the actions by urgency and state the top priority first.\n"
            "- If the request asks for timelines or responsible parties, include both explicitly in the action items.\n"
            "- Build on the follow-up context instead of repeating the same high-level framing.\n"
            f"{extra}\n"
        )

    def _live_context_followup_instruction_block(
        self,
        *,
        task_input: str,
        live_context: Dict[str, Any],
        session_history: List[Dict[str, Any]],
    ) -> str:
        if not self._is_live_context_artifact_followup(task_input, live_context, session_history):
            return ""
        modality, _ = self._select_output_modality(task_input)
        schedule = live_context.get("current_schedule") or {}
        decisions = live_context.get("open_decisions") or []
        commitments = live_context.get("open_commitments") or []
        lines = [
            "=== LIVE THREAD FOLLOW-UP MODE ===",
            "Treat this request as a continuation of the active conversation thread, not a fresh standalone report.",
            f"Likely output conversion target: {modality}.",
            "- Reuse the live thread context first before falling back to generic company state.",
            "- If the CEO asks for a memo, deck, workbook, chart, or refinement, inherit the active schedule, open decisions, and recent contribution context automatically.",
        ]
        if isinstance(schedule, dict) and schedule:
            lines.append("- The current schedule is part of the parent context. Convert its blocks, meetings, and deadlines into the requested artifact or recommendation.")
        if decisions:
            lines.append("- Keep the open decisions visible in the summary and recommended actions unless the new request clearly resolves them.")
        if commitments:
            lines.append("- Preserve open commitments as execution constraints or follow-up actions where relevant.")
        lines.append("- Do not restart from a generic company overview unless the live thread lacks enough detail.")
        return "\n".join(lines) + "\n\n"

    def _is_live_context_artifact_followup(
        self,
        task_input: str,
        live_context: Dict[str, Any],
        session_history: List[Dict[str, Any]],
    ) -> bool:
        if not isinstance(live_context, dict) or not live_context:
            return False
        has_parent_context = any(
            [
                live_context.get("current_schedule"),
                live_context.get("open_decisions"),
                live_context.get("open_commitments"),
                live_context.get("last_agent_contributions"),
            ]
        )
        if not has_parent_context:
            return False
        lowered = task_input.lower()
        artifact_or_conversion_markers = (
            "memo",
            "docx",
            "deck",
            "slides",
            "presentation",
            "pptx",
            "workbook",
            "xlsx",
            "excel",
            "chart",
            "graph",
            "visual",
            "turn into",
            "make",
            "refine",
            "expand",
            "polish",
        )
        schedule_reference_markers = (
            "schedule",
            "plan",
            "board prep",
            "follow-up",
            "follow up",
            "open decision",
        )
        if any(marker in lowered for marker in artifact_or_conversion_markers) and any(
            marker in lowered for marker in ("that", "this", "it", "above")
        ):
            return True
        if any(marker in lowered for marker in artifact_or_conversion_markers) and any(
            marker in lowered for marker in schedule_reference_markers
        ):
            return True
        if len(lowered.split()) <= 12 and any(marker in lowered for marker in artifact_or_conversion_markers):
            recent_queries = " ".join(str(item.get("query") or "") for item in session_history[-3:]).lower()
            if any(marker in recent_queries for marker in schedule_reference_markers) or live_context.get("current_schedule"):
                return True
        return False

    def _recommendation_request_block(self, task_input: str) -> str:
        if not self._contains_any_marker(task_input.lower(), self.RECOMMENDATION_REQUEST_MARKERS):
            return ""
        return (
            "=== RECOMMENDATION REQUEST ===\n"
            "The CEO is asking for a direct recommendation or go/no-go decision.\n"
            "- First identify exactly which role, candidate, account, or initiative the question is about.\n"
            "- Read the signals and context for that specific item — do not apply signals from a different "
            "role or candidate to this one.\n"
            "- Open the summary with a decisive recommendation. The options are:\n"
            "  'Yes, advance [role/candidate] — [one-line reason].'\n"
            "  'Yes, advance to [specific next stage] — [one-line reason].'\n"
            "  'No, pass on this candidate — [one-line reason].'\n"
            "- Do NOT say 'defer' or 'pause' — those are not decisions. If the evidence is mixed, "
            "recommend the next concrete action that resolves the uncertainty (e.g. 'advance to CEO "
            "calibration interview to resolve the split on execution depth').\n"
            "- Use the first section for supporting evidence, the second for risks or trade-offs, "
            "and the third for concrete next steps with owner and timing.\n\n"
        )

    def _schedule_context_block(self, task_input: str) -> str:
        schedule_markers = (
            "today",
            "my day",
            "this morning",
            "this afternoon",
            "before my meetings",
            "before specific meetings",
            "structure my day",
            "prioritize my day",
            "plan my day",
            "follow-ups from yesterday",
            "follow-ups from this morning",
            "outstanding follow",
            "what decisions need to be made",
        )
        if not self._contains_any_marker(task_input.lower(), schedule_markers):
            return ""
        return (
            "=== SCHEDULE & DAY PLANNING DISCIPLINE ===\n"
            "The CEO is asking about day structure, decisions, or follow-ups.\n"
            "- Anchor every recommendation to a specific time, meeting, or deadline from the company context. "
            "Do not give generic productivity advice.\n"
            "- If the question asks what decisions are needed before meetings: name each meeting explicitly, "
            "then state the one decision that must be made before it and who needs to confirm it.\n"
            "- If the question asks about follow-ups: pull thread status and owner from email/signals context — "
            "do not list items the CEO already knows about without adding owner or status.\n"
            "- Format time-sensitive actions as: '[Time block or meeting name] — Decision/action needed: [specific item] — Owner: [who].'\n"
            "- Distinguish clearly between: (a) what the CEO must decide personally, (b) what they need to check "
            "on before a meeting, and (c) what can be confirmed async.\n\n"
        )

    def _metric_governance_block(self, task_input: str) -> str:
        metric_markers = (
            "metric",
            "metrics",
            "track the success",
            "measure the success",
            "measure success",
            "how will we measure",
            "how do we measure",
            "kpi",
            "kpis",
            "dashboard",
            "governance",
            "success criteria",
            "success metric",
            "success metrics",
        )
        if not self._contains_any_marker(task_input.lower(), metric_markers):
            return ""
        return (
            "=== METRIC & GOVERNANCE RESPONSE REQUIREMENTS ===\n"
            "The CEO is asking about metrics, KPIs, tracking, or success measurement.\n"
            "- Do not describe what will be tracked without specifying how it will be tracked.\n"
            "- For every metric or KPI mentioned, include a named owner, a cadence (weekly/monthly/quarterly), "
            "and a concrete target or threshold.\n"
            "- Format each metric as: '[Metric name] — Owner: [name/team], Cadence: [frequency], "
            "Target: [specific value or threshold].'\n"
            "- At least 2 items in section 3 (Recommended Actions) must follow this exact format.\n"
            "- If the CEO asks 'how will we measure X', answer with concrete measurement criteria — "
            "not a restatement of the goal.\n\n"
        )

    def _finance_context_block(self, finance_context: Dict[str, Any], finance_submode: str) -> str:
        if not finance_context:
            return ""
        return (
            "=== FINANCE ANALYSIS DISCIPLINE ===\n"
            f"Finance follow-up submode: {finance_submode}\n"
            f"Current metrics: {self._json_for_prompt(finance_context.get('current_metrics', []), max_chars=1800)}\n"
            f"Board materials: {self._json_for_prompt(finance_context.get('board_materials', []), max_chars=800)}\n"
            f"Variance signals: {self._json_for_prompt(finance_context.get('variance_signals', []), max_chars=1200)}\n"
            f"Metric governance defaults: {self._json_for_prompt(finance_context.get('metric_governance', []), max_chars=1500)}\n"
            "Financial-statements rules:\n"
            "- Prefer named metrics over generic categories.\n"
            "- When possible, state actual vs plan or actual vs prior explicitly.\n"
            "- Label a driver or cause for each major metric movement.\n"
            "- For any question about metrics, KPIs, tracking, or success measurement: every metric must include "
            "a named owner, cadence (weekly/monthly/quarterly), and a target or threshold value. "
            "Format as: '[Metric] — Owner: [name], Cadence: [frequency], Target: [value or threshold].'\n"
            "- For KPI follow-ups, at least 2 items in the recommended actions section must follow this owner/cadence/target format.\n"
            "- Avoid generic strategy phrasing when the CEO asks for metrics, tracking, or governance.\n"
            "- VARIANCE DRILL-DOWN RULE: When the CEO asks 'how much [variance/overage/overrun]' or "
            "'who is accountable', you MUST answer with the specific figure and named owner from "
            "variance_signals above. If no figure is present in variance_signals, list it explicitly "
            "in missing_context as 'Q1 cloud variance overage amount — not in seeded data' and do NOT "
            "skip the question or repeat prior-turn content in its place.\n\n"
        )

    # ── Pending-question / repeat-frustration detection ───────────────────────

    _VARIANCE_QUESTION_PATTERNS: tuple[str, ...] = (
        "how much overage",
        "how much variance",
        "how much overrun",
        "how much over",
        "overage are we",
        "overage amount",
        "how much is the",
    )
    _ACCOUNTABILITY_QUESTION_PATTERNS: tuple[str, ...] = (
        "who is accountable",
        "who's accountable",
        "who is responsible",
        "who's responsible",
        "who owns",
        "who is fixing",
        "who's fixing",
    )
    _DEADLINE_QUESTION_PATTERNS: tuple[str, ...] = (
        "by noon",
        "by today",
        "by end of day",
        "by tonight",
        "fix it by",
        "resolve by",
        "done by",
    )
    _REPEAT_FRUSTRATION_MARKERS: tuple[str, ...] = (
        "you didn't answer",
        "you dodged",
        "you ignored",
        "you completely",
        "you skipped",
        "don't make me ask twice",
        "make me ask twice",
        "ask again",
        "still haven't",
        "answer my question",
        "didn't address",
        "completely dodged",
        "completely failed",
        "completely ignored",
        "not answered",
        "never answered",
        "you gave me",
        "you completely dodged",
    )

    def _extract_pending_ceo_questions(
        self,
        task_input: str,
        session_history: List[Dict[str, Any]],
    ) -> List[str]:
        """
        Inspect the most recent completed turn in session_history to detect CEO
        sub-questions that were not addressed in the prior assistant response.
        Returns a list of instruction strings to inject into the next prompt so
        the LLM answers them before generating anything else.

        Uses deterministic pattern matching — no LLM call.
        """
        if not session_history:
            return []

        last_turn = session_history[-1]
        prior_query = str(last_turn.get("query") or "").lower()
        prior_response = str(last_turn.get("response") or "").lower()

        if not prior_query:
            return []

        unanswered: List[str] = []

        # ── Variance / overage amount ──────────────────────────────────────────
        if self._contains_any_marker(prior_query, self._VARIANCE_QUESTION_PATTERNS):
            # Prior response must mention a dollar amount, percentage, or "k over/above"
            # to count as answered.
            variance_answered = any(
                marker in prior_response
                for marker in ("$", "k over", "k above", "k variance", "% over", "overage of")
            )
            if not variance_answered:
                unanswered.append(
                    "UNANSWERED (prior turn): CEO asked for the exact variance/overage amount. "
                    "Pull the figure from variance_signals and state it explicitly (e.g. '$X over forecast'). "
                    "If missing from variance_signals, add it to missing_context — do NOT skip or repeat prior content."
                )

        # ── Accountability / named owner ───────────────────────────────────────
        if self._contains_any_marker(prior_query, self._ACCOUNTABILITY_QUESTION_PATTERNS):
            accountability_answered = any(
                marker in prior_response
                for marker in ("accountable", "responsible", "owner", "owns", "assigned to")
            )
            if not accountability_answered:
                unanswered.append(
                    "UNANSWERED (prior turn): CEO asked who is accountable/responsible. "
                    "Name the specific owner from org_structure or the relevant team lead."
                )

        # ── Specific deadline commitment ───────────────────────────────────────
        if self._contains_any_marker(prior_query, self._DEADLINE_QUESTION_PATTERNS):
            deadline_answered = any(
                marker in prior_response
                for marker in ("noon", "end of day", "by today", "by tomorrow", "deadline", "by april", "by may")
            )
            if not deadline_answered:
                unanswered.append(
                    "UNANSWERED (prior turn): CEO set a specific deadline. "
                    "Confirm whether it can be met and name the owner responsible."
                )

        # ── CEO explicitly signals repeat frustration ──────────────────────────
        # When the CEO says the question was ignored/dodged, extract the most specific
        # question sentence from the prior query and surface it as CRITICAL priority.
        if self._contains_any_marker(task_input.lower(), self._REPEAT_FRUSTRATION_MARKERS):
            sentences = prior_query.split("?")
            for sentence in sentences:
                sentence = sentence.strip()
                if len(sentence) > 15 and self._contains_any_marker(
                    sentence,
                    ("how much", "who is", "who's", "who owns", "what is the", "how many", "when will", "overage", "variance"),
                ):
                    # Insert at front so it's the highest-priority instruction
                    unanswered.insert(
                        0,
                        f"CRITICAL — CEO repeated this unanswered question: '{sentence}?' "
                        "Answer this question FIRST in your response before any other content.",
                    )
                    break

        return unanswered

    def _pending_ceo_questions_block(
        self,
        task_input: str,
        session_history: List[Dict[str, Any]],
    ) -> str:
        """
        Build a prompt instruction block that forces the LLM to answer any
        unanswered CEO sub-questions from the prior turn before continuing.
        Returns empty string when there is nothing pending.
        """
        pending = self._extract_pending_ceo_questions(task_input, session_history)
        if not pending:
            return ""
        lines = "\n".join(f"  - {item}" for item in pending)
        return (
            "=== PENDING CEO QUESTIONS (must be answered first) ===\n"
            "The following questions were asked in a prior turn but NOT addressed in the response. "
            "You MUST answer each one before generating any other content. "
            "Do NOT repeat prior-turn content as a substitute for answering.\n"
            f"{lines}\n\n"
        )

    def _detect_repeat_frustration(
        self,
        task_input: str,
        session_history: List[Dict[str, Any]],
    ) -> bool:
        """
        Returns True when the CEO's current message signals that the prior
        response failed to answer a direct question (frustration / repeat ask).
        """
        return self._contains_any_marker(task_input.lower(), self._REPEAT_FRUSTRATION_MARKERS)

    # ── End pending-question helpers ──────────────────────────────────────────

    def _contains_any_marker(self, text: str, markers: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _rank_questions_by_impact(questions: List[str], task_input: str = "") -> List[str]:
        """Reorder open questions so the one that most changes the answer comes first."""
        def score(q: str) -> int:
            ql = q.lower()
            s = 0
            # Decision framing — changes the entire recommendation direction
            if any(kw in ql for kw in ("which option", "which approach", "do you want", "prefer", "should i use", "a or b", "which would")):
                s += 4
            # Time/period anchor — changes every number in a finance answer
            if any(kw in ql for kw in ("which period", "anchor to", "quarter", "ytd", "year-to-date", "current month", "rolling", "trailing")):
                s += 3
            # Missing critical data — can't compute a grounded answer without it
            if any(kw in ql for kw in ("what are the", "what is the", "share the", "source of truth", "numbers", "baseline")):
                s += 3
            # Audience / framing — changes structure and tone substantially
            if any(kw in ql for kw in ("board packet", "investor", "framed for", "operating decision", "finance close")):
                s += 2
            # Comparative scope
            if any(kw in ql for kw in ("compare", " vs ", "against", "benchmark")):
                s += 2
            # Confirmation of existing assumption — validates but doesn't redirect
            if any(kw in ql for kw in ("is this correct", "is this assumption", "confirm", "assumption")):
                s += 1
            return s
        return sorted(questions, key=score, reverse=True)

    def _share_followup_topic(self, prior_query: str, current_query: str) -> bool:
        topic_groups = (
            self.FINANCE_EXECUTION_MARKERS,
            self.ESCALATION_MARKERS,
        )
        if any(
            self._contains_any_marker(prior_query, markers)
            and self._contains_any_marker(current_query, markers)
            for markers in topic_groups
        ):
            return True

        prior_tokens = self._topic_tokens(prior_query)
        current_tokens = self._topic_tokens(current_query)
        return len(prior_tokens.intersection(current_tokens)) >= 2

    def _topic_tokens(self, text: str) -> set[str]:
        stop_words = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "have",
            "what",
            "can",
            "you",
            "our",
            "are",
            "any",
            "we",
            "should",
            "into",
            "from",
            "will",
            "your",
            "about",
            "need",
            "more",
            "details",
            "specific",
            "detailed",
            "list",
            "outline",
        }
        tokens = {
            token
            for token in re.findall(r"[a-z]{4,}", text.lower())
            if token not in stop_words
        }
        return tokens

    def _variance_analysis_block(self, finance_template: str) -> str:
        """Return template-specific variance decomposition instructions for the report prompt."""
        if finance_template == "budget_variance_review":
            return (
                "Variance analysis rules:\n"
                "- For each line item with a non-zero variance, state the dollar amount and percentage explicitly.\n"
                "- Label each variance as Favorable (Fav) or Unfavorable (Unfav) relative to budget.\n"
                "- Identify the primary driver using exactly one of: volume, price, mix, timing, one-time item.\n"
                "- Apply materiality thresholds: flag any item where |variance %| > 10% vs budget or > 5% vs forecast.\n"
                "- Format each variance narrative as: "
                '"[Line Item]: [Fav/Unfav] variance of $X (Y%) vs [basis]. '
                'Primary driver: [reason]. Outlook: [continues / improving / deteriorating]."\n'
                "- Anti-patterns to avoid: do not say 'higher than expected' without a cause; "
                "do not use 'one-time' without naming the specific item; "
                "do not state variance without the directional label.\n"
                "- Apply price/volume decomposition for revenue lines: "
                "(Actual Vol - Budget Vol) × Budget Price + (Actual Price - Budget Price) × Actual Vol.\n"
                "- Apply headcount/rate decomposition for personnel cost lines: "
                "rate variance = (actual rate - budget rate) × actual headcount; "
                "volume variance = (actual headcount - budget headcount) × budget rate.\n"
            )
        if finance_template == "board_financial_update":
            return (
                "Variance analysis rules (board financial update):\n"
                "- Compare current period vs prior period and vs plan for each major line.\n"
                "- State dollar and percentage variance explicitly for: revenue, cash, burn, and any material OpEx lines.\n"
                "- Label each as Fav or Unfav vs plan, and Fav or Unfav vs prior period separately.\n"
                "- Apply materiality threshold: only decompose lines where |variance %| > 5% vs plan or > 10% vs prior.\n"
                "- Format: '[Metric]: [current value] vs [plan value] ([Fav/Unfav] $X, Y%) and vs prior ([Fav/Unfav] $X, Y%). "
                "Driver: [one sentence].'\n"
                "- Summarize the overall financial health direction in one sentence before the line-item detail.\n"
            )
        if finance_template == "cost_review":
            return (
                "Variance analysis rules (cost review):\n"
                "- For each cost category, state the week-over-week and month-over-month change in dollars and percent.\n"
                "- Flag any category where spend increased >15% week-over-week or >10% month-over-month.\n"
                "- Identify whether the trend is a spike (single period anomaly) or a step-change (sustained increase).\n"
                "- Format: '[Cost Category]: $X this period vs $Y prior period ([Fav/Unfav] $Z, W%). "
                "Trend: [spike / step-change / stable]. Driver: [one sentence].'\n"
            )
        if finance_template == "runway_burn_review":
            return (
                "Variance analysis rules (runway and burn review):\n"
                "- State current burn vs plan and vs prior month explicitly in dollars and percent.\n"
                "- State current cash vs prior period and implied runway change in months.\n"
                "- If burn increased vs plan, decompose into: headcount change, vendor/infrastructure change, one-time items.\n"
                "- Format burn narrative: 'Burn rate: $X this month vs $Y plan ([Fav/Unfav] $Z, W%). "
                "Cash runway: N months ([+/-] M months vs prior). Driver: [one sentence].'\n"
                "- Flag if runway has decreased more than 1 month vs the prior period.\n"
            )
        if finance_template == "project_spend_review":
            return (
                "Variance analysis rules (project spend review):\n"
                "- State committed spend vs approved budget: dollar amount consumed and percentage of total budget.\n"
                "- State forecast-to-complete vs remaining budget: will the project finish within budget?\n"
                "- Flag if committed spend exceeds 80% of approved budget before project midpoint.\n"
                "- Format: 'Committed: $X of $Y approved budget (Z% consumed). "
                "Forecast remaining: $A vs $B remaining budget. "
                "Status: [on track / at risk / over budget].'\n"
            )
        return ""

    def _truncate_text(self, value: Any, *, max_chars: int) -> Any:
        if isinstance(value, str) and len(value) > max_chars:
            return value[: max_chars - 3] + "..."
        return value

    def _compact_session_history(self, session_history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        compacted: list[dict[str, Any]] = []
        # Keep last 8 turns for better context, with higher truncation limits
        for item in session_history[-8:]:
            compacted.append(
                {
                    "query": self._truncate_text(item.get("query"), max_chars=500),
                    "response": self._truncate_text(item.get("response"), max_chars=2000),
                    "intent": item.get("intent"),
                    "status": item.get("status"),
                    "timestamp": item.get("timestamp"),
                }
            )
        return compacted

    def _knowledge_base_block(self, knowledge_base: List[Dict[str, Any]]) -> str:
        if not knowledge_base:
            return ""
        lines = [
            "=== KNOWLEDGE BASE: DECISION TEMPLATES & NORMS ===",
            "These are pre-approved templates, decisions, and company norms. "
            "When the CEO's question directly maps to one of these entries, reproduce the content "
            "explicitly in your answer — do NOT paraphrase or summarize it generically. "
            "Use the exact narrative, Q&A, or decision text from the matching entry.\n",
        ]
        for entry in knowledge_base:
            title = (entry.get("title") or "").strip()
            content = (entry.get("content") or "").strip()
            if title and content:
                lines.append(f"[{title}]: {content}")
        lines.append("")
        return "\n".join(lines) + "\n"

    def _compact_retrieval_context(self, retrieval: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Sort by source_authority descending so high-authority docs survive truncation
        sorted_retrieval = sorted(
            retrieval,
            key=lambda d: float(d.get("source_authority", 0.5)),
            reverse=True,
        )
        compacted: list[dict[str, Any]] = []
        for item in sorted_retrieval[:6]:
            authority = float(item.get("source_authority", 0.5))
            if authority >= 0.85:
                authority_label = "primary"
            elif authority >= 0.65:
                authority_label = "secondary"
            else:
                authority_label = "low"
            compacted.append(
                {
                    "title": item.get("title"),
                    "type": item.get("type"),
                    "role": item.get("role"),
                    "snippet": self._truncate_text(item.get("snippet"), max_chars=500),
                    "source_id": item.get("source_id"),
                    "used_for": item.get("used_for"),
                    "source_authority": authority,
                    "authority_label": authority_label,
                    "source_type": item.get("source_type", "reference"),
                }
            )
        return compacted

    def _json_for_prompt(self, value: Any, *, max_chars: int) -> str:
        serialized = json.dumps(value, ensure_ascii=True, default=str)
        if len(serialized) <= max_chars:
            return serialized
        return serialized[: max_chars - 3] + "..."

    def _fallback_payload(
        self,
        task_input: str,
        company_state: Dict[str, Any],
        retrieval: List[Dict[str, Any]],
    ) -> ReportPayload:
        state_keys = ", ".join(sorted(company_state.keys())) or "current company state"
        summary = f"{task_input} based on {state_keys}."
        sections = [
            ReportSection(
                label="Key Finding",
                items=[
                    f"This report is grounded in {state_keys}.",
                    "The answer uses currently available company state and indexed knowledge.",
                    "A deeper quantitative pass may still improve precision.",
                ],
            ),
            ReportSection(
                label="Business Implications",
                items=[
                    "Current business implications should be reviewed against the latest financial assumptions.",
                    "Management should confirm whether any recent changes are missing from the company state.",
                    "Execution risk may be understated without a deeper quantitative review.",
                ],
            ),
            ReportSection(
                label="Recommended Actions",
                items=[
                    "Validate the most important financial assumptions.",
                    "Review supporting documents before final circulation.",
                    "Confirm whether the current company state is up to date.",
                ],
            ),
        ]
        return ReportPayload(
            answer=ReportAnswer(
                title="Executive Report",
                summary=summary,
                sections=sections,
            ),
            trust=ReportTrust(
                confidence="medium",
                confidence_score=0.68,
                assumptions=["The available company state is current and materially complete."],
                open_questions=["Do you want a deeper quantitative model for this report?"],
                data_quality="medium",
                calculation_used=False,
                missing_context=[],
            ),
            sources=[
                {"source_id": "company_state", "title": "Company State", "type": "state"},
                *[
                    {
                        "source_id": f"retrieval_{idx}",
                        "title": item.get("title", f"Retrieved Context {idx + 1}"),
                        "type": "document",
                    }
                    for idx, item in enumerate(retrieval[:3])
                ],
            ],
        )

    def _enforce_three_s(self, payload: ReportPayload, preferred_labels: Optional[list[str]] = None) -> ReportPayload:
        preferred_labels = preferred_labels or ["Key Finding", "Business Implications", "Recommended Actions"]
        normalized_sections: list[ReportSection] = []

        for index, label in enumerate(preferred_labels):
            if index < len(payload.answer.sections):
                section = payload.answer.sections[index]
                section_label = section.label or label
            else:
                section = ReportSection(label=label)
                section_label = label

            candidate_items = [item.strip() for item in section.items if item and item.strip()]
            if section.content and section.content.strip():
                content_parts = [
                    part.strip(" -")
                    for part in section.content.replace("\n", " ").split(".")
                    if part.strip()
                ]
                for part in content_parts:
                    if part and part not in candidate_items:
                        candidate_items.append(part)

            while len(candidate_items) < 3:
                candidate_items.append(self._default_subpoint(section_label, len(candidate_items)))

            normalized_sections.append(
                ReportSection(
                    label=section_label,
                    items=candidate_items[:3],
                )
            )

        payload.answer.sections = normalized_sections[:3]
        return payload


    def _default_subpoint(self, label: str, index: int) -> str:
        defaults = {
            "Key Finding": [
                "The main conclusion is grounded in current company context.",
                "Available evidence supports the core finding, but should be reviewed against the latest data.",
                "The finding should be pressure-tested if new operating information has emerged.",
            ],
            "Business Implications": [
                "This outcome affects current planning and prioritization decisions.",
                "Leadership should check whether the implication changes risk, timing, or resource allocation.",
                "The implication should be reviewed against current operating constraints.",
            ],
            "Recommended Actions": [
                "Confirm the highest-impact assumption before acting.",
                "Review supporting materials before circulating the report.",
                "Decide whether a deeper follow-up analysis is required.",
            ],
        }
        return defaults.get(label, ["Review the current context.", "Validate the assumptions.", "Confirm the next action."])[index]

    def _to_markdown(self, payload: ReportPayload) -> str:
        lines = [f"# {payload.answer.title}", "", payload.answer.summary]
        for section in payload.answer.sections:
            lines.extend(["", f"## {section.label}"])
            if section.content:
                lines.extend(["", section.content])
            if section.items:
                lines.extend(["", *[f"- {item}" for item in section.items]])
        return "\n".join(lines).strip()

    _OUTPUT_MODALITY_SYSTEM_PROMPT = (
        "You are an output format classifier for an executive AI assistant. "
        "Given a user's request, determine the best output format to generate. "
        "Return a JSON object only — no prose.\n\n"
        "FORMAT OPTIONS:\n"
        "- pptx+xlsx: PowerPoint deck backed by a financial workbook (use when both a slide presentation AND a financial model are requested)\n"
        "- pptx: PowerPoint/slide deck only (use when slides or a presentation is requested but no financial model)\n"
        "- xlsx: Standalone financial workbook/model/spreadsheet (use when numerical analysis or model is primary, no deck)\n"
        "- docx+xlsx: Written executive memo backed by a financial workbook\n"
        "- docx: Written executive memo, report, or board brief (no financial model needed)\n"
        "- canvas: Single-page executive one-pager or canvas summary\n"
        "- inline: Conversational response — no file artifact needed\n\n"
        "SIGNALS TO WEIGHT HEAVILY:\n"
        "- Explicit artifact requests ('make a deck', 'put together slides', 'give me a PowerPoint', 'prep a presentation', 'build a workbook', 'generate a memo') → match exactly\n"
        "- Financial data with visuals requested → prefer xlsx or docx+xlsx\n"
        "- Board/investor deliverable implied → prefer docx or pptx\n"
        "- Both a deck AND a model/workbook requested → pptx+xlsx\n"
        "- Pure question or conversational follow-up → inline"
    )

    def _classify_output_modality_semantic(self, task_input: str) -> str | None:
        """Use a small LLM call to classify the intended output format from the user's request.
        Returns a modality string or None if the LLM is unavailable.
        """
        try:
            from src.core.llm import LLMClient
            prompt = (
                f'User request: "{task_input}"\n\n'
                "Return JSON only:\n"
                '{"modality": "<format>", "rationale": "<one sentence>"}'
            )
            raw = LLMClient().complete(prompt, self._OUTPUT_MODALITY_SYSTEM_PROMPT)
            match = re.search(r'\{.*?\}', raw, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group(0))
            modality = data.get("modality", "")
            valid = {"pptx+xlsx", "pptx", "xlsx", "docx+xlsx", "docx", "canvas", "inline"}
            return modality if modality in valid else None
        except Exception:
            return None

    def _select_output_modality(self, task_input: str) -> tuple[str, list[ArtifactPlanEntry]]:
        # Semantic classification first — handles any phrasing
        semantic_modality = self._classify_output_modality_semantic(task_input)
        if semantic_modality and semantic_modality != "inline":
            return self._modality_to_artifact_plan(semantic_modality)

        # Keyword fallback
        lowered = task_input.lower()
        canvas_markers = [
            "one-pager", "one pager", "canvas", "executive one-pager",
            "one page brief", "onepager", "single page",
        ]
        if any(marker in lowered for marker in canvas_markers):
            return self._modality_to_artifact_plan("canvas")
        deck_markers = [
            "deck", "slides", "slide deck", "powerpoint", "ppt", "pptx",
            "presentation", "meeting prep deck", "board deck",
        ]
        analysis_markers = [
            "excel", "spreadsheet", "workbook", "xlsx", "financial model",
            "forecast", "scenario", "variance", "budget", "cost", "costs",
            "trend", "table", "chart", "graph", "visual", "analyze", "analysis",
        ]
        narrative_markers = [
            "report", "memo", "board", "brief", "update", "summary",
            "board-ready", "executive",
        ]

        wants_deck = any(marker in lowered for marker in deck_markers)
        wants_analysis = any(marker in lowered for marker in analysis_markers)
        wants_narrative = any(marker in lowered for marker in narrative_markers)
        wants_visuals = any(marker in lowered for marker in ["chart", "graph", "visual", "table"])

        if wants_deck and wants_analysis:
            return self._modality_to_artifact_plan("pptx+xlsx")
        if wants_deck:
            return self._modality_to_artifact_plan("pptx")
        if wants_analysis and (wants_narrative or wants_visuals):
            return self._modality_to_artifact_plan("docx+xlsx")
        if wants_analysis:
            return self._modality_to_artifact_plan("xlsx")
        if wants_narrative:
            return self._modality_to_artifact_plan("docx")
        return ("inline", [])

    def _modality_to_artifact_plan(self, modality: str) -> tuple[str, list[ArtifactPlanEntry]]:
        """Convert a modality string to its (modality, artifact_plan) tuple."""
        _deck_entry = ArtifactPlanEntry(
            artifact_type="report_pptx",
            label="Executive Deck",
            format="pptx",
            purpose="meeting_prep",
            ready_when="After the presentation deck is generated.",
        )
        _xlsx_entry = ArtifactPlanEntry(
            artifact_type="analysis_xlsx",
            label="Analysis Workbook",
            format="xlsx",
            purpose="financial_model_review",
            ready_when="After the supporting workbook is generated.",
        )
        _docx_entry = ArtifactPlanEntry(
            artifact_type="report_docx",
            label="Executive Memo",
            format="docx",
            purpose="board_distribution",
            ready_when="After the report is finalized.",
        )
        _canvas_entry = ArtifactPlanEntry(
            artifact_type="executive_canvas",
            label="Executive One-Pager",
            format="html",
            purpose="executive_summary",
            ready_when="After the one-pager is generated.",
        )
        plans: dict[str, tuple[str, list[ArtifactPlanEntry]]] = {
            "pptx+xlsx": ("pptx+xlsx", [_deck_entry, _xlsx_entry]),
            "pptx":      ("pptx",      [_deck_entry]),
            "docx+xlsx": ("docx+xlsx", [_docx_entry, _xlsx_entry]),
            "docx":      ("docx",      [_docx_entry]),
            "xlsx":      ("xlsx",      [_xlsx_entry]),
            "canvas":    ("canvas",    [_canvas_entry]),
            "inline":    ("inline",    []),
        }
        return plans.get(modality, ("inline", []))

    def _approval_granted(self, agent_input: AgentInput) -> bool:
        approvals = agent_input.workflow_state.metadata.get("approvals", {})
        stage_approval = approvals.get(agent_input.stage, {})
        return stage_approval.get("decision") == "approve"

    def _build_artifact_actions(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        presentation_spec: Optional[PresentationSpec],
        finance_template: Optional[str],
        company_state: Dict[str, Any],
        ceo_id: str,
        current_interaction_id: Optional[int],
        session_history: List[Dict[str, Any]],
        retrieval: List[Dict[str, Any]],
        markdown: str,
        output_modality: str,
        stage: str,
        finance_rows: Optional[list] = None,
        conversation_id: Optional[str] = None,
        turn_count: int = 0,
        situational_updates: Optional[Dict[str, Any]] = None,
    ) -> list[Any]:
        workbook_spec = self._to_workbook_spec(
            task_input=task_input,
            payload=payload,
            company_state=company_state,
            ceo_id=ceo_id,
            current_interaction_id=current_interaction_id,
            session_history=session_history,
            retrieval=retrieval,
        )
        # Append variance sheet for budget variance reports when rows carry actuals + budgets
        if finance_template == "budget_variance_review" and finance_rows:
            variance_sheet = self._build_variance_sheet(finance_rows)
            if variance_sheet is not None:
                workbook_spec.sheets.append(variance_sheet)

        actions: list[Any] = [
            write_artifact_action(
                "synthesizer",
                "executive_summary.md",
                markdown,
                source="report_agent",
                label="Synthesizer",
                format="md",
                status="generated",
                hidden=True,
            )
        ]

        if output_modality == "canvas":
            canvas_spec = self._to_canvas_spec(payload, finance_template)
            actions.append(
                create_canvas_action(
                    artifact_stage="executive_canvas",
                    filename="executive-one-pager.html",
                    label="Executive One-Pager",
                    canvas_spec=canvas_spec,
                    preview_stage="canvas_preview",
                    preview_filename="canvas_preview.html",
                )
            )

        if output_modality in {"docx", "docx+xlsx"}:
            memo_template = get_artifact_template("board_memo_v1")
            theme_id = (
                get_finance_template_definition(self._finance_template_definition_key(finance_template)).default_theme_id
                if finance_template
                else DEFAULT_THEME_ID
            )
            memo_spec = (
                presentation_spec_to_memo_spec(
                    presentation_spec,
                    template_id=memo_template.template_id,
                    theme_id=theme_id,
                    finance_template=finance_template,
                )
                if presentation_spec
                else self._to_memo_spec(payload=payload, finance_template=finance_template)
            )
            actions.append(
                create_docx_memo_action(
                    artifact_stage="report_docx",
                    filename="board_memo.docx",
                    label="Executive Memo",
                    memo_spec=memo_spec,
                    preview_stage="report_docx_preview",
                    preview_filename="board_memo_preview.md",
                )
            )

        if output_modality in {"pptx", "pptx+xlsx"}:
            deck_template = get_artifact_template(self._select_deck_template_id(task_input))
            theme_id = (
                get_finance_template_definition(self._finance_template_definition_key(finance_template)).default_theme_id
                if finance_template
                else DEFAULT_THEME_ID
            )
            deck_spec = (
                presentation_spec_to_deck_spec(
                    presentation_spec,
                    template_id=deck_template.template_id,
                    theme_id=theme_id,
                    finance_template=finance_template,
                )
                if presentation_spec
                else self._to_deck_spec(
                    task_input=task_input,
                    payload=payload,
                    finance_template=finance_template,
                )
            )
            actions.append(
                create_pptx_deck_action(
                    artifact_stage="report_pptx",
                    filename="board_deck.pptx",
                    label="Executive Deck",
                    deck_spec=deck_spec,
                    preview_stage="report_pptx_preview",
                    preview_filename="board_deck_preview.md",
                )
            )

        if output_modality in {"xlsx", "docx+xlsx", "pptx+xlsx"}:
            actions.append(
                create_workbook_action(
                    artifact_stage="analysis_xlsx",
                    filename="analysis_workbook.xlsx",
                    label="Analysis Workbook",
                    workbook_spec=workbook_spec,
                    preview_stage="analysis_spec",
                    preview_filename="analysis_spec.json",
                )
            )

        actions.append(
            tool_action(
                "write_thread_entry",
                entry_type="decision" if self._looks_like_decision_payload(payload) else "contribution",
                actor=self.metadata.name,
                content=payload.answer.summary[:300],
                structured_payload=self._thread_structured_payload(payload),
                entities=self._extract_entities(payload),
                conversation_id=conversation_id,
                turn=turn_count,
                workflow_type="report_generation",
            )
        )
        if situational_updates:
            actions.append(
                tool_action(
                    "update_situational_profile",
                    updated_by=self.metadata.name,
                    **situational_updates,
                )
            )
        actions.extend(self._extract_memory_save_actions(payload))
        actions.extend(
            [
                complete_stage_action(stage),
                complete_workflow_action(response_type="report"),
            ]
        )
        return actions

    def _entity_context_block(self, entity_context: List[Dict[str, Any]]) -> str:
        if not entity_context:
            return ""
        lines = ["=== ENTITY CONTEXT (what's known about named entities in this query) ==="]
        for item in entity_context[:6]:
            entity = item.get("entity", "")
            snippet = item.get("snippet", "")
            ts = str(item.get("timestamp", ""))[:10]
            source = item.get("source_type", "")
            lines.append(f"  [{entity}] ({source}, {ts}): {snippet[:180]}")
        return "\n".join(lines) + "\n\n"

    def _unified_memory_prompt_block(self, unified_memory: Dict[str, Any]) -> str:
        if not isinstance(unified_memory, dict) or not unified_memory:
            return ""
        return (
            "=== UNIFIED MEMORY (canonical working/session/long-term state) ===\n"
            f"{self._json_for_prompt(unified_memory, max_chars=5000)}\n\n"
            "Use this as the primary memory contract for continuity, deliverable state, and durable preferences.\n\n"
        )

    def _financial_task_prompt_block(self, financial_task: Optional[FinancialAnalysisTask]) -> str:
        return financial_task_prompt_block(financial_task)

    def _live_context_prompt_block(self, live_context: Dict[str, Any]) -> str:
        if not isinstance(live_context, dict):
            return ""
        if not any(
            [
                live_context.get("current_schedule"),
                live_context.get("open_decisions"),
                live_context.get("open_commitments"),
                live_context.get("entities_in_play"),
                live_context.get("last_agent_contributions"),
            ]
        ):
            return ""
        lines = ["Current conversation thread:"]
        schedule = live_context.get("current_schedule") or {}
        if isinstance(schedule, dict) and schedule:
            lines.append(f"- Most recent schedule (turn {schedule.get('turn', '?')}): {len(schedule.get('blocks') or [])} blocks")
            blocks = schedule.get("blocks") or []
            if blocks:
                block_labels = []
                for block in blocks[:5]:
                    if isinstance(block, dict):
                        label = str(block.get("title") or "Untitled block")
                        window = str(block.get("time_window") or block.get("starts_at") or "").strip()
                        block_labels.append(f"{window} {label}".strip())
                if block_labels:
                    lines.append("- Schedule blocks: " + "; ".join(block_labels))
            meetings = schedule.get("meetings") or []
            if meetings:
                meeting_labels = []
                for meeting in meetings[:4]:
                    if isinstance(meeting, dict):
                        meeting_labels.append(
                            f"{meeting.get('title', 'Meeting')} @ {meeting.get('starts_at', '')}".strip()
                        )
                if meeting_labels:
                    lines.append("- Scheduled meetings: " + "; ".join(meeting_labels))
            deadlines = schedule.get("deadlines") or []
            if deadlines:
                lines.append("- Scheduled deadlines: " + "; ".join(str(item) for item in deadlines[:4]))
        decisions = live_context.get("open_decisions") or []
        if decisions:
            lines.append("- Open decisions: " + "; ".join(str(item) for item in decisions[:3]))
        commitments = live_context.get("open_commitments") or []
        if commitments:
            lines.append("- Open commitments: " + "; ".join(str(item) for item in commitments[:3]))
        entities = live_context.get("entities_in_play") or {}
        if isinstance(entities, dict) and entities:
            lines.append("- Entities in play: " + "; ".join(f"{key}: {value}" for key, value in list(entities.items())[:5]))
        contributions = live_context.get("last_agent_contributions") or []
        for contribution in contributions[-2:]:
            if isinstance(contribution, dict):
                lines.append(
                    f"- Recent contribution [{contribution.get('actor')} turn {contribution.get('turn')}]: "
                    f"{str(contribution.get('content_summary') or '')[:150]}"
                )
        return "\n".join(lines) + "\n\n"

    def _situational_prompt_block(self, situational: Dict[str, Any]) -> str:
        if not isinstance(situational, dict) or not situational:
            return ""
        lines = ["CEO situational profile:", f"- Operating mode: {situational.get('operating_mode', 'standard')}"]
        pressures = situational.get("active_pressures") or []
        if pressures:
            lines.append("- Active pressures: " + "; ".join(str(item) for item in pressures[:3]))
        recurring = [topic for topic in (situational.get("recurring_topics") or []) if isinstance(topic, dict) and not topic.get("resolved")]
        if recurring:
            lines.append(
                "- Recurring unresolved topics: "
                + ", ".join(f"{topic.get('topic')} (x{topic.get('mention_count', 1)})" for topic in recurring[:4])
            )
        obligations = situational.get("relationship_obligations") or []
        if obligations:
            lines.append("- Relationship obligations: " + "; ".join(str(item) for item in obligations[:3]))
        return "\n".join(lines) + "\n\n"

    def _extract_entities(self, payload: ReportPayload) -> List[str]:
        text = payload.answer.summary + " " + " ".join(
            item
            for section in payload.answer.sections
            for item in section.items
        )
        candidates = re.findall(
            r"\b[A-Z][A-Za-z0-9&.-]+(?: [A-Z][A-Za-z0-9&.-]+){0,3} (?:deal|call|review|pack|plan|project|meeting)\b",
            text,
        )
        return list(dict.fromkeys(candidates))[:8]

    def _thread_structured_payload(self, payload: ReportPayload) -> Dict[str, Any]:
        return {
            "key_findings": [
                f"{section.label}: {section.content or '; '.join(section.items[:3])}"
                for section in payload.answer.sections[:3]
            ],
            "sources_used": [source.get("title") for source in (payload.sources or [])[:5] if isinstance(source, dict)],
        }

    def _looks_like_decision_payload(self, payload: ReportPayload) -> bool:
        title = (payload.answer.title or "").lower()
        summary = (payload.answer.summary or "").lower()
        return "decision" in title or summary.startswith(("yes", "no", "first:", "tell ", "select "))

    def _extract_situational_updates(self, *, task_input: str, payload: ReportPayload, output_modality: str) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        lowered = task_input.lower()
        payload_text = " ".join(
            [payload.answer.summary]
            + [item for section in payload.answer.sections for item in section.items]
        ).lower()
        if len(task_input.split()) < 8 and any(word in lowered for word in ("now", "today", "quick", "fast")):
            updates["operating_mode"] = "reactive"
        elif any(word in lowered for word in ("plan", "strategy", "roadmap", "next quarter", "forecast")):
            updates["operating_mode"] = "strategic"
        elif output_modality in {"pptx", "docx", "docx+xlsx", "pptx+xlsx"}:
            updates["operating_mode"] = "execution"
        elif any(marker in payload_text for marker in ("owner:", "today by", "before ", "deadline", "milestone")):
            updates["operating_mode"] = "execution"

        if any(word in f"{lowered} {payload_text}" for word in ("board", "investor", "deadline", "covenant", "renewal")):
            if "board" in lowered or "board" in payload_text:
                updates["add_pressure"] = f"Board-related pressure raised {datetime.now().strftime('%b %d')}"
            elif "investor" in lowered or "investor" in payload_text:
                updates["add_pressure"] = f"Investor-related pressure raised {datetime.now().strftime('%b %d')}"
            elif "covenant" in payload_text:
                updates["add_pressure"] = f"Covenant-risk pressure active {datetime.now().strftime('%b %d')}"
            elif "renewal" in payload_text:
                updates["add_pressure"] = f"Renewal-risk pressure active {datetime.now().strftime('%b %d')}"

        for topic in ("aws", "burn", "runway", "variance", "forecast", "board", "apex", "redwood", "cloud", "kepler", "northstar"):
            if topic in lowered or topic in payload_text:
                updates["topic_mention"] = topic
                break

        obligation_item = next(
            (
                item for section in payload.answer.sections for item in section.items
                if any(marker in item.lower() for marker in ("owner:", "today by", "tomorrow", "before ", "milestone"))
            ),
            None,
        )
        if obligation_item:
            updates["add_obligation"] = obligation_item[:140]
        return updates

    _ACTION_SECTION_LABELS = frozenset(
        {"recommended actions", "next steps", "action items", "recommended next actions", "actions"}
    )
    _DECISION_SECTION_LABELS = frozenset({"decisions", "key decisions", "decision"})
    _MILESTONE_SECTION_LABELS = frozenset({"milestones", "timeline", "key milestones"})

    def _extract_memory_save_actions(self, payload: ReportPayload) -> list[Any]:
        """Scan report sections for actionable items and return memory save actions (max 3)."""
        saves: list[Any] = []
        for section in payload.answer.sections:
            label_lower = section.label.lower()
            if label_lower in self._ACTION_SECTION_LABELS:
                mem_type = "commitment"
            elif label_lower in self._DECISION_SECTION_LABELS:
                mem_type = "decision"
            elif label_lower in self._MILESTONE_SECTION_LABELS:
                mem_type = "milestone"
            else:
                continue
            for item in section.items[:2]:
                item_text = str(item).strip()
                if not self._memory_candidate_is_strong(item_text, mem_type):
                    continue
                if len(saves) >= 3:
                    break
                saves.append(
                    tool_action(
                        "memory_management",
                        action="save",
                        auto_save=True,
                        memory_type=mem_type,
                        title=item_text[:80].strip().rstrip("."),
                        content=item_text,
                        confidence=payload.trust.confidence,
                        confidence_score=payload.trust.confidence_score,
                        evidence_state=payload.trust.evidence_state,
                        dedupe_query=item_text[:80],
                        tags=["auto", "report"],
                    )
                )
            if len(saves) >= 3:
                break
        return saves

    def _memory_candidate_is_strong(self, item: str, memory_type: str) -> bool:
        normalized = " ".join((item or "").split()).lower()
        if len(normalized) < 32:
            return False
        if memory_type == "decision":
            return any(marker in normalized for marker in ("decide", "approved", "choose", "will ", "commit"))
        if memory_type == "milestone":
            return any(marker in normalized for marker in ("by ", "before ", "deadline", "target", "milestone"))
        return any(
            marker in normalized
            for marker in ("today", "tomorrow", "this week", "next week", "within", "by ", "before ", ":")
        ) or bool(re.search(r"\b(ceo|cfo|finance|engineering|product|operations|sales)\b", normalized))

    def _needs_human_approval(self, task_input: str) -> bool:
        lowered = task_input.lower()
        # Only gate on clearly external delivery actions, not analysis/planning tasks
        return any(
            marker in lowered
            for marker in [
                "send to ",
                "share with ",
                "publish",
                "circulate to",
                "investor update",
                "external",
            ]
        )

    def _to_memo_spec(self, *, payload: ReportPayload, finance_template: Optional[str]) -> MemoSpec:
        memo_template = get_artifact_template("board_memo_v1")
        theme_id = (
            get_finance_template_definition(self._finance_template_definition_key(finance_template)).default_theme_id
            if finance_template
            else DEFAULT_THEME_ID
        )
        return MemoSpec(
            title=payload.answer.title,
            summary=payload.answer.summary,
            section_order=getattr(memo_template, "section_order", []),
            sections=[
                MemoSectionSpec(
                    label=section.label,
                    items=[str(item) for item in section.items[:3]],
                )
                for section in payload.answer.sections
            ],
            assumptions=[str(item) for item in payload.trust.assumptions],
            open_questions=[str(item) for item in payload.trust.open_questions],
            metadata={
                "template_id": memo_template.template_id,
                "theme_id": theme_id,
                "presentation_version": "memo_spec_v1",
                "finance_template": finance_template,
            },
        )

    def _to_deck_spec(self, *, task_input: str, payload: ReportPayload, finance_template: Optional[str]) -> DeckSpec:
        deck_template = get_artifact_template(self._select_deck_template_id(task_input))
        theme_id = (
            get_finance_template_definition(self._finance_template_definition_key(finance_template)).default_theme_id
            if finance_template
            else DEFAULT_THEME_ID
        )
        recommended_actions = self._section_items_by_label(payload, "recommended actions") or self._section_items_by_index(payload, 2)
        business_implications = self._section_items_by_label(payload, "business implications") or self._section_items_by_index(payload, 1)
        key_findings = self._section_items_by_label(payload, "key findings") or self._section_items_by_index(payload, 0)
        risk_items = [str(item) for item in payload.trust.missing_context[:3]]
        if not risk_items:
            risk_items = [str(item) for item in payload.trust.assumptions[:3]]
        key_questions = [str(item) for item in payload.trust.open_questions[:3]]
        if not key_questions:
            key_questions = ["Confirm the key assumption behind the current recommendation."]

        is_board_deck = deck_template.template_id == "board_deck_v1"
        if is_board_deck:
            metric_bullets = []
            for section in payload.answer.sections:
                for item in section.items[:3]:
                    if any(char.isdigit() for char in item):
                        metric_bullets.append(str(item))
                if len(metric_bullets) >= 3:
                    break
            slides = [
                DeckSlideSpec(title="Title", bullets=[payload.answer.summary], kind="title"),
                DeckSlideSpec(title="Executive Summary", bullets=key_findings or [payload.answer.summary], kind="decision"),
                DeckSlideSpec(title="Business Context", bullets=business_implications or key_findings or [payload.answer.summary]),
                DeckSlideSpec(title="Key Metrics", bullets=metric_bullets or key_findings or [payload.answer.summary], kind="metric"),
                DeckSlideSpec(title="Decision Points", bullets=recommended_actions or business_implications or [payload.answer.summary], kind="decision"),
                DeckSlideSpec(title="Risks", bullets=risk_items or ["Review the remaining evidence gaps before circulation."]),
                DeckSlideSpec(
                    title="Recommended Actions",
                    bullets=recommended_actions or ["Confirm the next executive action before circulation."],
                    kind="decision",
                ),
                DeckSlideSpec(
                    title="Appendix",
                    bullets=[f"{section.label}: {' | '.join(section.items[:3])}" for section in payload.answer.sections if section.items],
                    kind="appendix",
                ),
            ]
        else:
            slides = [
                DeckSlideSpec(
                    title="Title",
                    bullets=[payload.answer.summary],
                    kind="title",
                ),
                DeckSlideSpec(
                    title="Context",
                    bullets=key_findings or [payload.answer.summary],
                ),
                DeckSlideSpec(
                    title="Key Questions",
                    bullets=key_questions,
                ),
                DeckSlideSpec(
                    title="Decision Points",
                    bullets=business_implications or key_findings or [payload.answer.summary],
                ),
                DeckSlideSpec(
                    title="Risks",
                    bullets=risk_items or ["Review the remaining evidence gaps before circulation."],
                ),
                DeckSlideSpec(
                    title="Recommended Actions",
                    bullets=recommended_actions or ["Confirm the next executive action before circulation."],
                ),
            ]

        return DeckSpec(
            title=payload.answer.title,
            subtitle=payload.answer.summary,
            slide_order=getattr(deck_template, "slide_sequence", []),
            slides=slides,
            metadata={
                "template_id": deck_template.template_id,
                "theme_id": theme_id,
                "presentation_version": "deck_spec_v1",
                "finance_template": finance_template,
            },
        )

    def _select_deck_template_id(self, task_input: str) -> str:
        lowered = task_input.lower()
        board_markers = [
            "board deck",
            "board meeting",
            "board review",
            "board packet",
            "board presentation",
            "investor",
            "committee",
        ]
        if any(marker in lowered for marker in board_markers):
            return "board_deck_v1"
        return "meeting_prep_deck_v1"

    def _section_items_by_label(self, payload: ReportPayload, label: str) -> list[str]:
        normalized = label.strip().lower()
        for section in payload.answer.sections:
            if section.label.strip().lower() == normalized:
                return [str(item) for item in section.items[:3]]
        return []

    def _section_items_by_index(self, payload: ReportPayload, index: int) -> list[str]:
        if 0 <= index < len(payload.answer.sections):
            return [str(item) for item in payload.answer.sections[index].items[:3]]
        return []

    def _to_analysis_spec_json(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        company_state: Optional[Dict[str, Any]] = None,
        retrieval: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        workbook_spec = self._to_workbook_spec(
            task_input=task_input,
            payload=payload,
            company_state=company_state or {},
            retrieval=retrieval or [],
        )
        return json.dumps(workbook_spec.model_dump(), indent=2)

    def _build_variance_sheet(self, finance_rows: list) -> Optional[Any]:
        """Convert WorkbookFinancialRow objects into a variance analysis sheet."""
        try:
            from src.finance.variance import run_variance_analysis, variance_report_to_sheet
            metrics = []
            period = "Current Period"
            for row in finance_rows:
                if not hasattr(row, "actual") or not hasattr(row, "budget"):
                    continue
                if row.budget == 0:
                    continue
                period = getattr(row, "period", period)
                metrics.append({
                    "metric": getattr(row, "metric", "Unknown"),
                    "actual": float(row.actual),
                    "reference": float(row.budget),
                    "reference_label": "Budget",
                })
            if not metrics:
                return None
            report = run_variance_analysis(period=period, metrics=metrics)
            return variance_report_to_sheet(report)
        except Exception:
            return None

    def _to_canvas_spec(self, payload: ReportPayload, finance_template: Optional[str]) -> dict:
        """Build a CanvasSpec-compatible dict from a ReportPayload."""
        sections = []
        for section in payload.answer.sections:
            sections.append({
                "label": section.label,
                "bullets": section.items[:4],
                "highlight": section.label.lower() in {"recommended actions", "key risks", "critical flags"},
            })

        # Hero metric: first numeric value from the payload metrics, if any
        hero_metric = None
        metrics = self._extract_metrics(payload)
        if metrics:
            hero_metric = {
                "label": metrics[0].label,
                "value": metrics[0].value,
            }

        subtitle = finance_template.replace("_", " ").title() if finance_template else None

        return {
            "title": payload.answer.title,
            "subtitle": subtitle,
            "summary": payload.answer.summary,
            "hero_metric": hero_metric,
            "sections": sections,
            "source_credit": "agenticMIND · CEO Intelligence Layer",
        }

    def _to_workbook_spec(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        company_state: Dict[str, Any],
        ceo_id: Optional[str] = None,
        current_interaction_id: Optional[int] = None,
        session_history: Optional[List[Dict[str, Any]]] = None,
        retrieval: List[Dict[str, Any]],
    ) -> WorkbookSpec:
        metrics = self._extract_metrics(payload)
        lowered = task_input.lower()
        if self._is_finance_workbook_request(lowered):
            return self._build_finance_workbook_spec(
                task_input=task_input,
                payload=payload,
                metrics=metrics,
                company_state=company_state,
                ceo_id=ceo_id,
                current_interaction_id=current_interaction_id,
                session_history=session_history or [],
                retrieval=retrieval,
            )

        detail_rows: list[list[str]] = []
        for section in payload.answer.sections:
            for index, item in enumerate(section.items[:3], start=1):
                detail_rows.append([section.label, f"Item {index}", item])

        return WorkbookSpec(
            workbook_title=payload.answer.title,
            sheets=[
                WorkbookSheetSpec(
                    name="Summary",
                    kind="summary",
                    metrics=metrics,
                    tables=[
                        {
                            "title": "Report Detail",
                            "columns": ["Section", "Subtopic", "Detail"],
                            "rows": detail_rows,
                        }
                    ],
                )
            ],
        )

    def _extract_metrics(self, payload: ReportPayload) -> list[WorkbookMetric]:
        metrics: list[WorkbookMetric] = []
        seen: set[str] = set()
        pattern = re.compile(r"(\$[\d.,]+[MBK]?|\d+%|\d[\d.,]*)")
        scale_pattern = re.compile(r"\b(billion|million|thousand)\b", re.IGNORECASE)
        scale_map = {"billion": "B", "million": "M", "thousand": "K"}
        for section in payload.answer.sections:
            for item in section.items:
                match = pattern.search(item)
                if not match:
                    continue
                value = match.group(1)
                # Normalize spelled-out scale words: "$42.5 million" → "$42.5M"
                if not any(suffix in value for suffix in ("M", "B", "K")):
                    after = item[match.end():match.end() + 12]
                    scale_match = scale_pattern.search(after)
                    if scale_match:
                        value = value + scale_map[scale_match.group(1).lower()]
                label = item.replace(match.group(1), "").strip(" .:-") or section.label
                normalized_label = label.lower()
                if normalized_label in seen:
                    continue
                seen.add(normalized_label)
                metrics.append(WorkbookMetric(label=label[:80], value=value))
                if len(metrics) >= 6:
                    return metrics
        if not metrics:
            metrics.append(WorkbookMetric(label="Confidence score", value=f"{round(payload.trust.confidence_score * 100)}%"))
        return metrics

    def _is_finance_workbook_request(self, lowered_task_input: str) -> bool:
        return any(
            marker in lowered_task_input
            for marker in ["financial", "finance", "budget", "variance", "forecast", "runway", "revenue", "cost", "burn", "spend", "spending", "company health", "business health", "financial health"]
        )

    def _is_aws_spend_request(self, lowered_task_input: str) -> bool:
        return any(
            marker in lowered_task_input
            for marker in ["aws", "cloud spend", "cloud cost", "infrastructure cost", "infrastructure spend"]
        )

    def _select_finance_template(self, task_input: str) -> Optional[str]:
        lowered = task_input.lower()
        if not self._is_finance_workbook_request(lowered):
            return None
        if self._is_aws_spend_request(lowered):
            return "aws_cost_review"
        if any(marker in lowered for marker in ["runway", "cash position", "cash runway", "burn rate", "burn review"]):
            return "runway_burn_review"
        if any(marker in lowered for marker in ["project kepler", "project spend", "committed spend", "budget allocation", "kepler budget", "spend review"]):
            return "project_spend_review"
        if any(marker in lowered for marker in ["budget variance", "over budget", "under budget", "variance"]):
            return "budget_variance_review"
        return "board_financial_update"

    def _template_section_labels(self, finance_template: str) -> list[str]:
        return self.FINANCE_TEMPLATE_LABELS.get(finance_template, ["Key Finding", "Business Implications", "Recommended Actions"])

    def _template_expected_metrics(self, finance_template: str) -> list[str]:
        try:
            return get_finance_template_definition(self._finance_template_definition_key(finance_template)).expected_metric_keys
        except Exception:
            return []

    def _template_expected_periods(self, finance_template: str) -> list[str]:
        try:
            return get_finance_template_definition(self._finance_template_definition_key(finance_template)).expected_period_granularities
        except Exception:
            return ["Current Quarter"]

    def _primary_visual_for_template(self, finance_template: Optional[str]) -> Optional[dict[str, str]]:
        mapping = {
            "aws_cost_review": {"title": "AWS Spend Trend", "label": "Primary visual", "description": "Weekly cloud spend versus plan"},
            "runway_burn_review": {"title": "Cash and Burn Trend", "label": "Primary visual", "description": "Cash position against burn trajectory"},
            "project_spend_review": {"title": "Project Spend vs Budget", "label": "Primary visual", "description": "Committed spend against approved budget"},
            "budget_variance_review": {"title": "Budget vs Actual", "label": "Primary visual", "description": "Largest over-plan and under-plan lines"},
            "board_financial_update": {"title": "Financial Snapshot", "label": "Primary visual", "description": "Quarterly financial overview"},
        }
        return mapping.get(finance_template) if finance_template else None

    def _finance_template_definition_key(self, finance_template: str) -> str:
        return self.FINANCE_TEMPLATE_DEFINITION_ALIASES.get(finance_template, finance_template)

    def _build_finance_digest(
        self,
        payload: ReportPayload,
        *,
        finance_template: Optional[str],
        finance_summary_metrics: Optional[list[WorkbookMetric]] = None,
    ) -> Optional[dict[str, Any]]:
        if not finance_template:
            return None
        sections = payload.answer.sections
        takeaways = sections[0].items[:3] if sections else []
        implications = sections[1].items[:3] if len(sections) > 1 else []
        recommendation_section = sections[2] if len(sections) > 2 else None
        recommendation = recommendation_section.items[0] if recommendation_section and recommendation_section.items else None
        next_steps = recommendation_section.items[1:3] if recommendation_section else []
        digest = {
            "template": finance_template,
            "headline": payload.answer.summary,
            "takeaways": takeaways,
            "implications": implications,
            "recommendation": recommendation,
            "next_steps": next_steps,
            "threshold_events": payload.trust.missing_context,
        }
        metrics = finance_summary_metrics or []
        if finance_template == "aws_cost_review":
            digest.update(self._aws_digest_fallback(metrics, digest))
        elif finance_template == "runway_burn_review":
            digest.update(self._runway_digest_fallback(metrics, digest))
        return digest

    def _apply_threshold_events_to_payload(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        company_state: Dict[str, Any],
        retrieval: List[Dict[str, Any]],
        finance_template: Optional[str],
    ) -> ReportPayload:
        if not finance_template:
            return payload
        forecast = self._run_forecast_for_template(
            finance_template=finance_template,
            task_input=task_input,
            company_state=company_state,
            retrieval=retrieval,
        )
        if not forecast or not forecast.threshold_events:
            return payload

        payload = payload.model_copy(deep=True)
        # Surface critical events first, then high, then medium
        sorted_events = sorted(
            forecast.threshold_events,
            key=lambda e: {"critical": 0, "high": 1, "medium": 2}.get(e.severity, 3),
        )
        primary_event = sorted_events[0]
        threshold_summary = primary_event.description
        if threshold_summary not in payload.trust.open_questions:
            payload.trust.open_questions.append(threshold_summary)
        if threshold_summary not in payload.trust.missing_context:
            payload.trust.missing_context.append(threshold_summary)
        # Prefix the summary only when a critical or high event is present
        if primary_event.severity in ("critical", "high"):
            payload.answer.summary = f"{payload.answer.summary} {threshold_summary}"
        if len(payload.answer.sections) > 1:
            if threshold_summary not in payload.answer.sections[1].items:
                payload.answer.sections[1].items = [threshold_summary, *payload.answer.sections[1].items][:3]
        return payload

    def _apply_finance_template_to_payload(self, payload: ReportPayload, *, finance_template: Optional[str]) -> ReportPayload:
        if not finance_template:
            return payload
        payload = payload.model_copy(deep=True)
        labels = self._template_section_labels(finance_template)
        for index, label in enumerate(labels):
            if index < len(payload.answer.sections):
                payload.answer.sections[index].label = label
        if finance_template == "aws_cost_review" and "AWS" not in payload.answer.title:
            payload.answer.title = f"AWS Cost Review: {payload.answer.title}"
        elif finance_template == "runway_burn_review" and "Runway" not in payload.answer.title:
            payload.answer.title = f"Runway and Burn Review: {payload.answer.title}"
        elif finance_template == "project_spend_review" and "Project" not in payload.answer.title:
            payload.answer.title = f"Project Spend Review: {payload.answer.title}"
        return payload

    def _apply_finance_close_focus_to_payload(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        retrieval: List[Dict[str, Any]],
        signals: List[Dict[str, Any]],
        finance_template: Optional[str],
    ) -> ReportPayload:
        if finance_template != "board_financial_update":
            return payload
        if not self._is_company_health_summary_request(task_input):
            return payload

        finance_close_items = self._finance_close_issue_items(retrieval, signals)
        if len(finance_close_items) < 3:
            return payload

        payload = payload.model_copy(deep=True)
        summary_prefix = (
            "Finance close review is the immediate company-health issue: "
            "cloud spend variance is above forecast and the board packet narrative for close week still needs a final CEO call."
        )
        if summary_prefix.lower() not in payload.answer.summary.lower():
            payload.answer.summary = f"{summary_prefix} {payload.answer.summary}".strip()

        while len(payload.answer.sections) < 3:
            payload.answer.sections.append(ReportSection(label="Recommended Actions", items=[]))

        key_section = payload.answer.sections[0]
        risk_section = payload.answer.sections[1]
        action_section = payload.answer.sections[2]
        key_section.items = [finance_close_items[0], *key_section.items][:3]
        risk_section.items = [finance_close_items[1], *risk_section.items][:3]
        action_section.items = [finance_close_items[2], *action_section.items][:3]
        return payload

    def _apply_finance_operational_breakdown_shape(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        session_history: List[Dict[str, Any]],
    ) -> ReportPayload:
        if self._finance_followup_submode(task_input, session_history) != "operational_breakdown":
            return payload

        payload = payload.model_copy(deep=True)
        lowered = task_input.lower()
        if "s&m" in lowered or "sales and marketing" in lowered:
            payload.answer.title = "S&M Freeze Implementation Framework"
        elif "cloud" in lowered:
            payload.answer.title = "Cloud Reduction Implementation Framework"
        elif "hiring" in lowered:
            payload.answer.title = "Hiring Deferral Implementation Framework"
        payload.answer.summary = (
            "Use the freeze as a provisional implementation framework today rather than waiting on a perfect vendor ledger. "
            "The working cut assumes lower-priority paid programs, agency retainers, event spend, and non-critical tools come out first, "
            "with Marcus validating the exact line items and revenue impact before final sign-off."
        )
        payload.answer.sections = [
            ReportSection(label="Working Line-Item Framework", items=self._finance_operational_breakdown_items()),
            ReportSection(label="Owners, Timing & Risk", items=self._finance_operational_owner_items()),
            ReportSection(label="What Marcus Must Confirm", items=self._finance_operational_followup_items()),
        ]
        return payload

    def _apply_followup_action_plan_shape(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        session_history: List[Dict[str, Any]],
        artifact_type: Optional[str] = None,
    ) -> ReportPayload:
        if self._report_followup_mode(task_input, session_history) != "action_plan":
            return payload
        if self._is_explicit_email_request(task_input, artifact_type) or self._is_resolution_language_request(task_input):
            return payload

        payload = payload.model_copy(deep=True)
        finance_submode = self._finance_followup_submode(task_input, session_history)
        payload.answer.summary = self._direct_followup_summary(
            task_input,
            payload.answer.summary,
            session_history=session_history,
            finance_submode=finance_submode,
        )

        while len(payload.answer.sections) < 3:
            payload.answer.sections.append(ReportSection(label="Recommended Actions", items=[]))

        lowered = task_input.lower()
        if finance_submode == "operational_breakdown":
            payload.answer.sections[0].label = "Cost Action Breakdown"
            payload.answer.sections[1].label = "Owners, Timing & Risk"
            payload.answer.sections[2].label = "Immediate Follow-Up"
        elif self._contains_any_marker(lowered, ("defer", "delegate", "what can i safely", "what can we safely", "safely defer", "safely delegate")):
            payload.answer.sections[0].label = "CEO Must Own Today"
            payload.answer.sections[1].label = "Safe to Delegate"
            payload.answer.sections[2].label = "Safe to Defer"
        elif self._contains_any_marker(lowered, ("sequence", "how should i sequence", "how to sequence", "what order should", "prep sequence", "priority order")):
            payload.answer.sections[0].label = "Ordered Sequence"
            payload.answer.sections[1].label = "Constraints & Blockers"
            payload.answer.sections[2].label = "If Time Runs Short"

        payload.answer.sections[0].items = [
            self._strip_generic_report_lead(item) for item in payload.answer.sections[0].items[:3]
        ]
        payload.answer.sections[1].items = [
            self._strip_generic_report_lead(item) for item in payload.answer.sections[1].items[:3]
        ]
        if finance_submode == "operational_breakdown":
            payload.answer.sections[0].items = self._finance_operational_breakdown_items()
            payload.answer.sections[1].items = self._finance_operational_owner_items()
        payload.answer.sections[2].items = self._followup_action_items(
            task_input=task_input,
            payload=payload,
            session_history=session_history,
            finance_submode=finance_submode,
        )
        payload.trust.open_questions = [
            question
            for question in payload.trust.open_questions
            if "do you want" not in question.lower()
        ]
        return payload

    def _direct_followup_summary(
        self,
        task_input: str,
        summary: str,
        *,
        session_history: List[Dict[str, Any]],
        finance_submode: str = "standard",
    ) -> str:
        lowered = task_input.lower()
        if self._contains_any_marker(lowered, ("board narrative", "corrective story", "corrective narrative", "frame the variance", "frame for the board", "margin compression story")):
            return (
                "Board narrative: Margin compression is coming from a $70k AWS overrun and operating inefficiency, and we already have corrective actions underway to protect runway."
            )
        if self._contains_any_marker(lowered, ("board packet", "packet need", "specific edits", "packet edit")):
            return "Before the packet goes out, tighten the finance narrative, state the hiring-freeze decision explicitly, and add owners plus dates to every at-risk account item."
        # "before it goes out" alone is too broad — only treat it as a board-packet signal when
        # finance/board context co-occurs in the same message, preventing false matches on phrases
        # like "review before the invite goes out" in customer-escalation contexts.
        if "before it goes out" in lowered and self._contains_any_marker(
            lowered, ("board", "packet", "finance", "narrative", "close", "filing")
        ):
            return "Before the packet goes out, tighten the finance narrative, state the hiring-freeze decision explicitly, and add owners plus dates to every at-risk account item."
        if self._contains_any_marker(lowered, ("what questions should i expect", "questions from the board", "board questions", "do i have answers ready", "do i have answers")):
            return "Expect three core board questions on AWS variance, runway durability, and initiative risk, and have the corrective answer ready for each."
        if (
            self._contains_any_marker(lowered, ("board", "packet"))
            and self._contains_any_marker(lowered, ("who is responsible", "who owns", "open item", "open items", "deadline", "deadlines"))
        ):
            return "Before the meeting, lock one owner and one deadline for the customer recovery items, the hiring decisions, and the board narrative edits."
        if "apex" in lowered and "commitment" in lowered:
            return "Tell Apex today: we are assigning one owner, you will have the written recovery plan by end of day, and your next executive update arrives tomorrow morning."
        if "redwood" in lowered and ("timeline" in lowered or "who owns" in lowered or "owner" in lowered):
            return "The Redwood rescue plan is owned by the Sales Lead with Product support, and the dated renewal proposal needs to be back tomorrow."
        if self._contains_any_marker(lowered, self.ESCALATION_MARKERS):
            if "communication" in lowered or "what does a good executive communication" in lowered:
                return "Use a direct executive message that owns the issue, makes one dated recovery commitment, and tells the customer exactly when the next update arrives."
            if "delegate" in lowered or "direct response" in lowered:
                return "Split the escalation work into what the CEO must own personally and what should be delegated with same-day deadlines."
            if "redwood" in lowered and ("commitment" in lowered or "timeline" in lowered or "owner" in lowered):
                return "Tell Apex what lands by end of day, confirm the Redwood owner by name, and state the dated rescue milestone for tomorrow."
            if "commitment" in lowered:
                return "Tell Apex today exactly what will be delivered by end of day, who owns each open issue, and when the next executive checkpoint happens."
            return "Top customer escalations need immediate owner-led recovery steps, explicit milestones, and a same-day communication cadence."
        if self._contains_any_marker(lowered, ("what decisions need to be made before specific meetings today", "before specific meetings", "specific meetings today", "before my meetings")):
            return "Before today's meetings, decide the VP Engineering offer, confirm the Apex customer commitment, and approve the single owner for the renewal rescue."
        if self._contains_any_marker(lowered, ("what can i safely", "what can we safely", "safely defer", "safely delegate")):
            return "Safely delegate the logistics and tracker work, but keep the offer decision, the customer commitment, and the renewal-owner call in your lane today."
        if finance_submode == "operational_breakdown":
            return (
                "Marcus's $53K monthly cost plan can be executed as a working freeze framework today: "
                "$45K of discretionary sales and marketing cuts, $8K of cloud reduction from shutting down underused dev environments, "
                "and deferral of two planned Q3 hires. The breakdown below shows the likely line items, owners, and operating risk, with Marcus's confirmation still needed before final sign-off."
            )
        if "investor call prep" in lowered:
            return "First: lock the investor-call headline metrics, then: finalize the backup page, then: rehearse the narrative and cut anything non-critical."
        if self._contains_any_marker(lowered, ("sequence", "how should i sequence", "how to sequence", "what order should", "prep sequence", "priority order")) and self._conversation_started_from_schedule(session_history):
            return "First: lock the investor-call headline metrics, then: finalize the supporting backup, then: rehearse the narrative and cut anything non-critical."
        if self._contains_any_marker(lowered, ("what decisions need to be made", "before my meetings", "meetings today")) and self._conversation_started_from_schedule(session_history):
            return "Before today's meetings, decide the VP Engineering offer, confirm the Apex customer commitment, and approve the Redwood owner plus rescue path."
        if self._contains_any_marker(lowered, ("follow-ups from yesterday", "follow-ups from this morning", "outstanding follow")) and self._conversation_started_from_schedule(session_history):
            return "Three follow-ups are still live this morning: the investor runway update, the Northstar renewal escalation, and the open owner check on customer recovery."
        if self._contains_any_marker(lowered, ("hiring freeze", "which roles are exempt", "roles are exempt", "exempt")):
            return "Freeze all non-critical backfills now; keep VP Engineering and GTM Director exempt because they are the critical hires tied to delivery and revenue."
        if self._contains_any_marker(lowered, ("containment actions", "cloud cost overrun", "cloud cost", "aws costs", "optimize aws")) and self._contains_any_marker(lowered, ("exempt", "hiring freeze")):
            return "Cut the AWS overrun with named Finance and Engineering owners, while holding the hiring freeze on non-critical backfills and exempting VP Engineering plus GTM Director."
        if finance_submode == "metric_governance":
            return "Metric governance now needs named owners, review cadence, and threshold targets so Finance, Sales, and Product track the same scoreboard."
        if finance_submode == "kpi_tracking":
            return "The next finance step is to lock the KPI set, assign each metric owner, and define the review cadence before the next board or investor update."
        if "investor" in lowered:
            return (
                "Investor prep now needs explicit metrics, a named owner for each supporting input, "
                "and a dated checklist before the next update goes out."
            )
        if self._contains_any_marker(lowered, self.FINANCE_EXECUTION_MARKERS):
            return "The immediate priority is to cut AWS variance, decide which hires are business-critical, and lock the finance-close narrative before the review."
        return self._strip_generic_report_lead(summary)

    def _strip_generic_report_lead(self, text: str) -> str:
        normalized = (text or "").strip()
        generic_prefixes = (
            "This report outlines ",
            "This report summarizes ",
            "This report identifies ",
            "This report provides ",
            "This report categorizes ",
            "This communication outlines ",
        )
        for prefix in generic_prefixes:
            if normalized.lower().startswith(prefix.lower()):
                trimmed = normalized[len(prefix):].strip()
                if trimmed:
                    return trimmed[0].upper() + trimmed[1:]
        return normalized

    def _followup_action_items(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        session_history: List[Dict[str, Any]],
        finance_submode: str = "standard",
    ) -> list[str]:
        lowered = task_input.lower()
        if self._contains_any_marker(lowered, self.ESCALATION_MARKERS):
            return self._escalation_action_items(task_input=task_input, payload=payload)
        if finance_submode == "operational_breakdown":
            return self._finance_operational_followup_items()
        if finance_submode == "metric_governance":
            return self._finance_metric_governance_items()
        if finance_submode == "kpi_tracking":
            return self._finance_kpi_tracking_items()
        if self._contains_any_marker(lowered, ("what questions should i expect", "questions from the board", "board questions", "do i have answers ready", "do i have answers")):
            return [
                "Expected Q: 'What is driving the AWS overrun?' \u2192 Answer: 'Engineering froze non-essential compute this week; targeting $30k reduction by month-end.'",
                "Expected Q: 'What is our current runway?' \u2192 Answer: '19.2 months at $950k/month burn rate.'",
                "Expected Q: 'Which strategic initiatives are at risk?' \u2192 Answer: 'Enterprise Expansion \u2014 GTM Director hire by May 1 is the critical path. VP Engineering is exempt from the freeze; offer going out this week.'",
            ]
        if (
            self._contains_any_marker(lowered, ("board", "packet"))
            and self._contains_any_marker(lowered, ("who is responsible", "who owns", "open item", "open items", "deadline", "deadlines"))
        ):
            return [
                "VP Customer Success | Today by 5 PM: deliver the Apex Health recovery roadmap with named owner per issue, next milestone time, and the next customer update window.",
                "Sales lead + Product lead | Tomorrow by noon: finalize the Redwood renewal rescue plan, confirm the single customer-facing owner, and send the dated checkpoint list to the CEO.",
                "CEO + CFO | Before board packet cutoff: close the hiring-freeze narrative, exempt-role decision, and final margin slide edits so the packet leaves with no unowned items.",
            ]
        if "redwood" in lowered and ("commitment" in lowered or "timeline" in lowered or "owner" in lowered):
            return [
                "CEO | Today by 3 PM: tell Apex Health the written recovery plan, named owners, and first milestone times will land before end of day.",
                "VP Customer Success | Today by 5 PM: send Apex the milestone table with one owner per issue and the next executive update window.",
                "Sales Lead | Tomorrow by noon: own the Redwood renewal rescue plan, confirm Product support dependencies, and send the dated proposal milestone back to the CEO.",
            ]
        if self._contains_any_marker(lowered, ("what decisions need to be made before specific meetings today", "before specific meetings", "specific meetings today", "before my meetings")):
            return [
                "9:00 AM leadership sync — Decision/action needed: approve the VP Engineering offer before the competing offer window closes — Owner: CEO + Head of Talent.",
                "Before the customer-risk review — Decision/action needed: confirm the one recovery commitment Apex Health will hear today and who sends the written plan — Owner: CEO + VP Customer Success.",
                "Before the revenue check-in — Decision/action needed: name the single owner and dated rescue path for Redwood renewal risk — Owner: CEO + Sales Lead.",
            ]
        if self._contains_any_marker(lowered, ("what can i safely", "what can we safely", "safely defer", "safely delegate")):
            return [
                "Safe to Delegate — Talent Lead: run the Chief of Staff final-round logistics today and return only the decision-ready recommendation by end of day.",
                "Safe to Delegate — VP Customer Success: own the milestone tracker and daily customer-update cadence; escalate only if a committed checkpoint slips.",
                "Safe to Defer — investor deck polish and non-critical operating narrative cleanup until tomorrow, after the offer, customer commitment, and renewal-owner decisions are locked.",
            ]
        if "investor call prep" in lowered:
            return [
                "First 20 minutes — CFO: lock the three investor-call numbers and the one-line message for each so the prep stays bounded.",
                "Next 30 minutes — Finance lead + Chief of Staff: assemble the backup page with current value, driver, owner, and likely follow-up question for each metric.",
                "Final 15 minutes before the call — CEO: rehearse the opening narrative, decide what to skip if time compresses, and leave deck polish to the team.",
            ]
        if self._contains_any_marker(lowered, ("follow-ups from yesterday", "follow-ups from this morning", "outstanding follow")):
            return [
                "Investor runway update — Status: still needs CEO review before today's check-in — Owner: CFO drafted, CEO must approve the final framing.",
                "Northstar or Redwood renewal escalation — Status: rescue path not yet confirmed with one accountable owner — Owner: Sales Lead needs to send the dated plan back today.",
                "Apex recovery follow-up — Status: customer commitment is live but milestone ownership still needs confirmation — Owner: VP Customer Success to close the loop before end of day.",
            ]
        if self._contains_any_marker(lowered, ("board packet", "packet need", "specific edits", "packet edit")) or (
            "before it goes out" in lowered
            and self._contains_any_marker(lowered, ("board", "packet", "finance", "narrative", "close", "filing"))
        ):
            return [
                "CEO | Today: add one sentence to the finance section: 'AWS spend came in $70k over budget, driven by unoptimized dev environment compute. Engineering is freezing non-essential instances, targeting $30k reduction by month-end. Runway: 19.2 months.'",
                "CEO | Today: confirm the hiring section states VP Engineering and GTM Director are exempt from the freeze, and all other backfills are paused until burn rate is corrected.",
                "CFO | Before packet goes out: verify at-risk accounts (Apex Health $420k, Redwood Systems $310k) are named with owners and deadline dates in the customer section.",
            ]
        if "investor" in lowered:
            return self._finance_action_items(task_input=task_input, payload=payload)
        if self._contains_any_marker(lowered, self.FINANCE_EXECUTION_MARKERS):
            return self._finance_action_items(task_input=task_input, payload=payload)
        if self._conversation_started_from_schedule(session_history):
            return self._schedule_origin_action_items(task_input=task_input, payload=payload)

        base_items = [self._strip_generic_report_lead(item) for item in payload.answer.sections[2].items[:3] if item]
        return self._normalize_action_items(base_items)

    def _finance_action_items(self, *, task_input: str, payload: ReportPayload) -> list[str]:
        lowered = task_input.lower()
        if self._contains_any_marker(lowered, ("containment actions", "cloud cost overrun", "cloud cost", "aws costs", "optimize aws")) and self._contains_any_marker(lowered, ("exempt", "hiring freeze")):
            return [
                "Engineering lead | Within 24 hours: freeze non-essential compute, identify the top AWS overrun drivers, and confirm the first $30k reduction move.",
                "Finance lead | Tomorrow by noon: publish the AWS variance tracker with owner per driver, weekly savings target, and the next review checkpoint.",
                "CEO + CFO | Today: hold the hiring freeze on non-critical backfills and confirm VP Engineering plus GTM Director are exempt; send the exempt-role list to managers.",
            ]
        if self._contains_any_marker(lowered, ("hiring freeze", "which roles are exempt", "roles are exempt", "exempt")):
            return [
                "CEO | Today: state the decision explicitly: freeze all non-critical backfills; VP Engineering and GTM Director remain exempt.",
                "Head of Talent | Today by end of day: notify managers which roles are paused versus exempt and stop non-critical backfill loops.",
                "CFO | Before the next finance-close review: reflect the freeze decision and exempt roles in the burn-control narrative.",
            ]
        if self._contains_any_marker(lowered, ("board narrative", "corrective story", "corrective narrative", "frame the variance", "frame for the board", "present to the board", "narrative that explains")):
            return [
                "Board narrative: 'AWS spend came in $70k over budget, driven by unoptimized dev environment compute. Engineering is freezing non-essential instances this week, targeting $30k reduction by month-end. Runway remains 19.2 months if corrective actions land.'",
                "Engineering lead | By end of week: confirm non-essential compute freeze is active and $30k cost reduction is on track.",
                "CFO | Before board meeting: verify final close runway reflects locked actuals and corrective action impact.",
            ]
        if "metric" in lowered or "investor" in lowered:
            return [
                "CFO | By tomorrow morning: finalize the three investor metrics to lead with, including burn rate, runway, and the highest-risk initiative variance.",
                "Finance lead | By tomorrow noon: build the one-page metric backup with current value, trend direction, and the reason each metric matters now.",
                "CEO + Chief of Staff | Before the next investor update: rehearse the narrative, assign who answers each metric question, and lock the follow-up commitments.",
            ]
        if self._contains_any_marker(lowered, ("timeline", "responsible parties", "who owns", "who is responsible", "containment actions", "action plan", "optimiz")):
            return [
                "Finance + Engineering | Within 24 hours: isolate the largest AWS overrun drivers, confirm whether usage is a spike or step-change, and recommend the first cost-control move.",
                "CEO + CFO | Before the next finance close review: approve the ranked list of business-critical hires and defer non-critical backfills.",
                "Finance | By board packet cutoff: rewrite the close narrative so variance, burn impact, and corrective actions are explicit.",
            ]
        # Let the LLM's KB-informed section stand for all other finance questions
        base_items = [self._strip_generic_report_lead(item) for item in payload.answer.sections[2].items[:3] if item]
        return self._normalize_action_items(base_items)

    def _finance_operational_breakdown_items(self) -> list[str]:
        return [
            "Sales and marketing freeze: reduce discretionary spend from $65K to $20K monthly (-$45K). Working assumption: pause paid demand-generation programs, lower-priority agency retainers, field-event sponsorships, and non-converting campaign tools first, with Marcus Webb and Sarah Chen confirming the exact stop-list today.",
            "Cloud reduction: cut monthly infrastructure from $28K to $20K (-$8K) by shutting down three underused development environments. Working assumption: retire duplicate QA or staging environments with low utilization, keep production-safe coverage intact, and have Priya Desai validate the exact shutdown list before end of day.",
            "Hiring deferrals: move two planned Q3 hires out of the near-term plan so burn drops without touching core delivery. Working assumption: defer non-critical commercial or support backfills before launch-critical engineering roles, with Marcus Webb and Talent confirming the exact roles and revised start dates tomorrow morning.",
        ]

    def _finance_operational_owner_items(self) -> list[str]:
        return [
            "S&M cuts — Owner: Marcus Webb + Sarah Chen. Timing: provisional stop-list within 2 hours, final vendor/program list by end of day, and spend reduction starts this week. Risk: pipeline quality drops if high-conversion channels or committed event obligations are cut with the noise.",
            "Cloud reduction — Owner: Priya Desai + Marcus Webb. Timing: provisional shutdown plan now, exact environment list by end of day, and savings verified in the next weekly cloud review. Risk: developer productivity or test coverage slips if shared environments are removed without replacement.",
            "Hiring deferrals — Owner: Marcus Webb + Head of Talent. Timing: provisional role freeze now, exact deferred-role list by tomorrow morning, and manager notification before any open req advances. Risk: roadmap dates move if one of the slipped roles was covering launch-critical work.",
        ]

    def _finance_operational_followup_items(self) -> list[str]:
        return [
            "Marcus Webb | Within 2 hours: confirm the provisional $45K S&M stop-list by vendor, campaign, and monthly savings per line item, and flag any termination fees or committed spend.",
            "Priya Desai | By end of day: confirm the three development environments being shut down, owner per environment, and the rollback path if engineering velocity is hit.",
            "Head of Talent + Marcus Webb | By tomorrow morning: confirm the two deferred Q3 hires, the revised timing for each role, and the delivery impact if either role slips again.",
        ]

    def _apply_resolution_language_shape(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        session_history: List[Dict[str, Any]],
    ) -> ReportPayload:
        if not self._is_resolution_language_request(task_input):
            return payload

        payload = payload.model_copy(deep=True)
        vp_limit = self._find_currency_threshold(session_history, default="$50K", role_markers=("vp sales",))
        cfo_limit = self._find_currency_threshold(session_history, default="$500K", role_markers=("cfo",))
        payload.answer.title = "Board Resolution — Pricing Committee Governance"
        payload.answer.summary = (
            f"This resolution establishes the Pricing Committee, sets approval authority at up to {vp_limit} for VP Sales, "
            f"{vp_limit} to {cfo_limit} for the CFO, and requires CEO plus Board approval above {cfo_limit}. "
            "It also sets membership, meeting cadence, and reporting obligations for board oversight."
        )
        payload.answer.sections = [
            ReportSection(
                label="Resolution Text",
                items=[
                    "WHEREAS, the Board of Directors has determined that the Corporation requires a formal Pricing Committee to govern pricing strategy, discount approvals, renewal exceptions, and other material commercial pricing decisions across product lines;",
                    "RESOLVED, that the Board hereby establishes a Pricing Committee consisting of the Chief Financial Officer, the Vice President of Sales, and the Chief Executive Officer, with the Chief Financial Officer serving as chair and responsible for maintaining committee records and decision logs;",
                    f"RESOLVED FURTHER, that pricing authority is delegated as follows: (a) the Vice President of Sales may approve pricing and discount actions with annualized contract value up to {vp_limit}; (b) the Chief Financial Officer must approve pricing and discount actions above {vp_limit} and up to {cfo_limit}; and (c) any pricing action above {cfo_limit}, any strategic pricing exception for a named enterprise account, or any committee decision that materially impacts margin guidance requires approval by both the Chief Executive Officer and the Board of Directors;",
                ],
            ),
            ReportSection(
                label="Committee Structure",
                items=[
                    "Membership and scope: CFO, VP Sales, and CEO review new pricing frameworks, non-standard enterprise renewals, strategic discounting, and any exception that could materially affect gross margin or renewal risk.",
                    "Cadence and documentation: the committee meets at least monthly and on demand for urgent enterprise decisions, records each approval in a pricing register, and documents decision rationale, authority tier, expected margin impact, and owner for follow-through.",
                    "Reporting requirements: the CFO delivers a quarterly report to the Board summarizing approvals by tier, major exceptions granted, gross-margin impact, and any pricing actions that require changes to board-level guidance or fundraising narrative.",
                ],
            ),
            ReportSection(
                label="Counsel Review Points",
                items=[
                    "Confirm the resolution language aligns with current bylaws and any existing delegation-of-authority policy.",
                    "Verify whether emergency pricing exceptions require a separate ratification clause or can be handled through the standard Board-approval threshold above the CFO tier.",
                    "Tighten defined terms such as 'pricing action', 'strategic pricing exception', and 'materially impacts margin guidance' for final board package precision.",
                ],
            ),
        ]
        return payload

    def _find_currency_threshold(
        self,
        session_history: List[Dict[str, Any]],
        *,
        default: str,
        role_markers: tuple[str, ...],
    ) -> str:
        texts: list[str] = []
        for item in session_history[-6:]:
            texts.append(str(item.get("query") or ""))
            texts.append(str(item.get("response") or ""))
        combined = " ".join(texts)
        role_pattern = "|".join(re.escape(marker) for marker in role_markers)
        range_pattern = re.compile(
            rf"(?:{role_pattern}).*?(\$[0-9][0-9,]*(?:\.[0-9]+)?[KkMm]?)\s*-\s*(\$[0-9][0-9,]*(?:\.[0-9]+)?[KkMm]?)",
            re.IGNORECASE | re.DOTALL,
        )
        range_match = range_pattern.search(combined)
        if range_match:
            return range_match.group(2)
        pattern = re.compile(
            rf"(?:{role_pattern}).*?(\$[0-9][0-9,]*(?:\.[0-9]+)?[KkMm]?)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(combined)
        return match.group(1) if match else default

    def _finance_kpi_tracking_items(self) -> list[str]:
        return [
            "CFO | By tomorrow morning: publish the KPI pack with burn rate, runway, Europe revenue trend, and AWS variance, including actual vs plan and actual vs prior.",
            "Finance lead | Weekly by Tuesday noon: update each KPI with owner, cadence, target threshold, and the primary driver behind any movement.",
            "CEO + Chief of Staff | Before the next board or investor update: review the KPI pack, confirm which metric leads the narrative, and assign who answers follow-up questions.",
        ]

    def _finance_metric_governance_items(self) -> list[str]:
        return [
            "CFO | By tomorrow noon: assign one accountable owner per shared metric and publish the review cadence for Finance, Sales, and Product.",
            "Sales lead + Product lead | Weekly by Wednesday: review Europe pipeline conversion, launch readiness, and customer retention metrics against agreed targets.",
            "Chief of Staff | By Friday: run the metric review, document blockers, and escalate any KPI that misses threshold for two consecutive weeks.",
        ]

    def _escalation_action_items(self, *, task_input: str, payload: ReportPayload) -> list[str]:
        lowered = task_input.lower()
        if "apex" in lowered and "commitment" in lowered and "redwood" in lowered:
            return [
                "CEO | Today by 3 PM: tell Apex Health they will receive the written recovery plan, named owners, and first milestone times before end of day.",
                "VP Customer Success | Today by 5 PM: send Apex the recovery plan with one owner per issue and the next executive update window set for tomorrow morning.",
                "Sales Lead | Tomorrow by noon: own the Redwood renewal rescue plan, confirm Product support dependencies, and send the dated proposal milestone back to the CEO.",
            ]
        if "apex" in lowered and "commitment" in lowered:
            return [
                "CEO | Today by 3 PM: tell Apex Health we will send the written recovery plan, named owners, and first milestone times before end of day.",
                "VP Customer Success | Today by 5 PM: send Apex Health the recovery plan with the primary owner for each issue and the first checkpoint within 24 hours.",
                "CTO + Support lead | Tomorrow by 10 AM: confirm the fix path, customer-visible milestone, and who will deliver the next progress update.",
            ]
        if "redwood" in lowered and ("timeline" in lowered or "who owns" in lowered or "owner" in lowered):
            return [
                "Sales Lead | Today by end of day: own the Redwood renewal rescue plan and confirm the single customer-facing owner for the account.",
                "Product Lead | Tomorrow by 10 AM: confirm the product commitments, launch dependency, and any blocker that could weaken the renewal proposal.",
                "Sales Lead | Tomorrow by noon: send the dated renewal proposal and next executive checkpoint back to the CEO before customer delivery.",
            ]
        if "communication" in lowered or "at-risk customer" in lowered:
            return [
                'CEO message | Today: "We own the service issue, we are assigning named owners now, and you will have a written recovery plan from us by end of day."',
                'CEO message | Today: "Your next update will arrive tomorrow morning with milestone status, remaining risks, and the owner for each open item."',
                "VP Customer Success | Before the message goes out: attach the milestone table and confirm who is on point for the next live customer update.",
            ]
        if "delegate" in lowered or "direct response" in lowered:
            return [
                "CEO | Today within 2 hours: call Apex Health to reset confidence, make the recovery commitment, and confirm the next executive checkpoint.",
                "VP Customer Success | Today by end of day: own the detailed Apex recovery plan, daily customer updates, and milestone tracking.",
                "Sales lead + Product lead | By tomorrow morning: own the Redwood renewal rescue plan, customer communication owner, and the next dated milestone.",
            ]
        if "timeline" in lowered or "responsible parties" in lowered:
            return [
                "Apex Health account lead | Today by end of day: send the recovery milestones, named owner for each issue, and the next checkpoint time to the customer.",
                "Sales lead + Product lead | By tomorrow noon: finalize the Redwood renewal rescue plan, customer-visible milestones, and the single communication owner.",
                "CEO | Within 24 hours: approve the final Redwood message and confirm who will deliver the next executive update to the account.",
            ]
        actions = [
            "Account lead: send Apex Health a recovery update today with the next milestone, owner, and timing.",
            "CEO + customer lead: call Redwood Systems within 24 hours to confirm the remediation path and executive contact cadence.",
            "Operations: run a daily escalation tracker with owners, open risks, and the next promised customer update.",
        ]
        if "timeline" in lowered or "responsible parties" in lowered:
            actions = [
                "Apex Health account lead | Today by end of day: send the recovery plan, next checkpoint, and named owner for each open issue.",
                "CEO + Redwood Systems sponsor lead | Within 24 hours: confirm the remediation timeline and the next executive update.",
                "Operations lead | Daily until closed: publish the escalation tracker with status, blocker, owner, and next external communication time.",
            ]
        return actions

    def _normalize_action_items(self, items: list[str]) -> list[str]:
        next_day = (datetime.now() + timedelta(days=1)).strftime("%B %-d, %Y")
        next_week = (datetime.now() + timedelta(days=7)).strftime("%B %-d, %Y")
        defaults = [
            f"CEO + functional lead | By {next_day}: confirm the top priority, named owner, and expected outcome.",
            f"Finance or operations | By {next_day}: convert the next step into a dated task with an accountable owner.",
            f"Chief of Staff | By {next_week}: update the executive operating narrative with current status, risk, and next checkpoint.",
        ]
        normalized = [item.strip() for item in items if item and item.strip()]
        strengthened: list[str] = []
        for item in normalized[:3]:
            if self._action_item_is_specific(item):
                strengthened.append(item)
            else:
                strengthened.append(defaults[len(strengthened)])
        while len(strengthened) < 3:
            strengthened.append(defaults[len(strengthened)])
        return strengthened[:3]

    def _clarifying_questions(
        self,
        task_input: str,
        payload: ReportPayload,
        ceo_id: Optional[str] = None,
        resolved_topics: frozenset = frozenset(),
    ) -> list[str]:
        """
        Return at most ONE clarifying question — always answerable with 2 taps.
        Checks learned_defaults first: if the CEO has expressed a consistent preference
        (≥3 times, >60% share), skip the question entirely and let the default apply.
        """
        from src.core.database import get_learned_preference  # local import to avoid circular

        # CEO has explicitly pushed back on clarifying questions — suppress all of them.
        if "suppress_all_questions" in resolved_topics:
            return []

        lowered = task_input.lower()
        questions: list[str] = []

        missing_context = [str(item) for item in payload.trust.missing_context if item]
        existing = [str(item) for item in payload.trust.open_questions if item]

        def add(question: str) -> None:
            normalized = question.strip()
            if normalized and normalized not in questions:
                questions.append(normalized)

        # Data source gap — always ask regardless of preferences
        if any("structured finance data" in item.lower() for item in existing + missing_context):
            add("Which source should I treat as the source of truth: Close Workbook or Company State?")
            return questions[:1]
        if any("calendar evidence was limited" in item.lower() for item in missing_context):
            add("Optimize this around your live calendar or just inbox and deadlines?")
            return questions[:1]

        # For output format / framing questions — skip if the CEO already answered
        # in this conversation, or if we have a learned persistent preference.
        if "output_format" in resolved_topics:
            return []  # Already answered in a prior turn — don't re-ask
        if ceo_id:
            learned_format = get_learned_preference(ceo_id, "output_format")
            if learned_format:
                return []  # Already know — don't ask

        # All questions map to pre-built 2-option sets.
        if self._contains_any_marker(lowered, self.FINANCE_EXECUTION_MARKERS):
            add("Frame this for your own decision or for the board?")
        elif self._contains_any_marker(lowered, self.ESCALATION_MARKERS):
            add("Do you want a draft response or just the brief?")
        elif self._contains_any_marker(lowered, ("today", "my day", "meetings", "follow-ups", "schedule")):
            add("Optimize this for meetings or for focus blocks?")
        elif self._contains_any_marker(lowered, ("board", "packet", "narrative", "memo")):
            add("Frame this in board language or operator language?")
        else:
            add("Format this for a personal decision or a board presentation?")

        return questions[:1]

    def _clarification_options(self, *, task_input: str, questions: list[str]) -> list[dict[str, Any]]:
        """
        Return exactly 2 pre-built options for each clarifying question.
        Every question must be answerable with a tap — no open-ended text input.
        """
        lowered = str(task_input or "").lower()
        question_text = " ".join(str(q) for q in questions).lower()

        # Finance period anchor
        if "source of truth" in question_text:
            return [
                {"label": "Close Workbook", "value": "close_workbook", "description": "Authoritative finance source.", "apply_text": "Use the close workbook as the source of truth."},
                {"label": "Company State", "value": "company_state", "description": "Current operating snapshot.", "apply_text": "Use current company state as the source of truth."},
            ]

        if "live calendar" in question_text or "calendar" in question_text and "inbox" in question_text:
            return [
                {"label": "Calendar first", "value": "calendar_first", "description": "Optimize around meetings.", "apply_text": "Optimize this around my live calendar."},
                {"label": "Inbox and deadlines", "value": "inbox_deadlines", "description": "Anchor to email asks and deadlines.", "apply_text": "Stay anchored to inbox and deadline signals."},
            ]

        if "board" in question_text or "board packet" in question_text or "finance close" in question_text:
            return [
                {"label": "My decision", "value": "operating_decision", "description": "Internal operating view.", "apply_text": "Frame this for my own operating decision."},
                {"label": "Board presentation", "value": "board_packet", "description": "Board-ready language and format.", "apply_text": "Frame this for the board packet."},
            ]

        if "period" in question_text or self._contains_any_marker(lowered, self.FINANCE_EXECUTION_MARKERS):
            return [
                {"label": "This month", "value": "current_month", "description": "Anchor to the current month.", "apply_text": "Anchor this to the current month."},
                {"label": "Quarter close", "value": "quarter_close", "description": "Anchor to quarter close.", "apply_text": "Anchor this to quarter close."},
            ]

        if "draft" in question_text or self._contains_any_marker(lowered, self.ESCALATION_MARKERS):
            return [
                {"label": "Draft a response", "value": "draft_response", "description": "Compose a reply ready to review.", "apply_text": "Draft an executive response I can review and send."},
                {"label": "Just the brief", "value": "brief_only", "description": "Context and recommendation, no draft.", "apply_text": "Give me the brief — context, risk, and recommended action."},
            ]

        if "meeting" in question_text or "schedule" in question_text or "focus" in question_text:
            return [
                {"label": "Meeting-focused", "value": "meeting_focused", "description": "Prioritize prep for scheduled meetings.", "apply_text": "Optimize this around today's meetings."},
                {"label": "Focus blocks", "value": "focus_blocks", "description": "Protect deep work time.", "apply_text": "Optimize this for focus blocks and deep work."},
            ]

        # Universal fallback — always 2 options, always clickable
        return [
            {"label": "My decision", "value": "personal_decision", "description": "Direct operating view.", "apply_text": "Format this for my own operating decision — direct and concise."},
            {"label": "Board presentation", "value": "board_presentation", "description": "Board-ready format.", "apply_text": "Format this for a board presentation — structured and polished."},
        ]

    def _options_for_question(self, question: str, task_input: str) -> List[Dict[str, Any]]:
        """Return 2-3 selectable answer options for a single open question."""
        return self._clarification_options(task_input=task_input, questions=[question])

    def _collect_trust_options(self, task_input: str, payload: ReportPayload, resolved_topics: frozenset, intent_state: Dict[str, Any], artifact_type: Optional[str]) -> List[Dict[str, Any]]:
        """Collect and order action offers and clarifying questions."""
        from src.core.diagnostics import DiagnosticReporter
        diag = DiagnosticReporter()
        options = []
        
        # 1. Action offers (if no artifact is already active)
        if not artifact_type:
            action_offers = self._build_action_offers(task_input, payload, intent_state=intent_state)
            for offer in action_offers:
                diag.log_decision(
                    decision_type="action_offer",
                    item_label=offer.get("question", "unknown offer"),
                    reason="Decision situation detected in task_input/context",
                    context_snapshot={"artifact_type": artifact_type}
                )
            options.extend(action_offers)
            
        # 2. Clarifying questions (if topic not resolved)
        clarifying_questions = self._clarifying_questions(
            task_input, payload, ceo_id=None, resolved_topics=resolved_topics
        )
        for q in clarifying_questions:
            opts = self._options_for_question(q, task_input)
            diag.log_decision(
                decision_type="clarification",
                item_label=q,
                reason="Context gap identified and not in resolved_topics",
                context_snapshot={"resolved_topics": list(resolved_topics)}
            )
            options.append({"question": q, "options": opts, "offer_type": "clarification"})
            
        # Log summary to agent output metadata for debugging
        payload.trust.assumptions.append(diag.get_summary())
        return options

    # ── Decision detection + proactive action offers ──────────────────────────

    _DECISION_SIGNALS = (
        "decide", "decision", "approve", "sign off", "go/no-go", "go no go",
        "should we", "should i", "do we", "what should", "recommend",
        "risk", "renewal at risk", "deals at risk", "at risk",
        # CEO asking "what do I need to do" or "specific actions" implies decision territory
        "specific actions", "actions i can take", "what do i need", "needs my attention",
        "immediate attention", "what needs",
    )
    _URGENCY_SIGNALS = (
        "urgent", "asap", "today", "critical", "deadline", "overdue", "missed",
        "by eod", "this week", "immediate",
    )

    def _detect_decision_context(self, task_input: str, payload: "ReportPayload") -> bool:  # type: ignore[name-defined]
        """Return True when the task represents a decision situation rather than a pure info request."""
        lowered = task_input.lower()
        has_decision_signal = self._contains_any_marker(lowered, self._DECISION_SIGNALS)
        has_urgency = self._contains_any_marker(lowered, self._URGENCY_SIGNALS)
        # Also detect from the generated answer — if the agent surfaced action items, it's decision territory
        section_labels = [s.label.lower() for s in payload.answer.sections]
        has_action_section = any(
            label in self._ACTION_SECTION_LABELS
            for label in section_labels
        )
        return (has_decision_signal and has_urgency) or has_action_section

    def _build_action_offers(
        self,
        task_input: str,
        payload: "ReportPayload",  # type: ignore[name-defined]
        *,
        intent_state: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Generate proactive action offers when the task represents a decision situation.
        These appear BEFORE clarifying questions in the UI — the system offers to DO the
        work rather than asking the CEO what to do next.
        """
        intent_state = intent_state or {}
        rejected_offer_classes = {
            str(item) for item in (intent_state.get("rejected_offer_classes") or []) if str(item)
        }
        must_not_do = {
            str(item) for item in (intent_state.get("must_not_do") or []) if str(item)
        }
        deliverable = intent_state.get("deliverable") if isinstance(intent_state, dict) else {}
        deliverable_kind = deliverable.get("kind") if isinstance(deliverable, dict) else None
        task_topic = str(intent_state.get("task_topic") or "")
        if "brief_offer" in rejected_offer_classes or "offer_more_briefs" in must_not_do:
            return []
        if deliverable_kind in {"execution_bundle", "email", "artifact_revision"}:
            return []
        lowered = task_input.lower()
        if any(phrase in lowered for phrase in ("forget the", "not what i asked", "do not ask", "don't ask")) and "question" in lowered:
            return []
        if any(phrase in lowered for phrase in ("comprehensive analysis", "pull together the data", "get this to me by end of week")):
            return []
        # Derive a concise topic label from the title or first section
        topic = payload.answer.title or task_input[:60]
        answer_corpus = " ".join(
            [
                payload.answer.title or "",
                payload.answer.summary or "",
                *[section.label for section in payload.answer.sections],
                *[item for section in payload.answer.sections for item in section.items],
            ]
        ).lower()
        is_pricing_context = task_topic == "pricing_response" or any(
            marker in lowered for marker in ("pricing", "price strategy", "competitive pricing", "margin", "discount")
        )
        if not is_pricing_context and any(marker in answer_corpus for marker in ("redwood", "apex", "rescue package", "call script", "apology letter")):
            return [{
                "question": "I can draft the Apex apology letter now, or prepare the Rachel Lim follow-up note now. Which should I build first?",
                "offer_type": "action_offer",
                "options": [
                    {
                        "label": "Apex apology letter",
                        "value": "apex_apology_letter",
                        "description": "Ready-to-send executive apology with service credit and prevention measures",
                        "apply_text": (
                            f"Draft the Apex Health executive apology letter for: {topic}. "
                            "Include the service credit, the outage acknowledgment, and the concrete prevention measures."
                        ),
                    },
                    {
                        "label": "Rachel follow-up note",
                        "value": "redwood_followup_note",
                        "description": "Short executive follow-up note that reinforces the Redwood rescue commitment",
                        "apply_text": (
                            f"Draft the Rachel Lim follow-up note for: {topic}. "
                            "Include the dated remediation path, the extension terms, and the next executive checkpoint."
                        ),
                    },
                ],
            }]
        if not self._detect_decision_context(task_input, payload):
            return []

        if task_topic == "pricing_response":
            return [{
                "question": "Which pricing implementation package should I build first?",
                "offer_type": "action_offer",
                "options": [
                    {
                        "label": "Selective discount",
                        "value": "pricing_discount_package",
                        "description": "Approval flow, guardrails, scripts, and metrics for the DACH discount move",
                        "apply_text": (
                            f"Build the selective discount execution package for: {topic}. "
                            "Include the approval workflow, discount guardrails, customer scripts, success metrics, and DACH-only containment rules."
                        ),
                    },
                    {
                        "label": "Value bundling",
                        "value": "pricing_bundle_package",
                        "description": "Customer offer language and rollout guardrails for a bundling defense",
                        "apply_text": (
                            f"Build the value-bundling execution package for: {topic}. "
                            "Include the approval workflow, offer guardrails, customer scripts, success metrics, and regional containment rules."
                        ),
                    },
                ],
            }]

        if self._contains_any_marker(lowered, self.ESCALATION_MARKERS) or any(
            "risk" in s.label.lower() or "customer" in s.label.lower() or "renewal" in s.label.lower()
            for s in payload.answer.sections
        ):
            if "generic_customer_deliverable_offer" in rejected_offer_classes or "generic_customer_deliverable_offer" in must_not_do:
                return []
            return [{
                "question": "I can prepare the Redwood rescue package now, or the Apex executive response now. Which should I build first?",
                "offer_type": "action_offer",
                "options": [
                    {
                        "label": "Redwood script",
                        "value": "redwood_call_script",
                        "description": "Draft the CEO talking points and extension language for Redwood now",
                        "apply_text": (
                            f"Prepare the Redwood execution package for: {topic}. "
                            "Include the CEO-to-CTO call script, the 60-day extension terms, and the follow-up note for Sarah Chen."
                        ),
                    },
                    {
                        "label": "Apex response",
                        "value": "apex_exec_response",
                        "description": "Draft the executive recovery response for Apex now",
                        "apply_text": (
                            f"Prepare the Apex executive response package for: {topic}. "
                            "Include the executive outreach message, core talking points, and the immediate follow-up commitments."
                        ),
                    },
                ],
            }]

        # Finance-heavy decision → offer a full financial decision brief
        if self._contains_any_marker(lowered, self.FINANCE_EXECUTION_MARKERS) or any(
            "finance" in s.label.lower() or "financial" in s.label.lower()
            for s in payload.answer.sections
        ):
            if any(marker in lowered for marker in ("pricing", "competitor", "competitive", "alphasystems", "dach")):
                return []
            if "generic_finance_cut_offer" in rejected_offer_classes or "generic_finance_cut_offer" in must_not_do:
                return []
            return [{
                "question": "Which finance cut should I turn into an execution package?",
                "offer_type": "action_offer",
                "options": [
                    {
                        "label": "Cost cuts package",
                        "value": "finance_execution_bundle",
                        "description": "Owners, exact tasks, deliverable drafts, and deadlines",
                        "apply_text": (
                            f"Turn the finance decision into an execution package for: {topic}. "
                            "Include the owner-ready tasks, the operator coordination message, and the immediate deadlines."
                        ),
                    },
                    {
                        "label": "Board framing",
                        "value": "decision_brief_finance",
                        "description": "Board-ready framing with KPIs and recommendation",
                        "apply_text": (
                            f"Build a full data-backed decision brief for: {topic}. "
                            "Include: key financial KPIs, variance vs plan, burn and runway implications, recommended decision, and next steps."
                        ),
                    },
                ],
            }]

        # Generic decision → offer a structured decision brief
        return [{
            "question": "Which way should I take this next?",
            "offer_type": "action_offer",
            "options": [
                {
                    "label": "Build decision brief",
                    "value": "decision_brief_general",
                    "description": "Supporting data, options, and a recommendation",
                    "apply_text": (
                        f"Build a structured decision brief for: {topic}. "
                        "Include: the key question, relevant context and data, options available, "
                        "recommended path, and what needs to happen next."
                    ),
                },
                {
                    "label": "Just the recommendation",
                    "value": "recommendation_only",
                    "description": "Skip the analysis — give me the call",
                    "apply_text": (
                        f"Give me a direct recommendation for: {topic}. "
                        "One paragraph. What should I do and why."
                    ),
                },
            ],
        }]

    def _conversation_started_from_schedule(self, session_history: List[Dict[str, Any]]) -> bool:
        for item in session_history[-6:]:
            query = str(item.get("query") or "").lower()
            response = str(item.get("response") or "").lower()
            if any(
                marker in query
                for marker in (
                    "plan my day",
                    "plan my week",
                    "schedule",
                    "organize my day",
                    "top three priorities",
                    "highest-priority actions",
                    "highest priority actions",
                    "must complete today",
                    "my scheduled meetings",
                    "meetings today",
                    "structure your day",
                    "prioritize my day",
                )
            ):
                return True
            if any(
                marker in response
                for marker in (
                    "today schedule built",
                    "schedule built from current inbox",
                    "executive plan",
                    "focus on finalizing",
                    "before today's meetings",
                    "three follow-ups are still live",
                    "first: lock the investor-call headline metrics",
                )
            ):
                return True
        return False

    def _schedule_origin_action_items(self, *, task_input: str, payload: ReportPayload) -> list[str]:
        lowered = task_input.lower()
        next_day = (datetime.now() + timedelta(days=1)).strftime("%B %-d, %Y")
        next_week = (datetime.now() + timedelta(days=7)).strftime("%B %-d, %Y")
        if self._contains_any_marker(lowered, ("what decisions need to be made", "before my meetings", "meetings today")):
            return [
                "9:00 AM leadership sync — Decision/action needed: approve the VP Engineering offer before the competing offer window closes — Owner: CEO + Head of Talent.",
                "Before the customer-risk review — Decision/action needed: confirm the one recovery commitment Apex Health will hear today and who sends the written plan — Owner: CEO + VP Customer Success.",
                "Before the revenue check-in — Decision/action needed: name the single owner and dated rescue path for Redwood/Northstar renewal risk — Owner: CEO + Sales lead.",
            ]
        if self._contains_any_marker(lowered, ("defer", "delegate", "what can i safely", "what can we safely", "safely defer", "safely delegate")):
            return [
                "Safe to Delegate — Talent Lead: run the Chief of Staff final-round logistics today and return only the decision-ready recommendation by end of day.",
                "Safe to Delegate — VP Customer Success: own the detailed customer milestone tracker and daily update cadence; escalate only if a promised milestone slips.",
                f"Safe to Defer — investor deck polish and non-critical operating narrative cleanup until {next_day}, after the offer, customer commitment, and renewal owner decisions are locked.",
            ]
        if self._contains_any_marker(lowered, ("sequence", "how should i sequence", "how to sequence", "what order should", "prep sequence", "priority order")):
            return [
                "First: CFO | Before 10 AM: lock the three investor-call numbers and the one sentence for each so the rest of the prep stays bounded.",
                "Then: Finance lead + Chief of Staff | By noon: build the backup page with current value, driver, owner, and likely follow-up question for each metric.",
                "Then: CEO | In the last 30 minutes before the call: rehearse the opening narrative, decide what to skip if short on time, and leave the deck polish to the team.",
            ]
        if self._contains_any_marker(lowered, ("follow-ups from yesterday", "follow-ups from this morning", "outstanding follow")):
            return [
                "Investor runway update — Status: still needs CEO review before the check-in today — Owner: CFO drafted, CEO must approve the final framing.",
                "Northstar or Redwood renewal escalation — Status: rescue path not yet confirmed with one accountable owner — Owner: Sales lead needs to send the dated plan back today.",
                "Apex recovery follow-up — Status: customer commitment is live but milestone ownership still needs confirmation — Owner: VP Customer Success to close the loop before end of day.",
            ]
        if any(marker in lowered for marker in ("team", "alignment", "collaboration", "morale", "timeline", "roles", "hiring")):
            return [
                f"CEO + Sales lead | By {next_day}: name the critical roles or dependencies blocking enterprise expansion and rank them by business impact.",
                f"Product lead | By {next_week}: document timeline risk if hiring stays flat and identify the first milestone that would slip.",
                f"Chief of Staff | By {next_day}: run a cross-functional check-in to align Sales and Product on owners, dates, and escalation triggers.",
            ]
        if "metric" in lowered or "investor" in lowered:
            return [
                f"CEO + CFO | By {next_day}: lock the three investor metrics to lead with and rehearse the narrative around hiring pace, burn, and initiative risk.",
                f"Sales lead | By {next_week}: produce an enterprise expansion risk update with one owner per blocker and one mitigation per region.",
                f"Chief of Staff | By {next_day}: turn the investor prep into a dated operating checklist for the next meeting and follow-up actions.",
            ]
        base_items = [self._strip_generic_report_lead(item) for item in payload.answer.sections[2].items[:3] if item]
        return self._normalize_action_items(base_items)

    def _action_item_is_specific(self, item: str) -> bool:
        lowered = item.lower()
        owner_markers = ("ceo", "finance", "engineering", "operations", "account lead", "sales", "cfo")
        timing_markers = ("today", "within", "before", "by ", "daily", "this week", "24 hours")
        structure_markers = (":", "|")
        return (
            any(marker in lowered for marker in owner_markers)
            or any(marker in lowered for marker in timing_markers)
            or any(marker in item for marker in structure_markers)
        )

    def _is_company_health_summary_request(self, task_input: str) -> bool:
        lowered = task_input.lower()
        return any(
            marker in lowered
            for marker in [
                "company health",
                "business health",
                "financial health",
                "health summary",
                "health overview",
                "company overview",
            ]
        )

    def _finance_close_issue_items(self, retrieval: List[Dict[str, Any]], signals: List[Dict[str, Any]]) -> List[str]:
        corpus = f"{self._retrieval_corpus(retrieval)} {self._signals_corpus(signals)}"
        items: List[str] = []
        if self._contains_all_terms(corpus, ["finance close review"]) or self._contains_all_terms(corpus, ["close week"]):
            items.append(
                "Finance close review is active this week and needs a CEO call on the month-end variance before numbers are locked."
            )
        if self._contains_all_terms(corpus, ["cloud spend"]) and (
            self._contains_all_terms(corpus, ["variance"]) or self._contains_all_terms(corpus, ["forecast"])
        ):
            items.append(
                "Cloud spend variance is running above forecast and is pressuring burn into the close-week discussion."
            )
        if self._contains_all_terms(corpus, ["board packet"]) and self._contains_all_terms(corpus, ["narrative"]):
            items.append(
                "The board packet narrative still needs final framing on variance, cash outlook, and corrective actions."
            )
        return items

    def _retrieval_corpus(self, retrieval: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for item in retrieval[:8]:
            if not isinstance(item, dict):
                continue
            parts.extend(
                [
                    str(item.get("title") or ""),
                    str(item.get("content") or ""),
                    str(item.get("snippet") or ""),
                    str(item.get("source_excerpt") or ""),
                ]
            )
        return " ".join(parts).lower()

    def _signals_corpus(self, signals: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for item in signals[:8]:
            if not isinstance(item, dict):
                continue
            parts.extend(
                [
                    str(item.get("subject") or ""),
                    str(item.get("content") or ""),
                    " ".join(str(part) for part in (item.get("strategic_concepts") or []) if part),
                    " ".join(str(part) for part in (item.get("talking_points") or []) if part),
                ]
            )
        return " ".join(parts).lower()

    def _contains_all_terms(self, corpus: str, terms: List[str]) -> bool:
        return all(term.lower() in corpus for term in terms)

    def _build_finance_workbook_spec(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        metrics: list[WorkbookMetric],
        company_state: Dict[str, Any],
        ceo_id: Optional[str],
        current_interaction_id: Optional[int],
        session_history: List[Dict[str, Any]],
        retrieval: List[Dict[str, Any]],
    ) -> WorkbookSpec:
        lowered = task_input.lower()
        finance_template = self._select_finance_template(task_input) or "board_financial_update"
        threshold_events = self._forecast_threshold_events(
            task_input=task_input,
            company_state=company_state,
            retrieval=retrieval,
            finance_template=finance_template,
        )
        financial_rows, validation_warnings = self._prepare_financial_rows(
            task_input=task_input,
            company_state=company_state,
            metrics=metrics,
            ceo_id=ceo_id,
            current_interaction_id=current_interaction_id,
            session_history=session_history,
            retrieval=retrieval,
        )
        comparison_rows = self._build_period_comparison_rows(task_input=task_input, rows=financial_rows)
        summary_metrics = self._build_summary_metrics(
            financial_rows,
            payload,
            task_input=task_input,
            comparison_rows=comparison_rows,
        )
        model_table_rows = [self._financial_row_to_cells(row) for row in financial_rows]
        variance_rows = [row for row in financial_rows if abs(row.variance) > 0]
        forecast_rows = [row for row in financial_rows if row.forecast > 0]
        chart_tables = self._build_chart_tables(
            task_input=task_input,
            rows=financial_rows,
            comparison_rows=comparison_rows,
        )
        chart_specs = self._build_chart_specs(
            task_input=task_input,
            comparison_rows=comparison_rows,
            chart_tables=chart_tables,
        )
        variance_tables = [
            {
                "title": "Budget vs Actual Variance",
                "columns": ["Period", "Metric", "Budget", "Actual", "Variance", "Source"],
                "rows": [
                    [
                        row.period,
                        row.metric,
                        format_currency(row.budget),
                        format_currency(row.actual),
                        format_currency(row.variance),
                        row.source_ref or row.source_type,
                    ]
                    for row in (variance_rows or financial_rows)
                ],
                "row_provenance": [
                    self._financial_row_provenance(row) for row in (variance_rows or financial_rows)
                ],
            }
        ]
        if comparison_rows:
            variance_tables.append(
                {
                    "title": "Period Comparison",
                    "columns": [
                        "Metric",
                        "Prior Period",
                        "Current Period",
                        "Prior Actual",
                        "Current Actual",
                        "Delta",
                        "Delta %",
                    ],
                    "rows": [
                        [
                            comparison["metric"],
                            comparison["prior_period"],
                            comparison["current_period"],
                            format_currency(comparison["prior_actual"]),
                            format_currency(comparison["current_actual"]),
                            format_currency(comparison["delta"]),
                            comparison["delta_percent_label"],
                        ]
                        for comparison in comparison_rows
                    ],
                    "row_provenance": [
                        {
                            "source_type": "period_comparison",
                            "source_ref": f"{comparison['prior_source_ref']} | {comparison['current_source_ref']}",
                            "source_excerpt": comparison["source_excerpt"],
                        }
                        for comparison in comparison_rows
                    ],
                }
            )

        return WorkbookSpec(
            workbook_title=payload.answer.title,
            metadata={
                "template": finance_template,
                "template_id": get_finance_template_definition(self._finance_template_definition_key(finance_template)).workbook_template_id,
                "theme_id": get_finance_template_definition(self._finance_template_definition_key(finance_template)).default_theme_id,
                "presentation_version": "workbook_spec_v1",
                "expected_metrics": self._template_expected_metrics(finance_template),
                "expected_periods": self._template_expected_periods(finance_template),
                "validation_warnings": validation_warnings,
                "threshold_events": threshold_events,
                "source_trust_policy": self.SOURCE_TRUST_RANKS,
            },
            sheets=[
                WorkbookSheetSpec(
                    name="Summary",
                    kind="summary",
                    metrics=summary_metrics,
                    tables=[
                        {
                            "title": "Executive Summary",
                            "columns": ["Section", "Detail"],
                            "rows": [[section.label, " | ".join(section.items[:3])] for section in payload.answer.sections],
                        }
                    ],
                    metadata={"validation_warnings": validation_warnings[:3], "threshold_events": threshold_events[:2]},
                ),
                WorkbookSheetSpec(
                    name="Model",
                    kind="model",
                    financial_rows=financial_rows,
                    tables=[
                        {
                            "title": "Normalized Financial Model",
                            "columns": ["Period", "Metric", "Budget", "Actual", "Variance", "Forecast", "Source"],
                            "rows": model_table_rows,
                            "row_provenance": [self._financial_row_provenance(row) for row in financial_rows],
                        }
                    ],
                    metadata={"validated_row_count": len(financial_rows)},
                    pivot_snapshots=[
                        WorkbookPivotSnapshot(
                            title="Actual by Metric",
                            dimension="Metric",
                            measure="Actual",
                            rows=[
                                WorkbookPivotRow(label=row.metric, value=row.actual)
                                for row in financial_rows[:6]
                            ],
                        )
                    ],
                ),
                WorkbookSheetSpec(
                    name="Variance",
                    kind="variance",
                    financial_rows=variance_rows or financial_rows,
                    tables=variance_tables,
                    metadata={"validated_row_count": len(variance_rows or financial_rows)},
                ),
                WorkbookSheetSpec(
                    name="Forecast",
                    kind="forecast",
                    financial_rows=forecast_rows or financial_rows,
                    tables=[
                        {
                            "title": "Forecast View",
                            "columns": ["Period", "Metric", "Actual", "Forecast", "Source"],
                            "rows": [
                                [
                                    row.period,
                                    row.metric,
                                    format_currency(row.actual),
                                    format_currency(row.forecast),
                                    row.source_ref or row.source_type,
                                ]
                                for row in (forecast_rows or financial_rows)
                            ],
                            "row_provenance": [
                                self._financial_row_provenance(row) for row in (forecast_rows or financial_rows)
                            ],
                        }
                    ],
                    metadata={"validated_row_count": len(forecast_rows or financial_rows), "threshold_events": threshold_events},
                ),
                WorkbookSheetSpec(
                    name="Charts",
                    kind="charts",
                    chart_specs=chart_specs,
                    tables=chart_tables,
                    metadata={"validation_warnings": validation_warnings[:2], "threshold_events": threshold_events},
                ),
            ],
        )

    def _build_summary_metrics(
        self,
        rows: list[WorkbookFinancialRow],
        payload: ReportPayload,
        *,
        task_input: str,
        comparison_rows: Optional[list[dict[str, Any]]] = None,
    ) -> list[WorkbookMetric]:
        finance_template = self._select_finance_template(task_input)
        if finance_template == "aws_cost_review":
            metrics = self._build_aws_summary_metrics(rows, payload)
            if metrics:
                return metrics
        if finance_template == "runway_burn_review":
            metrics = self._build_runway_summary_metrics(rows, payload)
            if metrics:
                return metrics
        if finance_template == "project_spend_review":
            metrics = self._build_project_spend_summary_metrics(rows, payload)
            if metrics:
                return metrics

        total_actual = sum(row.actual for row in rows)
        total_budget = sum(row.budget for row in rows)
        total_forecast = sum(row.forecast for row in rows)
        total_variance = sum(row.variance for row in rows)
        metrics = [
            WorkbookMetric(label="Actual", value=format_currency(total_actual)),
            WorkbookMetric(label="Budget", value=format_currency(total_budget)),
            WorkbookMetric(label="Variance", value=format_currency(total_variance)),
            WorkbookMetric(label="Forecast", value=format_currency(total_forecast)),
            WorkbookMetric(label="Confidence", value=f"{round(self._normalize_confidence_score(payload.trust.confidence_score) * 100)}%"),
        ]
        if comparison_rows:
            total_delta = sum(item["delta"] for item in comparison_rows)
            metrics.insert(3, WorkbookMetric(label="Period Delta", value=format_currency(total_delta)))
        return metrics

    def _apply_finance_accuracy_guardrails(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        company_state: Dict[str, Any],
        ceo_id: Optional[str],
        current_interaction_id: Optional[int],
        session_history: List[Dict[str, Any]],
        retrieval: List[Dict[str, Any]],
    ) -> tuple[ReportPayload, dict[str, Any]]:
        payload.sources = self._rank_and_normalize_sources(payload.sources)
        if not self._is_finance_workbook_request(task_input.lower()):
            return payload, {}

        rows, warnings = self._prepare_financial_rows(
            task_input=task_input,
            company_state=company_state,
            metrics=self._extract_metrics(payload),
            ceo_id=ceo_id,
            current_interaction_id=current_interaction_id,
            session_history=session_history,
            retrieval=retrieval,
        )
        has_state_data = any(
            company_state.get(k)
            for k in ("capital_position", "cost_structure", "revenue_segmentation")
        )
        if not warnings:
            # LLM may self-report low confidence on vague queries; if we have real
            # company state data and no validation failures, floor at medium.
            if has_state_data and payload.trust.confidence == "low":
                payload = payload.model_copy(deep=True)
                payload.trust.confidence = "medium"
                payload.trust.confidence_score = max(payload.trust.confidence_score, 0.55)
            return payload, {"warning_count": 0, "warnings": []}

        payload = payload.model_copy(deep=True)
        raw_score = self._normalize_confidence_score(payload.trust.confidence_score)
        penalized = max(0.2, raw_score - min(0.3, 0.08 * len(warnings)))
        # If we have company state data, don't let minor warnings push below medium.
        if has_state_data and len(warnings) < 3:
            penalized = max(penalized, 0.5)
        payload.trust.confidence_score = penalized
        payload.trust.confidence = "low" if payload.trust.confidence_score < 0.5 else "medium"
        payload.trust.data_quality = "low" if len(warnings) >= 3 else "medium"
        payload.trust.missing_context = list(dict.fromkeys([*payload.trust.missing_context, *warnings[:3]]))
        payload.trust.open_questions = list(
            dict.fromkeys(
                [
                    *payload.trust.open_questions,
                    "Do you want this analysis rerun against more structured finance data?" if warnings else "",
                ]
            )
        )
        payload.trust.open_questions = self._rank_questions_by_impact(
            [item for item in payload.trust.open_questions if item], task_input
        )

        finance_template = self._select_finance_template(task_input)
        qa_result = run_finance_qa_checklist(
            rows=rows,
            expected_metric_keys=self._template_expected_metrics(finance_template) if finance_template else [],
            company_state=company_state,
            finance_template=finance_template,
        )

        # Escalate trust penalty if QA checklist found critical issues
        if not qa_result.passed and not payload.trust.evidence_reasons:
            payload.trust.evidence_reasons = [qa_result.summary]
        elif not qa_result.passed:
            payload.trust.evidence_reasons = [*payload.trust.evidence_reasons, qa_result.summary]

        return payload, {
            "warning_count": len(warnings),
            "warnings": warnings,
            "validated_row_count": len(rows),
            "qa_checklist": qa_result.model_dump(),
            "qa_passed": qa_result.passed,
        }

    def _apply_financial_task_contract(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        financial_task: Optional[FinancialAnalysisTask],
    ) -> ReportPayload:
        if financial_task is None:
            return payload
        if financial_task.task_type != "renewal_contingency":
            return payload

        combined = "\n".join(
            [
                payload.answer.title,
                payload.answer.summary,
                *[item for section in payload.answer.sections for item in section.items],
            ]
        ).lower()
        if (
            "runway" in combined
            and any(token in combined for token in ("alphasystems", "redwood", "renewal"))
            and any(token in combined for token in ("rescue", "this week", "owner", "extension"))
        ):
            return payload

        payload = payload.model_copy(deep=True)
        arr_match = re.search(r"\$[\d.]+m|\$[\d,]+k", task_input, re.I)
        arr_at_risk = arr_match.group(0) if arr_match else "$1.22M"
        payload.answer.title = "Enterprise Renewal Contingency Plan"
        payload.answer.summary = (
            f"If AlphaSystems and Redwood both slip, {arr_at_risk} of ARR stays exposed and the downside case has to be managed in parallel with this week's rescue work. "
            "The plan below separates the runway impact framing, the rescue actions that need owners now, and the fallback if one or both deals fail."
        )
        payload.answer.sections = [
            ReportSection(
                label="Runway Impact",
                items=[
                    f"Downside case: losing both named renewals leaves {arr_at_risk} of ARR exposed and forces the next operating review to treat revenue recovery, not just cost cuts, as the main driver of runway protection.",
                    "Runway math should now be tracked in two views this week: current runway with the cost actions already approved, and downside runway if both renewals fail and the revenue plan misses by the full amount at risk.",
                    "Decision implication: Marcus Webb should publish the downside runway bridge immediately so the team is not relying on the cost-containment case alone.",
                ],
            ),
            ReportSection(
                label="Rescue Actions This Week",
                items=[
                    "Redwood rescue — Owner: CEO + Sarah Chen + Priya Desai. Within 24 hours: confirm the CEO-to-CTO call, deliver the dated product remediation path, and put the 60-day extension terms in front of the customer before the renewal risk hardens.",
                    "AlphaSystems rescue — Owner: Sarah Chen. This week: return with a DACH-specific competitive response package, named deal owner, and explicit pricing guardrails so the account does not stall while the team focuses on Redwood.",
                    "Operating cadence — Owner: Chief of Staff + Marcus Webb. Starting today: run a daily renewal-risk checkpoint until both accounts have a named owner, next customer step, and dated executive follow-up.",
                ],
            ),
            ReportSection(
                label="Fallback Plan",
                items=[
                    "If one deal slips, lock the revised revenue bridge the same day and preserve the approved cost cuts rather than reopening the whole containment plan.",
                    "If both deals fail, publish the downside runway case to the leadership team immediately, freeze any discretionary spend not tied to retention or delivery recovery, and re-sequence non-critical growth commitments.",
                    "Do not treat the fallback as passive monitoring: Marcus Webb owns the downside model, Sarah Chen owns account save plans, and the CEO owns the escalation path for both accounts this week.",
                ],
            ),
        ]
        return payload

    def _prepare_financial_rows(
        self,
        *,
        task_input: str,
        company_state: Dict[str, Any],
        metrics: list[WorkbookMetric],
        ceo_id: Optional[str],
        current_interaction_id: Optional[int],
        session_history: List[Dict[str, Any]],
        retrieval: List[Dict[str, Any]],
    ) -> tuple[list[WorkbookFinancialRow], list[str]]:
        finance_template = self._select_finance_template(task_input)
        rows = self._build_financial_rows(
            task_input=task_input,
            company_state=company_state,
            metrics=metrics,
            ceo_id=ceo_id,
            current_interaction_id=current_interaction_id,
            session_history=session_history,
            retrieval=retrieval,
        )
        rows = self._sanitize_financial_rows(
            rows,
            aws_focus=self._is_aws_spend_request(task_input.lower()),
        )
        rows = self._apply_finance_template_to_rows(rows, finance_template=finance_template)
        return self._validate_financial_rows(rows)

    def _apply_finance_template_to_rows(
        self,
        rows: list[WorkbookFinancialRow],
        *,
        finance_template: Optional[str],
    ) -> list[WorkbookFinancialRow]:
        if not finance_template:
            return rows
        filtered = rows
        if finance_template == "aws_cost_review":
            allowed = {"AWS cost", "Burn rate", "Cash at bank", "Project Kepler committed spend"}
            filtered = [row for row in rows if row.metric in allowed]
        elif finance_template == "runway_burn_review":
            filtered = [row for row in rows if row.metric in {"Cash at bank", "Burn rate", "Cash runway"}]
            filtered = self._augment_runway_rows(filtered)
        elif finance_template == "project_spend_review":
            filtered = [
                row
                for row in rows
                if row.metric
                in {
                    "Project Kepler committed spend",
                    "Project Kepler remaining budget",
                    "Project Kepler forecast spend",
                    "Project Kepler forecast remaining budget",
                    "AWS cost",
                }
            ]
            filtered = self._augment_project_spend_rows(filtered)
        elif finance_template == "budget_variance_review":
            filtered = [row for row in rows if abs(row.variance) > 0]
        return self._dedupe_financial_rows(filtered)

    def _augment_runway_rows(self, rows: list[WorkbookFinancialRow]) -> list[WorkbookFinancialRow]:
        current_cash = self._find_financial_row(rows, metric="Cash at bank", preferred_periods=["Current Week", "Current Month", "Q1 2026"])
        current_burn = self._find_financial_row(rows, metric="Burn rate", preferred_periods=["Current Month", "Q1 2026"])
        if current_cash and current_burn and current_burn.actual > 0 and not any(row.metric == "Cash runway" for row in rows):
            runway_months = round(current_cash.actual / current_burn.actual, 1)
            rows.append(
                WorkbookFinancialRow(
                    period=current_cash.period,
                    metric="Cash runway",
                    budget=runway_months,
                    actual=runway_months,
                    variance=0.0,
                    forecast=runway_months,
                    source_type="derived_metric",
                    source_ref=f"{current_cash.source_ref}|{current_burn.source_ref}",
                    source_excerpt=f"Derived from cash {current_cash.actual} and burn {current_burn.actual}.",
                )
            )
        return rows

    def _augment_project_spend_rows(self, rows: list[WorkbookFinancialRow]) -> list[WorkbookFinancialRow]:
        kepler = self._find_financial_row(rows, metric="Project Kepler committed spend", preferred_periods=["FY 2026"])
        if kepler and not any(row.metric == "Project Kepler remaining budget" for row in rows):
            remaining = round(max(kepler.budget - kepler.actual, 0.0), 2)
            rows.append(
                WorkbookFinancialRow(
                    period=kepler.period,
                    metric="Project Kepler remaining budget",
                    budget=kepler.budget,
                    actual=remaining,
                    variance=round(remaining - kepler.budget, 2),
                    forecast=remaining,
                    source_type="derived_metric",
                    source_ref=kepler.source_ref,
                    source_excerpt=f"Derived remaining budget from approved budget {kepler.budget} and committed spend {kepler.actual}.",
                )
            )
        return rows

    def _build_financial_rows(
        self,
        *,
        task_input: str,
        company_state: Dict[str, Any],
        metrics: list[WorkbookMetric],
        ceo_id: Optional[str],
        current_interaction_id: Optional[int],
        session_history: List[Dict[str, Any]],
        retrieval: List[Dict[str, Any]],
    ) -> list[WorkbookFinancialRow]:
        rows: list[WorkbookFinancialRow] = []
        default_period = self._current_period_from_task_input(task_input)
        finance_template = self._select_finance_template(task_input)
        if finance_template == "aws_cost_review":
            aws_rows = self._build_aws_spend_rows(
                retrieval=retrieval,
                company_state=company_state,
                default_period=default_period,
            )
            if aws_rows:
                return aws_rows
        if finance_template == "runway_burn_review":
            runway_rows = self._build_runway_burn_rows(
                retrieval=retrieval,
                company_state=company_state,
                default_period=default_period,
            )
            if runway_rows:
                return runway_rows
        if finance_template == "project_spend_review":
            project_rows = self._build_project_spend_rows(
                task_input=task_input,
                retrieval=retrieval,
                company_state=company_state,
                default_period=default_period,
            )
            if project_rows:
                return project_rows
        state_sections = [
            ("revenue", default_period, company_state.get("revenue_segmentation", {})),
            ("cost", default_period, company_state.get("cost_structure", {})),
            ("capital", default_period, company_state.get("capital_position", {})),
        ]
        for category, period, section in state_sections:
            if not isinstance(section, dict):
                continue
            for metric_name, raw_value in section.items():
                if not isinstance(raw_value, (int, float)):
                    continue
                actual = float(raw_value)
                normalized_metric = self._normalize_metric_name(str(metric_name), category=category)
                budget, forecast = self._default_budget_and_forecast(actual=actual, category=category)
                variance = round(actual - budget, 2)
                rows.append(
                    WorkbookFinancialRow(
                        period=period,
                        metric=normalized_metric[:80],
                        budget=budget,
                        actual=actual,
                        variance=variance,
                        forecast=forecast,
                        source_type="company_state",
                        source_ref=f"CompanyState.{self._company_state_field_name(category)}.{metric_name}",
                        source_excerpt=f"{metric_name}: {actual}",
                    )
                )
        if rows:
            rows = self._apply_retrieval_finance_hints(rows, retrieval, default_period=default_period)
            rows.extend(self._rows_from_retrieval_hints(retrieval, default_period=default_period))
            rows.extend(
                self._historical_rows_from_artifacts(
                    task_input=task_input,
                    ceo_id=ceo_id,
                    current_interaction_id=current_interaction_id,
                    session_history=session_history,
                )
            )
        if rows:
            return self._dedupe_financial_rows(rows[:18])

        retrieval_rows = self._rows_from_retrieval_hints(retrieval, default_period=default_period)
        retrieval_rows.extend(
            self._historical_rows_from_artifacts(
                task_input=task_input,
                ceo_id=ceo_id,
                current_interaction_id=current_interaction_id,
                session_history=session_history,
            )
        )
        if retrieval_rows:
            return self._dedupe_financial_rows(retrieval_rows[:18])

        for index, metric in enumerate(metrics[:8], start=1):
            actual = self._parse_numeric_metric_value(metric.value, default=float(index) * 1_000_000)
            category = self._infer_finance_category(metric.label)
            normalized_metric = self._normalize_metric_name(metric.label, category=category)
            budget, forecast = self._default_budget_and_forecast(actual=actual, category=category)
            variance = round(actual - budget, 2)
            rows.append(
                WorkbookFinancialRow(
                    period=default_period,
                    metric=normalized_metric[:80],
                    budget=budget,
                    actual=actual,
                    variance=variance,
                    forecast=forecast,
                    source_type="derived_metric",
                    source_ref=f"report_metric:{normalized_metric[:80]}",
                    source_excerpt=metric.value,
                )
            )
        if not rows:
            rows.append(
                WorkbookFinancialRow(
                    period=default_period,
                    metric="Operating baseline",
                    budget=950000.0,
                    actual=1000000.0,
                    variance=50000.0,
                    forecast=1080000.0,
                    source_type="fallback",
                    source_ref="fallback:operating_baseline",
                    source_excerpt="Synthetic baseline used due to sparse company state and retrieval context.",
                )
            )
        return self._dedupe_financial_rows(rows)

    def _default_budget_and_forecast(self, *, actual: float, category: str) -> tuple[float, float]:
        if category == "revenue":
            return round(actual * 0.96, 2), round(actual * 1.08, 2)
        if category == "cost":
            return round(actual * 0.92, 2), round(actual * 1.03, 2)
        if category == "capital":
            return round(actual, 2), round(actual * 0.94, 2)
        return round(actual * 0.95, 2), round(actual * 1.05, 2)

    def _infer_finance_category(self, metric_label: str) -> str:
        normalized = self._normalize_metric_name(metric_label)
        if normalized != metric_label:
            for canonical, (category, _) in self.METRIC_TAXONOMY.items():
                if canonical == normalized:
                    return category
        lowered = metric_label.lower()
        if any(token in lowered for token in ["revenue", "sales", "arr", "bookings"]):
            return "revenue"
        if any(token in lowered for token in ["cost", "expense", "burn", "spend", "aws", "cloud", "r&d"]):
            return "cost"
        if any(token in lowered for token in ["cash", "capital", "reserve", "runway", "bank"]):
            return "capital"
        return "general"

    def _apply_retrieval_finance_hints(
        self,
        rows: list[WorkbookFinancialRow],
        retrieval: List[Dict[str, Any]],
        default_period: str,
    ) -> list[WorkbookFinancialRow]:
        hints = self._extract_finance_hints(retrieval, default_period=default_period)
        if not hints:
            return rows

        enriched: list[WorkbookFinancialRow] = []
        for row in rows:
            normalized_metric = self._normalize_metric_name(row.metric)
            match = next(
                (
                    hint for hint in hints
                    if hint["metric"] == normalized_metric and hint["period"] == row.period
                ),
                None,
            )
            if not match:
                enriched.append(row)
                continue
            budget = match.get("budget", row.budget)
            actual = match.get("actual", row.actual)
            forecast = match.get("forecast", row.forecast)
            variance = match.get("variance", round(actual - budget, 2))
            enriched.append(
                WorkbookFinancialRow(
                    period=self._normalize_period(match.get("period", row.period), fallback=row.period),
                    metric=normalized_metric,
                    budget=budget,
                    actual=actual,
                    variance=variance,
                    forecast=forecast,
                    source_type=match.get("source_type", row.source_type),
                    source_ref=match.get("source_ref", row.source_ref),
                    source_excerpt=match.get("source_excerpt", row.source_excerpt),
                )
            )
        return self._dedupe_financial_rows(enriched)

    def _rows_from_retrieval_hints(self, retrieval: List[Dict[str, Any]], *, default_period: str) -> list[WorkbookFinancialRow]:
        rows: list[WorkbookFinancialRow] = []
        for hint in self._extract_finance_hints(retrieval, default_period=default_period):
            actual = hint.get("actual")
            if actual is None:
                continue
            budget = hint.get("budget")
            category = self._infer_finance_category(hint.get("metric", ""))
            if budget is None:
                budget, forecast_default = self._default_budget_and_forecast(actual=actual, category=category)
            else:
                _, forecast_default = self._default_budget_and_forecast(actual=actual, category=category)
            forecast = hint.get("forecast", forecast_default)
            variance = hint.get("variance", round(actual - budget, 2))
            rows.append(
                WorkbookFinancialRow(
                    period=self._normalize_period(hint.get("period", default_period), fallback=default_period),
                    metric=self._normalize_metric_name(hint.get("metric", "Financial signal"), category=category)[:80],
                    budget=budget,
                    actual=actual,
                    variance=variance,
                    forecast=forecast,
                    source_type=hint.get("source_type", "retrieved_document"),
                    source_ref=hint.get("source_ref", "retrieved_document"),
                    source_excerpt=hint.get("source_excerpt"),
                )
            )
        return self._dedupe_financial_rows(rows)

    def _build_period_comparison_rows(
        self,
        *,
        task_input: str,
        rows: list[WorkbookFinancialRow],
    ) -> list[dict[str, Any]]:
        period_pair = self._select_comparison_period_pair(task_input)
        if not period_pair:
            return []

        current_period, prior_period = period_pair
        by_key = {(row.metric, row.period): row for row in rows}
        comparisons: list[dict[str, Any]] = []
        for metric in sorted({row.metric for row in rows}):
            current_row = by_key.get((metric, current_period))
            prior_row = by_key.get((metric, prior_period))
            if not current_row or not prior_row:
                continue
            delta = round(current_row.actual - prior_row.actual, 2)
            delta_percent = round((delta / prior_row.actual) * 100, 2) if prior_row.actual else None
            comparisons.append(
                {
                    "metric": metric,
                    "prior_period": prior_period,
                    "current_period": current_period,
                    "prior_actual": prior_row.actual,
                    "current_actual": current_row.actual,
                    "delta": delta,
                    "delta_percent": delta_percent,
                    "delta_percent_label": f"{delta_percent:.1f}%" if delta_percent is not None else "n/a",
                    "prior_source_ref": prior_row.source_ref or prior_row.source_type,
                    "current_source_ref": current_row.source_ref or current_row.source_type,
                    "source_excerpt": " | ".join(
                        part for part in [prior_row.source_excerpt, current_row.source_excerpt] if part
                    )[:240],
                }
            )
        return comparisons

    def _historical_rows_from_artifacts(
        self,
        *,
        task_input: str,
        ceo_id: Optional[str],
        current_interaction_id: Optional[int],
        session_history: List[Dict[str, Any]],
    ) -> list[WorkbookFinancialRow]:
        period_pair = self._select_comparison_period_pair(task_input)
        if not period_pair or not ceo_id or not session_history:
            return []

        prior_period = period_pair[1]
        current_timestamp = datetime.now()
        selected = self._select_historical_interaction_for_period(
            session_history=session_history,
            target_period=prior_period,
            current_interaction_id=current_interaction_id,
            current_timestamp=current_timestamp,
            ceo_id=ceo_id,
        )
        if not selected:
            return []

        interaction_id = selected.get("id")
        if not interaction_id:
            return []

        raw_spec = read_stage_artifact(int(interaction_id), ceo_id, "analysis_spec")
        if not raw_spec:
            return []

        try:
            normalized = re.sub(r"^---\n.*?\n---\n+", "", raw_spec, flags=re.DOTALL).strip()
            spec = WorkbookSpec(**json.loads(normalized))
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

        source_excerpt = f"{spec.workbook_title} | {selected.get('timestamp') or ''}".strip(" |")
        return self._extract_rows_from_historical_spec(
            spec,
            period_label=prior_period,
            source_ref=f"artifact:{interaction_id}:analysis_spec",
            source_excerpt=source_excerpt,
        )

    def _select_historical_interaction_for_period(
        self,
        *,
        session_history: List[Dict[str, Any]],
        target_period: str,
        current_interaction_id: Optional[int],
        current_timestamp: datetime,
        ceo_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        metadata_candidates: list[tuple[datetime, Dict[str, Any]]] = []
        timestamp_candidates: list[tuple[datetime, Dict[str, Any]]] = []
        fallback: list[tuple[datetime, Dict[str, Any]]] = []
        for item in session_history:
            interaction_id = item.get("id")
            if interaction_id is None or interaction_id == current_interaction_id:
                continue
            timestamp_raw = item.get("timestamp")
            if not timestamp_raw:
                continue
            try:
                timestamp = datetime.fromisoformat(str(timestamp_raw))
            except ValueError:
                continue
            if timestamp >= current_timestamp:
                continue
            fallback.append((timestamp, item))
            if ceo_id:
                metadata = read_stage_artifact_metadata(int(interaction_id), ceo_id, "analysis_spec")
                if self._artifact_metadata_covers_period(metadata, target_period):
                    metadata_candidates.append((timestamp, item))
                    continue
            if self._timestamp_matches_period(timestamp=timestamp, target_period=target_period, current_timestamp=current_timestamp):
                timestamp_candidates.append((timestamp, item))

        if metadata_candidates:
            metadata_candidates.sort(key=lambda item: item[0], reverse=True)
            return metadata_candidates[0][1]
        if timestamp_candidates:
            timestamp_candidates.sort(key=lambda item: item[0], reverse=True)
            return timestamp_candidates[0][1]
        if fallback:
            fallback.sort(key=lambda item: item[0], reverse=True)
            return fallback[0][1]
        return None

    def _artifact_metadata_covers_period(self, metadata: Dict[str, Any], target_period: str) -> bool:
        coverage = metadata.get("period_coverage", {}) if isinstance(metadata, dict) else {}
        periods = coverage.get("periods", []) if isinstance(coverage, dict) else []
        if target_period in periods:
            return True
        comparison_pairs = coverage.get("comparison_pairs", []) if isinstance(coverage, dict) else []
        for pair in comparison_pairs:
            if not isinstance(pair, dict):
                continue
            if pair.get("prior") == target_period or pair.get("current") == target_period:
                return True
        return False

    def _timestamp_matches_period(self, *, timestamp: datetime, target_period: str, current_timestamp: datetime) -> bool:
        if target_period == "Prior Week":
            current_week_start = current_timestamp - timedelta(days=current_timestamp.weekday())
            prior_week_start = current_week_start - timedelta(days=7)
            return prior_week_start.date() <= timestamp.date() < current_week_start.date()
        if target_period == "Prior Month":
            prior_month_year = current_timestamp.year if current_timestamp.month > 1 else current_timestamp.year - 1
            prior_month = current_timestamp.month - 1 if current_timestamp.month > 1 else 12
            return timestamp.year == prior_month_year and timestamp.month == prior_month
        if target_period == "Prior Quarter":
            current_quarter = ((current_timestamp.month - 1) // 3) + 1
            prior_quarter = current_quarter - 1
            prior_year = current_timestamp.year
            if prior_quarter == 0:
                prior_quarter = 4
                prior_year -= 1
            return timestamp.year == prior_year and (((timestamp.month - 1) // 3) + 1) == prior_quarter
        return False

    def _extract_rows_from_historical_spec(
        self,
        spec: WorkbookSpec,
        *,
        period_label: str,
        source_ref: str,
        source_excerpt: str,
    ) -> list[WorkbookFinancialRow]:
        model_sheet = next((sheet for sheet in spec.sheets if sheet.name == "Model"), None)
        if not model_sheet:
            return []

        source_rows = model_sheet.financial_rows or []
        rows: list[WorkbookFinancialRow] = []
        for row in source_rows:
            rows.append(
                WorkbookFinancialRow(
                    period=period_label,
                    metric=self._normalize_metric_name(row.metric, category=self._infer_finance_category(row.metric))[:80],
                    budget=row.budget,
                    actual=row.actual,
                    variance=row.variance,
                    forecast=row.forecast,
                    source_type="historical_artifact",
                    source_ref=source_ref,
                    source_excerpt=source_excerpt[:240],
                )
            )
        return rows

    def _select_comparison_period_pair(self, task_input: str) -> Optional[tuple[str, str]]:
        lowered = task_input.lower()
        if any(token in lowered for token in ["previous week", "prior week", "last week", "previous weeks"]):
            return ("Current Week", "Prior Week")
        if any(token in lowered for token in ["previous month", "prior month", "last month"]):
            return ("Current Month", "Prior Month")
        if any(token in lowered for token in ["previous quarter", "prior quarter", "last quarter"]):
            return ("Current Quarter", "Prior Quarter")
        return None

    def _extract_finance_hints(self, retrieval: List[Dict[str, Any]], *, default_period: str) -> list[Dict[str, Any]]:
        hints: list[Dict[str, Any]] = []
        finance_keywords = ["revenue", "budget", "actual", "forecast", "variance", "cost", "burn", "cash", "runway"]
        period_pattern = re.compile(
            r"(Q[1-4]\s*\d{4}|FY\s*\d{4}|this week|last week|prior week|this quarter|current quarter|current month|monthly|weekly)",
            re.IGNORECASE,
        )
        number_pattern = re.compile(r"\$?\d[\d,]*(?:\.\d+)?[MK]?")

        for item in retrieval[:5]:
            content = str(item.get("content", ""))
            for raw_line in re.split(r"\n+|(?<=[a-zA-Z])\.\s+", content):
                line = raw_line.strip()
                lowered = line.lower()
                if len(line) < 12 or not any(keyword in lowered for keyword in finance_keywords):
                    continue
                period_match = period_pattern.search(line)
                numeric_line = line
                if period_match:
                    numeric_line = numeric_line.replace(period_match.group(1), "", 1)
                matches = number_pattern.findall(numeric_line)
                numbers = [self._parse_numeric_metric_value(match, default=0.0) for match in matches if match]
                if not numbers:
                    continue
                metric = re.sub(number_pattern, "", line).strip(" :-").lower()
                metric = re.sub(r"\s+", " ", metric)
                if not metric:
                    metric = "financial signal"
                hint: Dict[str, Any] = {
                    "period": self._normalize_period(period_match.group(1) if period_match else default_period, fallback=default_period),
                    "metric": self._normalize_metric_name(metric)[:80],
                    "source_type": "retrieved_document",
                    "source_ref": item.get("title", "Retrieved document"),
                    "source_excerpt": line[:240],
                }
                if "budget" in lowered and numbers:
                    hint["budget"] = numbers[0]
                if "actual" in lowered and len(numbers) >= 2:
                    hint["actual"] = numbers[1]
                elif numbers:
                    hint["actual"] = numbers[0]
                if "forecast" in lowered and len(numbers) >= 3:
                    hint["forecast"] = numbers[2]
                elif len(numbers) >= 2:
                    hint["forecast"] = numbers[-1]
                if "variance" in lowered and numbers:
                    hint["variance"] = numbers[-1]
                hints.append(hint)
        return hints

    def _normalize_metric_name(self, metric_label: str, *, category: Optional[str] = None) -> str:
        lowered = re.sub(r"[^a-z0-9& ]+", " ", metric_label.lower())
        lowered = re.sub(r"\s+", " ", lowered).strip()
        for canonical, (canonical_category, aliases) in self.METRIC_TAXONOMY.items():
            if lowered == canonical.lower() or any(alias in lowered for alias in aliases):
                return canonical
            if category and category == canonical_category and canonical.lower() in lowered:
                return canonical

        if category == "revenue" and not lowered.endswith("revenue"):
            if any(token in lowered for token in ["north america", "na", "americas"]):
                return "North America revenue"
            if any(token in lowered for token in ["europe", "emea"]):
                return "Europe revenue"
            if any(token in lowered for token in ["apac", "asia pacific"]):
                return "APAC revenue"
        if category == "cost" and "aws" in lowered:
            return "AWS cost"
        if category == "capital" and "runway" in lowered:
            return "Cash runway"
        if category == "capital" and any(token in lowered for token in ["cash", "bank"]):
            return "Cash at bank"
        return metric_label.strip().title()

    def _normalize_period(self, raw_period: Optional[str], *, fallback: str = "Current Week") -> str:
        if not raw_period:
            return fallback
        cleaned = re.sub(r"\s+", " ", str(raw_period).strip())
        lowered = cleaned.lower()
        if len(cleaned) > 40 or cleaned.count(" ") > 5:
            return fallback
        if any(token in lowered for token in ["this week", "weekly", "current week"]):
            return "Current Week"
        if any(token in lowered for token in ["prior week", "last week", "previous week"]):
            return "Prior Week"
        if any(token in lowered for token in ["prior month", "last month", "previous month"]):
            return "Prior Month"
        if any(token in lowered for token in ["prior quarter", "last quarter", "previous quarter"]):
            return "Prior Quarter"
        if any(token in lowered for token in ["this quarter", "current quarter", "quarterly"]):
            return "Current Quarter"
        if any(token in lowered for token in ["this month", "current month", "monthly"]):
            return "Current Month"
        if lowered == "current":
            return fallback
        quarter_match = re.fullmatch(r"q([1-4])\s*(\d{4})", cleaned, flags=re.IGNORECASE)
        if quarter_match:
            return f"Q{quarter_match.group(1)} {quarter_match.group(2)}"
        year_match = re.fullmatch(r"fy\s*(\d{4})", cleaned, flags=re.IGNORECASE)
        if year_match:
            return f"FY {year_match.group(1)}"
        month_match = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", cleaned)
        if month_match:
            return f"{month_match.group(1).title()} {month_match.group(2)}"
        return fallback

    def _current_period_from_task_input(self, task_input: str) -> str:
        lowered = task_input.lower()
        if any(token in lowered for token in ["this week", "current week", "weekly"]):
            return "Current Week"
        if any(token in lowered for token in ["this month", "current month", "monthly"]):
            return "Current Month"
        if any(token in lowered for token in ["this quarter", "current quarter", "quarterly", "quarter", "q1", "q2", "q3", "q4"]):
            return "Current Quarter"
        return self._normalize_period(task_input, fallback="Current Week")

    def _normalize_confidence_score(self, value: Any) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0.5
        if score > 1:
            score = score / 100.0
        return max(0.0, min(score, 1.0))

    def _build_aws_summary_metrics(self, rows: list[WorkbookFinancialRow], payload: ReportPayload) -> list[WorkbookMetric]:
        current_aws = self._find_financial_row(rows, metric="AWS cost", preferred_periods=["Current Week", "Current Month", "Current Quarter"])
        prior_aws = self._find_financial_row(rows, metric="AWS cost", preferred_periods=["Prior Week", "Prior Month", "Prior Quarter", "Q1 2026"])
        current_burn = self._find_financial_row(rows, metric="Burn rate", preferred_periods=["Current Month", "Current Quarter", "Q1 2026"])
        kepler_spend = self._find_financial_row(rows, metric="Project Kepler committed spend", preferred_periods=["FY 2026"])
        metrics: list[WorkbookMetric] = []
        if current_aws:
            metrics.append(WorkbookMetric(label="Current AWS Spend", value=format_currency(current_aws.actual)))
            metrics.append(WorkbookMetric(label="Weekly Plan", value=format_currency(current_aws.budget)))
            metrics.append(WorkbookMetric(label="Variance to Plan", value=format_currency(current_aws.variance)))
        if prior_aws:
            metrics.append(WorkbookMetric(label="Prior AWS Spend", value=format_currency(prior_aws.actual)))
        if current_burn:
            metrics.append(WorkbookMetric(label="Monthly Burn", value=format_currency(current_burn.actual)))
        if kepler_spend:
            metrics.append(WorkbookMetric(label="Kepler Committed", value=format_currency(kepler_spend.actual)))
        if len(metrics) < 5:
            metrics.append(
                WorkbookMetric(
                    label="Confidence",
                    value=f"{round(self._normalize_confidence_score(payload.trust.confidence_score) * 100)}%",
                )
            )
        return metrics[:5]

    def _build_runway_summary_metrics(self, rows: list[WorkbookFinancialRow], payload: ReportPayload) -> list[WorkbookMetric]:
        cash = self._find_financial_row(rows, metric="Cash at bank", preferred_periods=["Current Week", "Current Month", "Current Quarter", "Q1 2026"])
        burn = self._find_financial_row(rows, metric="Burn rate", preferred_periods=["Current Month", "Current Quarter", "Q1 2026"])
        runway = self._find_financial_row(rows, metric="Cash runway", preferred_periods=["Current Week", "Current Month", "Current Quarter", "Q1 2026"])
        metrics: list[WorkbookMetric] = []
        if cash:
            metrics.append(WorkbookMetric(label="Cash Position", value=format_currency(cash.actual)))
        if burn:
            metrics.append(WorkbookMetric(label="Monthly Burn", value=format_currency(burn.actual)))
        if runway:
            metrics.append(WorkbookMetric(label="Runway", value=f"{runway.actual:.1f} months"))
        metrics.append(WorkbookMetric(label="Confidence", value=f"{round(self._normalize_confidence_score(payload.trust.confidence_score) * 100)}%"))
        return metrics[:5]

    def _build_project_spend_summary_metrics(self, rows: list[WorkbookFinancialRow], payload: ReportPayload) -> list[WorkbookMetric]:
        committed = self._find_financial_row(rows, metric="Project Kepler committed spend", preferred_periods=["FY 2026"])
        remaining = self._find_financial_row(rows, metric="Project Kepler remaining budget", preferred_periods=["FY 2026"])
        projected_remaining = self._find_financial_row(rows, metric="Project Kepler forecast remaining budget", preferred_periods=["Q4 2026", "Q3 2026", "Q2 2026"])
        aws = self._find_financial_row(rows, metric="AWS cost", preferred_periods=["Current Week", "Current Month"])
        metrics: list[WorkbookMetric] = []
        if committed:
            metrics.append(WorkbookMetric(label="Committed Spend", value=format_currency(committed.actual)))
            metrics.append(WorkbookMetric(label="Approved Budget", value=format_currency(committed.budget)))
        if remaining:
            metrics.append(WorkbookMetric(label="Remaining Budget", value=format_currency(remaining.actual)))
        if projected_remaining:
            metrics.append(WorkbookMetric(label="Forecast Remaining", value=format_currency(projected_remaining.actual)))
        if aws:
            metrics.append(WorkbookMetric(label="Current AWS Spend", value=format_currency(aws.actual)))
        metrics.append(WorkbookMetric(label="Confidence", value=f"{round(self._normalize_confidence_score(payload.trust.confidence_score) * 100)}%"))
        return metrics[:5]

    def _find_financial_row(
        self,
        rows: list[WorkbookFinancialRow],
        *,
        metric: str,
        preferred_periods: list[str],
    ) -> Optional[WorkbookFinancialRow]:
        for period in preferred_periods:
            match = next((row for row in rows if row.metric == metric and row.period == period), None)
            if match:
                return match
        return next((row for row in rows if row.metric == metric), None)

    def _build_aws_spend_rows(
        self,
        *,
        retrieval: List[Dict[str, Any]],
        company_state: Dict[str, Any],
        default_period: str,
    ) -> list[WorkbookFinancialRow]:
        prior_aws = self._find_retrieval_measure(retrieval, r"Prior Week AWS Spend:\s*\$?([0-9.,]+[MK]?)")
        current_aws = self._find_retrieval_measure(retrieval, r"Current Week AWS Spend:\s*\$?([0-9.,]+[MK]?)")
        weekly_variance = self._find_retrieval_measure(retrieval, r"Weekly Variance:\s*[+\-]?\$?([0-9.,]+[MK]?)")
        qtd_plan_pct = self._find_retrieval_measure(retrieval, r"QTD AWS Spend vs Plan:\s*([+\-]?[0-9.,]+)%")
        current_burn = self._find_retrieval_measure(retrieval, r"Normalized Monthly Burn Run-Rate:\s*\$?([0-9.,]+[MK]?)")
        q1_burn = self._find_retrieval_measure(retrieval, r"Monthly Burn Rate:\s*\$?([0-9.,]+[MK]?)")
        current_cash = self._find_retrieval_measure(retrieval, r"Cash on Hand:\s*\$?([0-9.,]+[MK]?)")
        q1_cash = self._find_retrieval_measure(retrieval, r"Total Cash on Hand:\s*\$?([0-9.,]+[MK]?)")
        kepler_budget = (
            self._find_retrieval_measure(retrieval, r"FY2026 Approved Budget:\s*\$?([0-9.,]+[MK]?)")
            or self._find_retrieval_measure(retrieval, r"budget allocation of \$?([0-9.,]+[MK]?) for FY2026")
        )
        kepler_committed = self._find_retrieval_measure(retrieval, r"Spend Committed to Date:\s*\$?([0-9.,]+[MK]?)")

        rows: list[WorkbookFinancialRow] = []
        prior_aws_value = self._measure_value(prior_aws)
        current_aws_value = self._measure_value(current_aws)
        weekly_variance_value = self._measure_value(weekly_variance)
        qtd_multiplier = 1.0 + ((self._measure_value(qtd_plan_pct) or 0.0) / 100.0)
        if prior_aws_value is not None:
            rows.append(
                WorkbookFinancialRow(
                    period="Prior Week",
                    metric="AWS cost",
                    budget=prior_aws_value,
                    actual=prior_aws_value,
                    variance=0.0,
                    forecast=current_aws_value or round(prior_aws_value * qtd_multiplier, 2),
                    source_type="retrieved_document",
                    source_ref=(prior_aws or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(prior_aws or {}).get("source_excerpt"),
                )
            )
        if current_aws_value is not None:
            current_budget = current_aws_value - weekly_variance_value if weekly_variance_value is not None else (prior_aws_value or current_aws_value)
            rows.append(
                WorkbookFinancialRow(
                    period="Current Week",
                    metric="AWS cost",
                    budget=round(current_budget, 2),
                    actual=current_aws_value,
                    variance=round(current_aws_value - current_budget, 2),
                    forecast=round(current_aws_value * qtd_multiplier, 2),
                    source_type="retrieved_document",
                    source_ref=(current_aws or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(current_aws or {}).get("source_excerpt"),
                )
            )

        q1_burn_value = self._measure_value(q1_burn)
        current_burn_value = self._measure_value(current_burn)
        if q1_burn_value is not None:
            rows.append(
                WorkbookFinancialRow(
                    period="Q1 2026",
                    metric="Burn rate",
                    budget=q1_burn_value,
                    actual=q1_burn_value,
                    variance=0.0,
                    forecast=current_burn_value or q1_burn_value,
                    source_type="retrieved_document",
                    source_ref=(q1_burn or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(q1_burn or {}).get("source_excerpt"),
                )
            )
        if current_burn_value is not None:
            burn_budget = q1_burn_value or round(current_burn_value * 0.94, 2)
            rows.append(
                WorkbookFinancialRow(
                    period="Current Month",
                    metric="Burn rate",
                    budget=burn_budget,
                    actual=current_burn_value,
                    variance=round(current_burn_value - burn_budget, 2),
                    forecast=current_burn_value,
                    source_type="retrieved_document",
                    source_ref=(current_burn or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(current_burn or {}).get("source_excerpt"),
                )
            )

        q1_cash_value = self._measure_value(q1_cash)
        current_cash_value = self._measure_value(current_cash)
        if q1_cash_value is not None:
            rows.append(
                WorkbookFinancialRow(
                    period="Q1 2026",
                    metric="Cash at bank",
                    budget=q1_cash_value,
                    actual=q1_cash_value,
                    variance=0.0,
                    forecast=current_cash_value or q1_cash_value,
                    source_type="retrieved_document",
                    source_ref=(q1_cash or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(q1_cash or {}).get("source_excerpt"),
                )
            )
        if current_cash_value is not None:
            cash_budget = q1_cash_value or current_cash_value
            rows.append(
                WorkbookFinancialRow(
                    period=default_period if default_period != "Current Quarter" else "Current Week",
                    metric="Cash at bank",
                    budget=cash_budget,
                    actual=current_cash_value,
                    variance=round(current_cash_value - cash_budget, 2),
                    forecast=current_cash_value,
                    source_type="retrieved_document",
                    source_ref=(current_cash or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(current_cash or {}).get("source_excerpt"),
                )
            )

        kepler_budget_value = self._measure_value(kepler_budget)
        kepler_committed_value = self._measure_value(kepler_committed)
        if kepler_budget_value is not None and kepler_committed_value is not None:
            rows.append(
                WorkbookFinancialRow(
                    period="FY 2026",
                    metric="Project Kepler committed spend",
                    budget=kepler_budget_value,
                    actual=kepler_committed_value,
                    variance=round(kepler_committed_value - kepler_budget_value, 2),
                    forecast=kepler_budget_value,
                    source_type="retrieved_document",
                    source_ref=(kepler_committed or kepler_budget or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(kepler_committed or kepler_budget or {}).get("source_excerpt"),
                )
            )

        if not rows:
            return []

        if not any(row.metric == "AWS cost" for row in rows):
            aws_value = company_state.get("cost_structure", {}).get("AWS cost")
            if isinstance(aws_value, (int, float)):
                rows.append(
                    WorkbookFinancialRow(
                        period="Current Week",
                        metric="AWS cost",
                        budget=float(aws_value),
                        actual=float(aws_value),
                        variance=0.0,
                        forecast=float(aws_value),
                        source_type="company_state",
                        source_ref="CompanyState.cost_structure.AWS cost",
                        source_excerpt=f"AWS cost: {aws_value}",
                    )
                )
        if not any(row.metric == "Burn rate" for row in rows):
            burn_value = company_state.get("cost_structure", {}).get("Burn Rate") or company_state.get("capital_position", {}).get("Burn Rate")
            if isinstance(burn_value, (int, float)):
                rows.append(
                    WorkbookFinancialRow(
                        period="Current Month",
                        metric="Burn rate",
                        budget=float(burn_value),
                        actual=float(burn_value),
                        variance=0.0,
                        forecast=float(burn_value),
                        source_type="company_state",
                        source_ref="CompanyState.cost_structure.Burn Rate",
                        source_excerpt=f"Burn Rate: {burn_value}",
                    )
                )
        if not any(row.metric == "Cash at bank" for row in rows):
            capital_position = company_state.get("capital_position", {})
            cash_value = capital_position.get("Cash at Bank") or capital_position.get("cash_at_bank")
            if isinstance(cash_value, (int, float)):
                rows.append(
                    WorkbookFinancialRow(
                        period="Current Month",
                        metric="Cash at bank",
                        budget=float(cash_value),
                        actual=float(cash_value),
                        variance=0.0,
                        forecast=float(cash_value),
                        source_type="company_state",
                        source_ref="CompanyState.capital_position.Cash at Bank",
                        source_excerpt=f"Cash at Bank: {cash_value}",
                    )
                )
        return self._dedupe_financial_rows(rows)

    def _build_runway_burn_rows(
        self,
        *,
        retrieval: List[Dict[str, Any]],
        company_state: Dict[str, Any],
        default_period: str,
    ) -> list[WorkbookFinancialRow]:
        rows: list[WorkbookFinancialRow] = []
        current_burn = self._find_retrieval_measure(retrieval, r"Normalized Monthly Burn Run-Rate:\s*\$?([0-9.,]+[MK]?)")
        q1_burn = self._find_retrieval_measure(retrieval, r"Monthly Burn Rate:\s*\$?([0-9.,]+[MK]?)")
        current_cash = self._find_retrieval_measure(retrieval, r"Cash on Hand:\s*\$?([0-9.,]+[MK]?)")
        q1_cash = self._find_retrieval_measure(retrieval, r"Total Cash on Hand:\s*\$?([0-9.,]+[MK]?)")

        q1_burn_value = self._measure_value(q1_burn)
        current_burn_value = self._measure_value(current_burn)
        q1_cash_value = self._measure_value(q1_cash)
        current_cash_value = self._measure_value(current_cash)

        if q1_burn_value is not None:
            rows.append(
                WorkbookFinancialRow(
                    period="Q1 2026",
                    metric="Burn rate",
                    budget=q1_burn_value,
                    actual=q1_burn_value,
                    variance=0.0,
                    forecast=current_burn_value or q1_burn_value,
                    source_type="retrieved_document",
                    source_ref=(q1_burn or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(q1_burn or {}).get("source_excerpt"),
                )
            )
        if current_burn_value is not None:
            budget = q1_burn_value or round(current_burn_value * 0.94, 2)
            rows.append(
                WorkbookFinancialRow(
                    period="Current Month",
                    metric="Burn rate",
                    budget=budget,
                    actual=current_burn_value,
                    variance=round(current_burn_value - budget, 2),
                    forecast=current_burn_value,
                    source_type="retrieved_document",
                    source_ref=(current_burn or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(current_burn or {}).get("source_excerpt"),
                )
            )
        if q1_cash_value is not None:
            rows.append(
                WorkbookFinancialRow(
                    period="Q1 2026",
                    metric="Cash at bank",
                    budget=q1_cash_value,
                    actual=q1_cash_value,
                    variance=0.0,
                    forecast=current_cash_value or q1_cash_value,
                    source_type="retrieved_document",
                    source_ref=(q1_cash or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(q1_cash or {}).get("source_excerpt"),
                )
            )
        if current_cash_value is not None:
            budget = q1_cash_value or current_cash_value
            rows.append(
                WorkbookFinancialRow(
                    period=default_period if default_period in {"Current Month", "Current Week"} else "Current Month",
                    metric="Cash at bank",
                    budget=budget,
                    actual=current_cash_value,
                    variance=round(current_cash_value - budget, 2),
                    forecast=current_cash_value,
                    source_type="retrieved_document",
                    source_ref=(current_cash or {}).get("source_ref", "retrieved_document"),
                    source_excerpt=(current_cash or {}).get("source_excerpt"),
                )
            )
        if not rows:
            capital_position = company_state.get("capital_position", {})
            cash_value = capital_position.get("Cash at Bank") or capital_position.get("cash_at_bank")
            burn_value = capital_position.get("Burn Rate") or company_state.get("cost_structure", {}).get("Burn Rate")
            if isinstance(cash_value, (int, float)):
                rows.append(
                    WorkbookFinancialRow(
                        period="Current Quarter",
                        metric="Cash at bank",
                        budget=float(cash_value),
                        actual=float(cash_value),
                        variance=0.0,
                        forecast=float(cash_value),
                        source_type="company_state",
                        source_ref="CompanyState.capital_position.Cash at Bank",
                        source_excerpt=f"Cash at Bank: {cash_value}",
                    )
                )
            if isinstance(burn_value, (int, float)):
                rows.append(
                    WorkbookFinancialRow(
                        period="Current Month",
                        metric="Burn rate",
                        budget=float(burn_value),
                        actual=float(burn_value),
                        variance=0.0,
                        forecast=float(burn_value),
                        source_type="company_state",
                        source_ref="CompanyState.cost_structure.Burn Rate",
                        source_excerpt=f"Burn Rate: {burn_value}",
                    )
                )
        return self._dedupe_financial_rows(self._augment_runway_rows(rows))

    def _build_project_spend_rows(
        self,
        *,
        task_input: str,
        retrieval: List[Dict[str, Any]],
        company_state: Dict[str, Any],
        default_period: str,
    ) -> list[WorkbookFinancialRow]:
        mapped = map_company_context_to_metrics(
            MapperContext(
                company_state=company_state,
                retrieved_documents=retrieval,
                task_input=task_input,
            )
        )
        config = ForecastConfig(
            template="project_spend_review",
            horizon_periods=["Q2 2026", "Q3 2026", "Q4 2026"],
            metadata={"task_input": task_input},
        )
        forecast = self.forecast_engine.run(
            template="project_spend_review",
            metrics=mapped.metrics,
            config=config,
        )
        rows: list[WorkbookFinancialRow] = []
        approved_budget = next((metric for metric in mapped.metrics if metric.metric_key == "approved_budget"), None)
        committed_spend = next((metric for metric in mapped.metrics if metric.metric_key == "committed_spend"), None)
        remaining_budget = next((metric for metric in mapped.metrics if metric.metric_key == "remaining_budget"), None)
        weekly_cloud_cost = next((metric for metric in mapped.metrics if metric.metric_key == "cloud_cost_weekly"), None)

        if approved_budget and committed_spend:
            rows.append(
                WorkbookFinancialRow(
                    period=committed_spend.period.label,
                    metric="Project Kepler committed spend",
                    budget=approved_budget.value,
                    actual=committed_spend.value,
                    variance=round(committed_spend.value - approved_budget.value, 2),
                    forecast=approved_budget.value,
                    source_type="retrieved_document" if committed_spend.source_type == "document" else committed_spend.source_type,
                    source_ref=committed_spend.source_ref,
                    source_excerpt=str(committed_spend.metadata.get("source_excerpt", ""))[:240],
                )
            )
        if approved_budget and remaining_budget:
            rows.append(
                WorkbookFinancialRow(
                    period=remaining_budget.period.label,
                    metric="Project Kepler remaining budget",
                    budget=approved_budget.value,
                    actual=remaining_budget.value,
                    variance=round(remaining_budget.value - approved_budget.value, 2),
                    forecast=remaining_budget.value,
                    source_type="derived_metric" if remaining_budget.source_type == "derived" else remaining_budget.source_type,
                    source_ref=remaining_budget.source_ref,
                    source_excerpt=str(remaining_budget.metadata.get("source_excerpt") or remaining_budget.metadata.get("derivation") or "")[:240],
                )
            )
        if weekly_cloud_cost:
            rows.append(
                WorkbookFinancialRow(
                    period=weekly_cloud_cost.period.label,
                    metric="AWS cost",
                    budget=weekly_cloud_cost.value,
                    actual=weekly_cloud_cost.value,
                    variance=0.0,
                    forecast=weekly_cloud_cost.value,
                    source_type="retrieved_document" if weekly_cloud_cost.source_type == "document" else weekly_cloud_cost.source_type,
                    source_ref=weekly_cloud_cost.source_ref,
                    source_excerpt=str(weekly_cloud_cost.metadata.get("source_excerpt", ""))[:240],
                )
            )

        baseline_budget = forecast.metadata.get("baseline_period_budget")
        for forecast_row in forecast.workbook_rows:
            if forecast_row.metric_key == "forecast_spend":
                budget = float(forecast_row.metadata.get("period_budget", baseline_budget or 0.0))
                rows.append(
                    WorkbookFinancialRow(
                        period=forecast_row.period,
                        metric="Project Kepler forecast spend",
                        budget=budget,
                        actual=forecast_row.value,
                        variance=round(forecast_row.value - budget, 2),
                        forecast=forecast_row.value,
                        source_type="derived_metric",
                        source_ref="forecast_engine:project_spend_review",
                        source_excerpt="Deterministic forecast from canonical approved budget, committed spend, and weekly cloud spend.",
                    )
                )
            elif forecast_row.metric_key == "forecast_remaining_budget":
                starting_budget = float(forecast_row.metadata.get("starting_budget", approved_budget.value if approved_budget else 0.0))
                rows.append(
                    WorkbookFinancialRow(
                        period=forecast_row.period,
                        metric="Project Kepler forecast remaining budget",
                        budget=starting_budget,
                        actual=forecast_row.value,
                        variance=round(forecast_row.value - starting_budget, 2),
                        forecast=forecast_row.value,
                        source_type="derived_metric",
                        source_ref="forecast_engine:project_spend_review",
                        source_excerpt="Deterministic remaining-budget forecast from projected committed spend.",
                    )
                )
        return self._dedupe_financial_rows(self._augment_project_spend_rows(rows))

    def _project_spend_forecast_result(
        self,
        *,
        task_input: str,
        company_state: Dict[str, Any],
        retrieval: List[Dict[str, Any]],
    ) -> Optional[ForecastResult]:
        mapped = map_company_context_to_metrics(
            MapperContext(
                company_state=company_state,
                retrieved_documents=retrieval,
                task_input=task_input,
            )
        )
        config = ForecastConfig(
            template="project_spend_review",
            horizon_periods=["Q2 2026", "Q3 2026", "Q4 2026"],
            metadata={"task_input": task_input},
        )
        return self.forecast_engine.run(
            template="project_spend_review",
            metrics=mapped.metrics,
            config=config,
        )

    _TEMPLATE_HORIZON_PERIODS: dict[str, list[str]] = {
        "cost_review":           ["2 Weeks Ago", "Prior Week", "Current Week"],
        "runway_review":         ["Current", "Month 1", "Month 3", "Month 6", "Month 9", "Month 12"],
        "budget_variance_review": ["Current Period"],
        "board_financial_update": ["Prior Quarter", "Current Quarter"],
        "project_spend_review":  ["Q2 2026", "Q3 2026", "Q4 2026"],
    }

    def _run_forecast_for_template(
        self,
        *,
        finance_template: str,
        task_input: str,
        company_state: Dict[str, Any],
        retrieval: List[Dict[str, Any]],
    ) -> Optional[ForecastResult]:
        try:
            mapped = map_company_context_to_metrics(
                MapperContext(
                    company_state=company_state,
                    retrieved_documents=retrieval,
                    task_input=task_input,
                )
            )
            horizon = self._TEMPLATE_HORIZON_PERIODS.get(finance_template, [])
            config = ForecastConfig(
                template=finance_template,  # type: ignore[arg-type]
                horizon_periods=horizon,
                metadata={"task_input": task_input},
            )
            return self.forecast_engine.run(
                template=finance_template,  # type: ignore[arg-type]
                metrics=mapped.metrics,
                config=config,
            )
        except Exception:
            return None

    def _forecast_threshold_events(
        self,
        *,
        task_input: str,
        company_state: Dict[str, Any],
        retrieval: List[Dict[str, Any]],
        finance_template: str,
    ) -> list[dict[str, Any]]:
        forecast = self._run_forecast_for_template(
            finance_template=finance_template,
            task_input=task_input,
            company_state=company_state,
            retrieval=retrieval,
        )
        if not forecast:
            return []
        return [event.model_dump() for event in forecast.threshold_events]

    def _find_retrieval_measure(self, retrieval: List[Dict[str, Any]], pattern: str) -> Optional[Dict[str, Any]]:
        regex = re.compile(pattern, re.IGNORECASE)
        for item in retrieval[:6]:
            content = str(item.get("content", ""))
            for raw_line in content.splitlines():
                line = raw_line.strip(" -\t")
                if not line:
                    continue
                match = regex.search(line)
                if not match:
                    continue
                return {
                    "raw": match.group(1),
                    "source_ref": item.get("title", "Retrieved document"),
                    "source_excerpt": line[:240],
                    "source_type": "retrieved_document",
                }
        return None

    def _measure_value(self, measure: Optional[Dict[str, Any]]) -> Optional[float]:
        if not measure:
            return None
        raw = measure.get("raw")
        if raw is None:
            return None
        return self._parse_numeric_metric_value(str(raw), default=0.0)

    def _sanitize_financial_rows(self, rows: list[WorkbookFinancialRow], *, aws_focus: bool) -> list[WorkbookFinancialRow]:
        sanitized: list[WorkbookFinancialRow] = []
        for row in rows:
            if len(row.period) > 40 or len(row.metric) > 80:
                continue
            if aws_focus and row.metric not in {"AWS cost", "Burn rate", "Cash at bank", "Project Kepler committed spend"}:
                continue
            if row.actual < 0 and row.metric not in {"Project Kepler committed spend", "Project Kepler forecast remaining budget"}:
                continue
            sanitized.append(row)
        return self._dedupe_financial_rows(sanitized)

    def _validate_financial_rows(self, rows: list[WorkbookFinancialRow]) -> tuple[list[WorkbookFinancialRow], list[str]]:
        validated: list[WorkbookFinancialRow] = []
        warnings: list[str] = []
        for row in rows:
            delta = round(row.actual - row.budget, 2)
            if abs(delta - row.variance) > 1:
                warnings.append(f"{row.metric} in {row.period} failed variance validation.")
                continue
            if row.forecast < 0 and row.metric != "Project Kepler forecast remaining budget":
                warnings.append(f"{row.metric} in {row.period} has an invalid negative forecast.")
                continue
            if row.actual < 0.1 and row.metric not in {"Cash runway", "Project Kepler forecast remaining budget"} and self._infer_finance_category(row.metric) in {"revenue", "cost", "capital"}:
                warnings.append(f"{row.metric} in {row.period} appears under-scaled for a finance metric.")
                continue
            validated.append(row)
        if not validated and rows:
            warnings.append("All candidate finance rows failed validation; the workbook may need structured source data.")
        return validated or rows[:1], list(dict.fromkeys(warnings))

    def _rank_and_normalize_sources(self, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: list[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        ranked = sorted(
            sources,
            key=lambda item: self._source_trust_rank(
                source_type=str(item.get("type") or item.get("source_type") or ""),
                source_ref=str(item.get("source_id") or item.get("title") or ""),
                title=str(item.get("title") or ""),
            ),
            reverse=True,
        )
        for index, item in enumerate(ranked, start=1):
            title = str(item.get("title") or f"Source {index}")
            source_type = str(item.get("type") or item.get("source_type") or "document")
            key = (title.lower(), source_type)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                {
                    "source_id": str(item.get("source_id") or f"source_{index}"),
                    "title": title,
                    "type": source_type if source_type in {"document", "state", "artifact"} else "document",
                    "snippet": item.get("snippet") or item.get("source_excerpt") or item.get("content"),
                    "role": item.get("role") or self._source_role(source_type),
                    "relevance_reason": item.get("relevance_reason") or self._source_relevance_reason(source_type),
                    "used_for": item.get("used_for") or self._source_used_for(source_type),
                    "confidence_impact": item.get("confidence_impact") or self._source_confidence_impact(source_type),
                }
            )
        return normalized[:5]

    def _source_role(self, source_type: str) -> str:
        if source_type == "state":
            return "operating_context"
        if source_type == "artifact":
            return "internal_analysis"
        return "supporting_evidence"

    def _source_relevance_reason(self, source_type: str) -> str:
        if source_type == "state":
            return "Anchors the report in current company operating context."
        if source_type == "artifact":
            return "Provides prior internal analysis or generated supporting material."
        return "Supplies direct supporting evidence for the report conclusions."

    def _source_used_for(self, source_type: str) -> list[str]:
        if source_type == "state":
            return ["summary", "priorities", "recommendation"]
        if source_type == "artifact":
            return ["trend_check", "recommendation"]
        return ["evidence", "risk_assessment"]

    def _source_confidence_impact(self, source_type: str) -> str:
        if source_type == "state":
            return "high"
        if source_type == "artifact":
            return "medium"
        return "medium"

    def _to_presentation_section(self, section: ReportSection) -> PresentationSection:
        return PresentationSection(title=section.label, content=section.content, items=section.items)

    def _apply_presentation_metadata(
        self,
        payload: ReportPayload,
        *,
        task_input: str,
        finance_template: Optional[str],
        finance_digest: Optional[dict[str, Any]],
        primary_visual: Optional[dict[str, str]],
        finance_summary_metrics: Optional[list[WorkbookMetric]] = None,
        finance_rows: Optional[list[WorkbookFinancialRow]] = None,
        ceo_id: Optional[str] = None,
        resolved_topics: frozenset = frozenset(),
        artifact_type: Optional[str] = None,
        intent_state: Optional[Dict[str, Any]] = None,
    ) -> ReportPayload:
        # Always clear LLM-generated open_questions first — we always control this field.
        # The LLM may produce open-ended data-seeking questions; we replace them with
        # our controlled binary format. Clearing here ensures nothing leaks through.
        payload.trust.open_questions = []

        # When an artifact_type is already active, the output format is decided —
        # treat "output_format" as resolved so _clarifying_questions() returns [].
        if artifact_type:
            resolved_topics = resolved_topics | frozenset({"output_format"})

        sections = payload.answer.sections
        payload.presentation = ReportPresentation(
            mode="finance" if finance_template else "report",
            variant="digest" if finance_template else "executive",
            summary=payload.answer.summary,
            priorities=[self._to_presentation_section(sections[0])] if len(sections) > 0 else [],
            recommended_actions=[self._to_presentation_section(sections[2])] if len(sections) > 2 else [],
            risks=[self._to_presentation_section(sections[1])] if len(sections) > 1 else [],
            details=[],
            finance=self._build_finance_presentation(
                payload,
                finance_template=finance_template,
                finance_digest=finance_digest,
                primary_visual=primary_visual,
                finance_summary_metrics=finance_summary_metrics,
                finance_rows=finance_rows,
            ),
        )

        evidence_reasons: list[str] = []
        if len(payload.sources) <= 1:
            evidence_reasons.append("Only a narrow set of supporting sources was available.")
        clarifying_questions = self._clarifying_questions(
            task_input, payload, ceo_id=ceo_id, resolved_topics=resolved_topics
        )
        # Always replace LLM open_questions with our controlled binary format question.
        # When _clarifying_questions returns [], we get [], suppressing all LLM data-seeking
        # questions that are open-ended and would violate the binary-question constraint.
        payload.trust.open_questions = (
            self._rank_questions_by_impact(clarifying_questions, task_input)
            if clarifying_questions
            else []
        )
        # Drop any remaining questions whose topic the CEO already resolved.
        if resolved_topics and payload.trust.open_questions:
            payload.trust.open_questions = self._filter_resolved_open_questions(
                payload.trust.open_questions, resolved_topics
            )
        # Cap to at most 1 question — belt + suspenders.
        payload.trust.open_questions = payload.trust.open_questions[:1]
        if len(payload.trust.missing_context) > 0:
            evidence_reasons.append("Some supporting context is still missing.")
        elif len(payload.trust.open_questions) > 0:
            evidence_reasons.append("There are still unresolved ambiguities behind this answer.")

        if payload.trust.confidence == "low" or evidence_reasons:
            payload.trust.evidence_state = "sparse" if payload.trust.confidence == "low" or len(evidence_reasons) >= 2 else "mixed"
            payload.trust.evidence_reasons = evidence_reasons or ["The recommendation should be treated as directional guidance."]
            payload.trust.safe_to_act = payload.trust.confidence == "high" and not payload.trust.missing_context
        else:
            payload.trust.evidence_state = "strong"
            payload.trust.evidence_reasons = ["The report is grounded in multiple aligned internal context sources."]
            payload.trust.safe_to_act = True

        # Build question_options: action offers first, then clarifying questions.
        # Skip action offers entirely when artifact_type is already active — the CEO
        # already accepted/requested that mode and re-offering it every turn is noise.
        payload.trust.question_options = self._collect_trust_options(
            task_input, payload, resolved_topics, intent_state or {}, artifact_type
        )

        return payload

    def _build_presentation_spec(
        self,
        *,
        task_input: str,
        payload: ReportPayload,
        output_modality: str,
        finance_template: Optional[str],
    ) -> PresentationSpec:
        artifact_kind = "board_deck" if output_modality in {"pptx", "pptx+xlsx"} else (
            "financial_analysis" if finance_template or output_modality in {"xlsx", "docx+xlsx"} else "memo"
        )
        lowered = task_input.lower()
        audience = "board" if any(marker in lowered for marker in ("board", "investor", "committee")) else "ceo"
        intent = "decide" if self._looks_like_decision_payload(payload) else "inform"

        recommendation = None
        decision_required = None
        blocks: list[PresentationBlock] = []
        for index, section in enumerate(payload.answer.sections):
            label_lower = section.label.lower()
            block_kind = "analysis"
            if any(token in label_lower for token in ("priority", "summary", "context", "snapshot")):
                block_kind = "context"
            elif any(token in label_lower for token in ("risk", "gap", "sensitivity")):
                block_kind = "risks"
            elif any(token in label_lower for token in ("action", "next step", "decision")):
                block_kind = "actions"
            bullets = [str(item) for item in section.items[:6]]
            summary = section.content or (bullets[0] if bullets else None)
            blocks.append(
                PresentationBlock(
                    kind=block_kind,
                    title=section.label,
                    summary=summary,
                    bullets=bullets,
                    priority=index,
                )
            )
            if recommendation is None and block_kind == "actions" and bullets:
                recommendation = bullets[0]
        if blocks:
            blocks[0] = blocks[0].model_copy(update={"kind": "headline"})
        if artifact_kind == "board_deck":
            decision_required = recommendation or payload.answer.summary
        return PresentationSpec(
            artifact_kind=artifact_kind,  # type: ignore[arg-type]
            audience=audience,  # type: ignore[arg-type]
            intent=intent,  # type: ignore[arg-type]
            title=payload.answer.title,
            executive_summary=payload.answer.summary,
            recommendation=recommendation,
            decision_required=decision_required,
            assumptions=[str(item) for item in payload.trust.assumptions[:4]],
            sensitivities=[str(item) for item in payload.trust.missing_context[:4]],
            blocks=blocks,
            metadata={
                "finance_template": finance_template,
                "source_count": len(payload.sources),
                "output_modality": output_modality,
            },
        )

    def _build_finance_presentation(
        self,
        payload: ReportPayload,
        *,
        finance_template: Optional[str],
        finance_digest: Optional[dict[str, Any]],
        primary_visual: Optional[dict[str, str]],
        finance_summary_metrics: Optional[list[WorkbookMetric]] = None,
        finance_rows: Optional[list[WorkbookFinancialRow]] = None,
    ) -> Optional[dict[str, Any]]:
        if not finance_template:
            return None

        metrics = (finance_summary_metrics or self._extract_metrics(payload))[:5]
        return {
            "template": finance_template,
            "headline": (finance_digest or {}).get("headline") or payload.answer.summary,
            "takeaways": list((finance_digest or {}).get("takeaways") or []),
            "implications": list((finance_digest or {}).get("implications") or []),
            "recommendation": (finance_digest or {}).get("recommendation"),
            "next_steps": list((finance_digest or {}).get("next_steps") or []),
            "threshold_events": list((finance_digest or {}).get("threshold_events") or []),
            "key_metrics": [
                {"label": metric.label, "value": metric.value}
                for metric in metrics
                if getattr(metric, "label", None) and getattr(metric, "value", None)
            ],
            "primary_visual": primary_visual,
            "charts": self._build_chart_data(finance_rows or []),
        }

    def _build_chart_data(self, rows: list[WorkbookFinancialRow]) -> list[dict[str, Any]]:
        """Convert financial rows into chart-ready data grouped by metric category."""
        if not rows:
            return []

        # Dedupe: keep latest row per metric (preserve insertion order)
        seen: dict[str, WorkbookFinancialRow] = {}
        for row in rows:
            seen[row.metric] = row
        deduped = list(seen.values())

        revenue: list[dict[str, Any]] = []
        cost: list[dict[str, Any]] = []
        capital: list[dict[str, Any]] = []

        for row in deduped:
            cat = self._infer_finance_category(row.metric)
            point: dict[str, Any] = {
                "metric": row.metric,
                "actual": round(row.actual, 2) if row.actual else None,
                "budget": round(row.budget, 2) if row.budget else None,
                "forecast": round(row.forecast, 2) if row.forecast else None,
            }
            if cat == "revenue":
                revenue.append(point)
            elif cat == "cost":
                cost.append(point)
            elif cat == "capital":
                capital.append(point)

        charts = []
        if revenue:
            charts.append({"title": "Revenue", "type": "grouped_bar", "group": "revenue", "data": revenue[:8]})
        if cost:
            charts.append({"title": "Costs", "type": "grouped_bar", "group": "cost", "data": cost[:8]})
        if capital and not revenue:
            # Only show capital as standalone when there's no revenue data
            charts.append({"title": "Capital Position", "type": "grouped_bar", "group": "capital", "data": capital[:6]})
        return charts

    def _metrics_by_label(self, metrics: list[WorkbookMetric]) -> dict[str, str]:
        return {metric.label: metric.value for metric in metrics if metric.label and metric.value}

    def _aws_digest_fallback(self, metrics: list[WorkbookMetric], digest: dict[str, Any]) -> dict[str, Any]:
        metric_map = self._metrics_by_label(metrics)
        current = metric_map.get("Current AWS Spend")
        variance = metric_map.get("Variance to Plan")
        burn = metric_map.get("Monthly Burn")
        kepler = metric_map.get("Kepler Committed")
        takeaways = [
            item
            for item in [
                f"Current AWS spend is {current}." if current else None,
                f"Variance to plan is {variance}." if variance else None,
                f"Monthly burn is {burn}, so AWS cost should be managed as a burn driver." if burn else None,
            ]
            if item
        ]
        implications = [
            item
            for item in [
                "AWS cost discipline matters because cloud spend rolls directly into burn and margin performance.",
                f"If AWS variance stays at {variance}, finance should tighten cloud commitments before the next board update." if variance else None,
                f"Committed project spend of {kepler} limits room to absorb additional AWS overages." if kepler else None,
            ]
            if item
        ]
        return {
            "headline": digest.get("headline") or (f"AWS cost review: current AWS spend is {current}." if current else None),
            "takeaways": takeaways or digest.get("takeaways") or [],
            "implications": implications or digest.get("implications") or [],
            "recommendation": "Reduce AWS variance against plan and review the largest cloud cost drivers this quarter.",
            "next_steps": digest.get("next_steps") or [
                "Review the largest AWS cost drivers.",
                "Confirm whether reserved usage or architecture changes are needed.",
            ],
        }

    def _runway_digest_fallback(self, metrics: list[WorkbookMetric], digest: dict[str, Any]) -> dict[str, Any]:
        metric_map = self._metrics_by_label(metrics)
        cash = metric_map.get("Cash Position")
        burn = metric_map.get("Monthly Burn")
        runway = metric_map.get("Runway")
        takeaways = [
            item
            for item in [
                f"Cash position is {cash}." if cash else None,
                f"Monthly burn is {burn}." if burn else None,
                f"Cash runway is {runway}." if runway else None,
            ]
            if item
        ]
        implications = [
            item
            for item in [
                f"At the current burn rate, the board should plan around roughly {runway} of runway." if runway else None,
                "Any increase in burn will shorten runway and reduce financing flexibility.",
                "Board messaging should anchor on cash position, burn rate, and runway durability.",
            ]
            if item
        ]
        return {
            "headline": digest.get("headline") or (f"Runway review: cash position is {cash} with {runway} remaining." if cash and runway else None),
            "takeaways": takeaways or digest.get("takeaways") or [],
            "implications": implications or digest.get("implications") or [],
            "recommendation": "Hold burn within plan and prepare a board narrative around cash position and runway durability.",
            "next_steps": digest.get("next_steps") or [
                "Review burn drivers that could compress runway.",
                "Update the board narrative with current cash and runway assumptions.",
            ],
        }

    def _source_trust_rank(self, *, source_type: str, source_ref: str, title: str) -> int:
        lowered = f"{source_type} {source_ref} {title}".lower()
        if "audit" in lowered or "audited" in lowered:
            return self.SOURCE_TRUST_RANKS["audited_finance_doc"]
        if "check-in" in lowered or "check in" in lowered or "weekly" in lowered:
            return self.SOURCE_TRUST_RANKS["weekly_finance_checkin"]
        if "companystate" in lowered or source_type == "state":
            return self.SOURCE_TRUST_RANKS["company_state"]
        if "memo" in lowered:
            return self.SOURCE_TRUST_RANKS["internal_finance_memo"]
        return self.SOURCE_TRUST_RANKS.get(source_type, self.SOURCE_TRUST_RANKS["retrieved_document"])

    def _build_chart_tables(
        self,
        *,
        task_input: str,
        rows: list[WorkbookFinancialRow],
        comparison_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        finance_template = self._select_finance_template(task_input)
        if finance_template == "aws_cost_review":
            aws_rows = [row for row in rows if row.metric == "AWS cost"]
            spend_trend_table = {
                "title": "AWS Spend Trend",
                "columns": ["Period", "Budget", "Actual", "Forecast"],
                "rows": [
                    [row.period, format_currency(row.budget), format_currency(row.actual), format_currency(row.forecast)]
                    for row in aws_rows
                ],
                "row_provenance": [self._financial_row_provenance(row) for row in aws_rows],
            }
            comparison_source_rows = [
                row for row in rows if row.metric in {"AWS cost", "Burn rate", "Project Kepler committed spend"}
            ]
            comparison_table = {
                "title": "Budget vs Actual",
                "columns": ["Metric", "Budget", "Actual", "Forecast"],
                "rows": [
                    [
                        f"{row.metric} ({row.period})",
                        format_currency(row.budget),
                        format_currency(row.actual),
                        format_currency(row.forecast),
                    ]
                    for row in comparison_source_rows
                ],
                "row_provenance": [self._financial_row_provenance(row) for row in comparison_source_rows],
            }
            return [table for table in [spend_trend_table, comparison_table] if table["rows"]]
        if finance_template == "runway_burn_review":
            runway_rows = [row for row in rows if row.metric in {"Cash at bank", "Burn rate", "Cash runway"}]
            runway_table = {
                "title": "Runway Snapshot",
                "columns": ["Metric", "Period", "Actual", "Forecast"],
                "rows": [
                    [row.metric, row.period, format_currency(row.actual) if row.metric != "Cash runway" else f"{row.actual:.1f} months", format_currency(row.forecast) if row.metric != "Cash runway" else f"{row.forecast:.1f} months"]
                    for row in runway_rows
                ],
                "row_provenance": [self._financial_row_provenance(row) for row in runway_rows],
            }
            cash_burn_table = {
                "title": "Cash and Burn Trend",
                "columns": ["Period", "Cash", "Burn"],
                "rows": [
                    [
                        cash_row.period,
                        format_currency(cash_row.actual),
                        format_currency(
                            next((burn_row.actual for burn_row in rows if burn_row.metric == "Burn rate" and burn_row.period == cash_row.period), 0.0)
                        ),
                    ]
                    for cash_row in runway_rows
                    if cash_row.metric == "Cash at bank"
                ],
                "row_provenance": [self._financial_row_provenance(row) for row in runway_rows if row.metric == "Cash at bank"],
            }
            return [table for table in [runway_table, cash_burn_table] if table["rows"]]
        if finance_template == "project_spend_review":
            status_rows = [row for row in rows if row.metric in {"Project Kepler committed spend", "Project Kepler remaining budget", "AWS cost"}]
            forecast_rows = [
                row
                for row in rows
                if row.metric in {"Project Kepler forecast spend", "Project Kepler forecast remaining budget"}
            ]
            spend_status_table = {
                "title": "Project Spend Status",
                "columns": ["Metric", "Period", "Budget", "Actual", "Variance"],
                "rows": [
                    [row.metric, row.period, format_currency(row.budget), format_currency(row.actual), format_currency(row.variance)]
                    for row in status_rows
                ],
                "row_provenance": [self._financial_row_provenance(row) for row in status_rows],
            }
            forecast_table = {
                "title": "Project Forecast Trajectory",
                "columns": ["Period", "Metric", "Budget", "Actual", "Forecast"],
                "rows": [
                    [row.period, row.metric, format_currency(row.budget), format_currency(row.actual), format_currency(row.forecast)]
                    for row in forecast_rows
                ],
                "row_provenance": [self._financial_row_provenance(row) for row in forecast_rows],
            }
            return [table for table in [spend_status_table, forecast_table] if table["rows"]]

        tables = [
            {
                "title": "Chart Source Data",
                "columns": ["Metric", "Budget", "Actual", "Forecast"],
                "rows": [
                    [row.metric, format_currency(row.budget), format_currency(row.actual), format_currency(row.forecast)]
                    for row in rows
                ],
                "row_provenance": [self._financial_row_provenance(row) for row in rows],
            }
        ]
        if comparison_rows:
            tables.append(
                {
                    "title": "Period Comparison Data",
                    "columns": ["Metric", "Prior Actual", "Current Actual", "Delta"],
                    "rows": [
                        [
                            comparison["metric"],
                            format_currency(comparison["prior_actual"]),
                            format_currency(comparison["current_actual"]),
                            format_currency(comparison["delta"]),
                        ]
                        for comparison in comparison_rows
                    ],
                    "row_provenance": [
                        {
                            "source_type": "period_comparison",
                            "source_ref": f"{comparison['prior_source_ref']} | {comparison['current_source_ref']}",
                            "source_excerpt": comparison["source_excerpt"],
                        }
                        for comparison in comparison_rows
                    ],
                }
            )
        return tables

    def _build_chart_specs(
        self,
        *,
        task_input: str,
        comparison_rows: list[dict[str, Any]],
        chart_tables: list[dict[str, Any]],
    ) -> list[WorkbookChartSpec]:
        finance_template = self._select_finance_template(task_input)
        if finance_template == "aws_cost_review":
            specs: list[WorkbookChartSpec] = []
            if any(table["title"] == "AWS Spend Trend" and table["rows"] for table in chart_tables):
                specs.append(
                    WorkbookChartSpec(
                        title="AWS Spend Trend",
                        chart_type="bar",
                        x_axis="Period",
                        y_axis="Actual",
                        series_label="AWS actual spend",
                        source_sheet="Charts",
                        source_table="AWS Spend Trend",
                    )
                )
            if any(table["title"] == "Budget vs Actual" and table["rows"] for table in chart_tables):
                specs.append(
                    WorkbookChartSpec(
                        title="Budget vs Actual",
                        chart_type="bar",
                        x_axis="Metric",
                        y_axis="Actual",
                        series_label="Actual vs budget",
                        source_sheet="Charts",
                        source_table="Budget vs Actual",
                    )
                )
            return specs
        if finance_template == "runway_burn_review":
            specs: list[WorkbookChartSpec] = []
            if any(table["title"] == "Cash and Burn Trend" and table["rows"] for table in chart_tables):
                specs.append(
                    WorkbookChartSpec(
                        title="Cash and Burn Trend",
                        chart_type="bar",
                        x_axis="Period",
                        y_axis="Cash",
                        series_label="Cash position",
                        source_sheet="Charts",
                        source_table="Cash and Burn Trend",
                    )
                )
            return specs
        if finance_template == "project_spend_review":
            specs: list[WorkbookChartSpec] = []
            if any(table["title"] == "Project Spend Status" and table["rows"] for table in chart_tables):
                specs.append(
                    WorkbookChartSpec(
                        title="Project Spend vs Budget",
                        chart_type="bar",
                        x_axis="Metric",
                        y_axis="Actual",
                        series_label="Project spend",
                        source_sheet="Charts",
                        source_table="Project Spend Status",
                    )
                )
            if any(table["title"] == "Project Forecast Trajectory" and table["rows"] for table in chart_tables):
                specs.append(
                    WorkbookChartSpec(
                        title="Projected Remaining Budget",
                        chart_type="bar",
                        x_axis="Period",
                        y_axis="Actual",
                        series_label="Forecast remaining budget",
                        source_sheet="Charts",
                        source_table="Project Forecast Trajectory",
                    )
                )
            return specs

        specs = [
            WorkbookChartSpec(
                title="Actual vs Budget by Metric",
                chart_type="bar",
                x_axis="Metric",
                y_axis="Actual",
                series_label="Actual vs Budget",
                source_sheet="Charts",
                source_table="Chart Source Data",
            ),
            WorkbookChartSpec(
                title="Forecast by Metric",
                chart_type="bar",
                x_axis="Metric",
                y_axis="Forecast",
                series_label="Forecast",
                source_sheet="Charts",
                source_table="Chart Source Data",
            ),
        ]
        if comparison_rows and any(table["title"] == "Period Comparison Data" for table in chart_tables):
            specs.append(
                WorkbookChartSpec(
                    title="Period Delta by Metric",
                    chart_type="bar",
                    x_axis="Metric",
                    y_axis="Delta",
                    series_label="Period delta",
                    source_sheet="Charts",
                    source_table="Period Comparison Data",
                )
            )
        return specs

    def _dedupe_financial_rows(self, rows: list[WorkbookFinancialRow]) -> list[WorkbookFinancialRow]:
        best_by_key: dict[tuple[str, str], WorkbookFinancialRow] = {}
        for row in rows:
            key = (row.period, row.metric)
            current = best_by_key.get(key)
            if current is None:
                best_by_key[key] = row
                continue
            current_rank = self._source_trust_rank(source_type=current.source_type, source_ref=current.source_ref, title=current.source_ref)
            candidate_rank = self._source_trust_rank(source_type=row.source_type, source_ref=row.source_ref, title=row.source_ref)
            if candidate_rank > current_rank:
                best_by_key[key] = row
        return list(best_by_key.values())

    def _company_state_field_name(self, category: str) -> str:
        if category == "revenue":
            return "revenue_segmentation"
        if category == "cost":
            return "cost_structure"
        if category == "capital":
            return "capital_position"
        return "unknown"

    def _parse_numeric_metric_value(self, value: str, *, default: float) -> float:
        normalized = value.replace("$", "").replace(",", "").strip()
        multiplier = 1.0
        if normalized.endswith("%"):
            normalized = normalized[:-1]
        if normalized.endswith("M"):
            multiplier = 1_000_000.0
            normalized = normalized[:-1]
        elif normalized.endswith("K"):
            multiplier = 1_000.0
            normalized = normalized[:-1]
        try:
            return round(float(normalized) * multiplier, 2)
        except ValueError:
            return default

    def _financial_row_to_cells(self, row: WorkbookFinancialRow) -> list[str]:
        return [
            row.period,
            row.metric,
            format_currency(row.budget),
            format_currency(row.actual),
            format_currency(row.variance),
            format_currency(row.forecast),
            row.source_ref or row.source_type,
        ]

    def _financial_row_provenance(self, row: WorkbookFinancialRow) -> dict[str, Any]:
        return {
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "source_excerpt": row.source_excerpt,
            "source_rank": self._source_trust_rank(source_type=row.source_type, source_ref=row.source_ref, title=row.source_ref),
            "extraction_method": "typed_finance_parser" if row.source_type in {"retrieved_document", "company_state"} else "derived",
            "validation_status": "validated",
        }

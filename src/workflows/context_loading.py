from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Dict, List

from pydantic import BaseModel, Field

from src.agents.schemas import AgentAction, tool_action
from src.core.persona import AssistantPersona, build_persona_from_preferences
from src.core.vocabulary import CompanyVocabulary, extract_company_vocabulary, vocabulary_prompt_block
from src.presentation import QuantitativeEvidenceBundle
from src.workflows.action_items import normalize_structured_watch
from src.workflows.event_payloads import build_planning_context
from src.workflows.proactive_observations import observations_to_prompt_block, run_proactive_scan
from src.workflows.retrieval_manifest import PriorConversationRef, RetrievalManifest
from src.workflows.signal_extractor import normalize_signals
from src.workflows.planning_types import RequestPlan
from src.workflows.unified_memory import UnifiedMemoryState, build_unified_memory_state, unified_memory_from_payload


class ContextDocument(BaseModel):
    title: str
    content: str = ""
    source_authority: float = 0.50
    source_type: str = "reference"
    document_id: str | None = None


class ContextStageDefinition(BaseModel):
    name: str
    label: str
    description: str
    required_keys: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)


class ReportWorkflowContext(BaseModel):
    company_state: Dict[str, Any] = Field(default_factory=dict)
    company_identity: Dict[str, Any] = Field(default_factory=dict)
    preferences: Dict[str, Any] = Field(default_factory=dict)
    project_context: Dict[str, Any] = Field(default_factory=dict)
    retrieval_manifest: Dict[str, Any] = Field(default_factory=dict)
    session_history: List[Dict[str, Any]] = Field(default_factory=list)
    signals: List[Dict[str, Any]] = Field(default_factory=list)
    retrieved_documents: List[ContextDocument] = Field(default_factory=list)
    vocabulary: CompanyVocabulary = Field(default_factory=CompanyVocabulary)
    ceo_memories: List[Dict[str, Any]] = Field(default_factory=list)
    finance_context: Dict[str, Any] = Field(default_factory=dict)
    quantitative_evidence: QuantitativeEvidenceBundle = Field(default_factory=QuantitativeEvidenceBundle)
    live_context: Dict[str, Any] = Field(default_factory=dict)
    situational_profile: Dict[str, Any] = Field(default_factory=dict)
    entity_context: List[Dict[str, Any]] = Field(default_factory=list)
    unified_memory: Dict[str, Any] = Field(default_factory=dict)

    def prompt_payload(self) -> Dict[str, Any]:
        return {
            "company_state": self.company_state,
            "company_identity": self.company_identity,
            "preferences": self.preferences,
            "project_context": self.project_context,
            "retrieval_manifest": self.retrieval_manifest,
            "session_history": self.session_history,
            "signals": self.signals,
            "retrieved_documents": [document.model_dump() for document in self.retrieved_documents],
            "vocabulary_block": vocabulary_prompt_block(self.vocabulary),
            "ceo_memories": self.ceo_memories,
            "finance_context": self.finance_context,
            "quantitative_evidence": self.quantitative_evidence.model_dump(mode="json"),
            "live_context": self.live_context,
            "situational_profile": self.situational_profile,
            "entity_context": self.entity_context,
            "unified_memory": self.unified_memory,
        }


class DocumentExplanationContext(BaseModel):
    company_state: Dict[str, Any] = Field(default_factory=dict)
    preferences: Dict[str, Any] = Field(default_factory=dict)
    project_context: Dict[str, Any] = Field(default_factory=dict)
    retrieval_manifest: Dict[str, Any] = Field(default_factory=dict)
    retrieved_documents: List[ContextDocument] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)

    def prompt_payload(self) -> Dict[str, Any]:
        return {
            "company_state": self.company_state,
            "preferences": self.preferences,
            "project_context": self.project_context,
            "retrieval_manifest": self.retrieval_manifest,
            "retrieved_documents": [document.model_dump() for document in self.retrieved_documents],
            "attachments": self.attachments,
        }


class BriefingWorkflowContext(BaseModel):
    company_state: Dict[str, Any] = Field(default_factory=dict)
    preferences: Dict[str, Any] = Field(default_factory=dict)
    retrieved_documents: List[ContextDocument] = Field(default_factory=list)
    retrieval_manifest: Dict[str, Any] = Field(default_factory=dict)
    history: List[Dict[str, Any]] = Field(default_factory=list)
    signals: List[Dict[str, Any]] = Field(default_factory=list)
    request_plan: Dict[str, Any] = Field(default_factory=dict)
    ranked_threads: List[Dict[str, Any]] = Field(default_factory=list)
    upcoming_events: List[Dict[str, Any]] = Field(default_factory=list)
    structured_watch: Dict[str, Any] = Field(default_factory=dict)
    planning_context: Dict[str, Any] = Field(default_factory=dict)
    event_payload: Dict[str, Any] = Field(default_factory=dict)
    ceo_memories: List[Dict[str, Any]] = Field(default_factory=list)
    crm_deals: List[Dict[str, Any]] = Field(default_factory=list)
    live_context: Dict[str, Any] = Field(default_factory=dict)
    situational_profile: Dict[str, Any] = Field(default_factory=dict)
    entity_context: List[Dict[str, Any]] = Field(default_factory=list)
    unified_memory: Dict[str, Any] = Field(default_factory=dict)

    def prompt_payload(self) -> Dict[str, Any]:
        return {
            "company_state": self.company_state,
            "preferences": self.preferences,
            "retrieved_documents": [document.model_dump() for document in self.retrieved_documents],
            "retrieval_manifest": self.retrieval_manifest,
            "history": self.history,
            "signals": self.signals,
            "request_plan": self.request_plan,
            "ranked_threads": self.ranked_threads,
            "upcoming_events": self.upcoming_events,
            "structured_watch": self.structured_watch,
            "planning_context": self.planning_context,
            "event_payload": self.event_payload,
            "ceo_memories": self.ceo_memories,
            "crm_deals": self.crm_deals,
            "live_context": self.live_context,
            "situational_profile": self.situational_profile,
            "entity_context": self.entity_context,
            "unified_memory": self.unified_memory,
        }


REPORT_GENERATION_CONTEXT_STAGE_DEFINITIONS = [
    ContextStageDefinition(
        name="load_company_state",
        label="Load Company State",
        description="Hydrate the CEO-scoped operating and financial state before reasoning.",
        required_keys=["company_state"],
        tools=["get_company_state"],
    ),
    ContextStageDefinition(
        name="load_company_identity",
        label="Load Company Identity",
        description="Load the persisted company style and branding profile derived from exemplar materials.",
        required_keys=["company_identity"],
        tools=["get_company_identity_profile"],
    ),
    ContextStageDefinition(
        name="load_preferences",
        label="Load CEO Preferences",
        description="Load tone and decision preferences that shape report style and emphasis.",
        required_keys=["preferences"],
        tools=["get_preferences"],
    ),
    ContextStageDefinition(
        name="load_conversation_thread",
        label="Load Conversation Thread",
        description="Load the live conversation context so follow-up turns can reuse schedules, decisions, and recent contributions.",
        required_keys=["live_context"],
        tools=["get_live_context"],
    ),
    ContextStageDefinition(
        name="load_situational_profile",
        label="Load CEO Situational Profile",
        description="Load the CEO's current operating mode, active pressures, and recurring topics.",
        required_keys=["situational_profile"],
        tools=["get_situational_profile"],
    ),
    ContextStageDefinition(
        name="load_project_context",
        label="Load Project Context",
        description="Load the active user-defined project so retrieval and memory can bias toward its documents and prior conversations.",
        required_keys=["project_context"],
        tools=["get_project_context"],
    ),
    ContextStageDefinition(
        name="load_session_history",
        label="Load Session History",
        description="Load recent assistant interactions so report workflows can select explicit historical artifacts for period-aware comparisons.",
        required_keys=["session_history"],
        tools=["get_session_history"],
    ),
    ContextStageDefinition(
        name="load_signals",
        label="Load Signals",
        description="Load recent internal signals that should affect executive reporting when live operating issues are active.",
        required_keys=["signals"],
        tools=["get_recent_signals", "get_unread_signals"],
    ),
    ContextStageDefinition(
        name="retrieve_documents",
        label="Retrieve Supporting Documents",
        description="Pull indexed knowledge and company materials relevant to the request.",
        required_keys=["retrieval", "project_context", "session_history", "signals"],
        tools=["semantic_search", "google_drive_search"],
    ),
    ContextStageDefinition(
        name="load_memories",
        label="Load CEO Long-Term Memory",
        description="Load the CEO's persisted decisions, commitments, and preferences from long-term memory.",
        required_keys=["ceo_memories"],
        tools=["memory_management", "get_entity_context"],
    ),
    ContextStageDefinition(
        name="prepare_context",
        label="Prepare Report Context",
        description="Normalize the loaded context into the structured payload consumed by ReportAgent.",
        required_keys=["company_state", "company_identity", "preferences", "project_context", "session_history", "signals", "retrieval"],
        tools=[],
    ),
]

DOCUMENT_EXPLANATION_CONTEXT_STAGE_DEFINITIONS = [
    ContextStageDefinition(
        name="load_company_state",
        label="Load Company State",
        description="Hydrate CEO-scoped business context before document explanation.",
        required_keys=["company_state"],
        tools=["get_company_state"],
    ),
    ContextStageDefinition(
        name="load_preferences",
        label="Load CEO Preferences",
        description="Load explanation and communication preferences for the current CEO.",
        required_keys=["preferences"],
        tools=["get_preferences"],
    ),
    ContextStageDefinition(
        name="load_project_context",
        label="Load Project Context",
        description="Load the active project so the explainer can prioritize the right working set and prior discussion context.",
        required_keys=["project_context"],
        tools=["get_project_context"],
    ),
    ContextStageDefinition(
        name="retrieve_documents",
        label="Retrieve Related Material",
        description="Retrieve related indexed documents and uploaded material for evidence grounding.",
        required_keys=["retrieval", "project_context"],
        tools=["semantic_search"],
    ),
    ContextStageDefinition(
        name="prepare_context",
        label="Prepare Explanation Context",
        description="Normalize retrieval results and attachments into a structured explainer context.",
        required_keys=["company_state", "preferences", "project_context", "retrieval"],
        tools=[],
    ),
]

REPORT_GENERATION_CONTEXT_STAGES = [stage.name for stage in REPORT_GENERATION_CONTEXT_STAGE_DEFINITIONS]
DOCUMENT_EXPLANATION_CONTEXT_STAGES = [stage.name for stage in DOCUMENT_EXPLANATION_CONTEXT_STAGE_DEFINITIONS]
EVENT_BRIEFING_CONTEXT_STAGES = [
    "load_company_state",
    "load_preferences",
    "load_conversation_thread",
    "load_situational_profile",
    "load_session_history",
    "load_signals",
    "load_live_connector",
    "load_crm",
    "load_memories",
    "retrieve_documents",
    "prepare_context",
]
EVENT_BRIEFING_CONTEXT_STAGE_DEFINITIONS = [
    ContextStageDefinition(
        name="load_company_state",
        label="Load Company State",
        description="Hydrate CEO-scoped operating context before briefing or schedule synthesis.",
        required_keys=["company_state"],
        tools=["get_company_state"],
    ),
    ContextStageDefinition(
        name="load_preferences",
        label="Load CEO Preferences",
        description="Load ranking and communication preferences for watch and planning synthesis.",
        required_keys=["preferences"],
        tools=["get_preferences"],
    ),
    ContextStageDefinition(
        name="load_conversation_thread",
        label="Load Conversation Thread",
        description="Load the live conversation context so briefing and planning follow-ups can reuse prior schedule and decision state.",
        required_keys=["live_context"],
        tools=["get_live_context"],
    ),
    ContextStageDefinition(
        name="load_situational_profile",
        label="Load CEO Situational Profile",
        description="Load the CEO's current operating mode, active pressures, and recurring topics.",
        required_keys=["situational_profile"],
        tools=["get_situational_profile"],
    ),
    ContextStageDefinition(
        name="load_session_history",
        label="Load Session History",
        description="Load recent assistant history so planner-led requests can reuse prior context and decisions.",
        required_keys=["history"],
        tools=["get_session_history"],
    ),
    ContextStageDefinition(
        name="load_signals",
        label="Load Signals",
        description="Load recent signal state that can reinforce inbox and planning prioritization.",
        required_keys=["signals"],
        tools=["get_recent_signals", "get_unread_signals"],
    ),
    ContextStageDefinition(
        name="load_live_connector",
        label="Load Live Connector Data",
        description="Fetch real-time emails and calendar events from connected provider accounts for current briefing context.",
        required_keys=[],
        tools=["read_email_threads", "read_calendar_events"],
    ),
    ContextStageDefinition(
        name="load_crm",
        label="Load CRM Pipeline",
        description="Fetch active deal pipeline from HubSpot or Salesforce for meeting prep and stakeholder context.",
        required_keys=[],
        tools=["crm_deal_context"],
    ),
    ContextStageDefinition(
        name="load_memories",
        label="Load CEO Long-Term Memory",
        description="Load the CEO's persisted decisions, commitments, and preferences from long-term memory.",
        required_keys=["ceo_memories"],
        tools=["memory_management"],
    ),
    ContextStageDefinition(
        name="retrieve_documents",
        label="Retrieve Supporting Documents",
        description="Pull documents relevant to the current planning horizon or executive request.",
        required_keys=["retrieval"],
        tools=["semantic_search", "google_drive_search"],
    ),
    ContextStageDefinition(
        name="prepare_context",
        label="Prepare Briefing Context",
        description="Normalize reusable context into the payload consumed by briefing synthesis inside the active workflow path.",
        required_keys=["company_state", "preferences", "history", "signals", "retrieval"],
        tools=[],
    ),
]


def build_report_context_actions(task_input: str, context: Dict[str, Any]) -> List[AgentAction]:
    actions: List[AgentAction] = []
    if "company_state" not in context:
        actions.append(tool_action("get_company_state", result_key="company_state"))
    if "company_identity" not in context:
        actions.append(tool_action("get_company_identity_profile", result_key="company_identity"))
    if "preferences" not in context:
        actions.append(tool_action("get_preferences", result_key="preferences"))
    if "live_context" not in context:
        actions.append(tool_action("get_live_context", result_key="live_context"))
    if "situational_profile" not in context:
        actions.append(tool_action("get_situational_profile", result_key="situational_profile"))
    if "project_context" not in context:
        actions.append(tool_action("get_project_context", result_key="project_context"))
    if "session_history" not in context:
        actions.append(tool_action("get_session_history", result_key="session_history", limit=12))
    if "signals" not in context:
        actions.append(tool_action("get_recent_signals", result_key="signals", limit=5))
    if "retrieval" not in context:
        project_context = context.get("project_context") or {}
        actions.append(
            tool_action(
                "semantic_search",
                result_key="retrieval",
                query=task_input,
                limit=5,
                preferred_document_ids=project_context.get("document_ids", []),
            )
        )
    if "drive_retrieval" not in context:
        actions.append(
            tool_action(
                "google_drive_search",
                result_key="drive_retrieval",
                query=task_input,
                max_results=5,
                read_contents_limit=2,
            )
        )
    return actions


def build_document_explanation_context_actions(
    task_input: str,
    context: Dict[str, Any],
) -> List[AgentAction]:
    actions: List[AgentAction] = []
    if "company_state" not in context:
        actions.append(tool_action("get_company_state", result_key="company_state"))
    if "preferences" not in context:
        actions.append(tool_action("get_preferences", result_key="preferences"))
    if "project_context" not in context:
        actions.append(tool_action("get_project_context", result_key="project_context"))
    if "retrieval" not in context:
        project_context = context.get("project_context") or {}
        attachment_ids = [
            str(attachment.get("document_id") or attachment.get("file_id") or attachment.get("id") or "").strip()
            for attachment in (context.get("attachments") or [])
            if isinstance(attachment, dict)
        ]
        preferred_document_ids = [
            document_id
            for document_id in (
                [str(doc_id).strip() for doc_id in (project_context.get("document_ids", []) if isinstance(project_context, dict) else [])]
                + attachment_ids
            )
            if document_id
        ]
        actions.append(
            tool_action(
                "semantic_search",
                result_key="retrieval",
                query=task_input,
                limit=6,
                preferred_document_ids=preferred_document_ids,
            )
        )
    return actions


def prepare_report_context(context: Dict[str, Any]) -> ReportWorkflowContext:
    company_state = _normalize_state(context.get("company_state"))
    vocab = extract_company_vocabulary(company_state)
    retrieved_documents = _normalize_retrieval(
        context.get("retrieval"),
        drive_raw=context.get("drive_retrieval"),
    )
    signals = _normalize_signals(context.get("signals"))
    session_history = _normalize_history(context.get("session_history"))
    live_context = _normalize_live_context(context.get("live_context"))
    situational_profile = _normalize_situational_profile(context.get("situational_profile"))
    entity_context = _normalize_entity_context(context.get("entity_context"))
    unified_memory = _prepare_unified_memory(
        context=context,
        session_history=session_history,
        signals=signals,
        retrieved_documents=retrieved_documents,
        live_context=live_context,
        situational_profile=situational_profile,
        entity_context=entity_context,
    )
    finance_context = _build_finance_context(
        company_state=company_state,
        retrieved_documents=retrieved_documents,
        signals=signals,
        session_history=session_history,
    )
    return ReportWorkflowContext(
        company_state=company_state,
        company_identity=_normalize_identity(context.get("company_identity")),
        preferences=_normalize_preferences(context.get("preferences")),
        project_context=_normalize_project_context(context.get("project_context")),
        retrieval_manifest=dict(context.get("retrieval_manifest") or {}),
        session_history=session_history,
        signals=signals,
        retrieved_documents=retrieved_documents,
        vocabulary=vocab,
        ceo_memories=_normalize_memories(context.get("ceo_memories")),
        finance_context=finance_context,
        quantitative_evidence=_build_quantitative_evidence_bundle(
            company_state=company_state,
            retrieved_documents=retrieved_documents,
            finance_context=finance_context,
        ),
        live_context=live_context,
        situational_profile=situational_profile,
        entity_context=entity_context,
        unified_memory=unified_memory,
    )


def prepare_document_explanation_context(
    context: Dict[str, Any],
    attachments: List[Dict[str, Any]],
) -> DocumentExplanationContext:
    return DocumentExplanationContext(
        company_state=_normalize_state(context.get("company_state")),
        preferences=_normalize_preferences(context.get("preferences")),
        project_context=_normalize_project_context(context.get("project_context")),
        retrieval_manifest=dict(context.get("retrieval_manifest") or {}),
        retrieved_documents=_normalize_retrieval(context.get("retrieval")),
        attachments=attachments,
    )


def _extract_response_summary(raw_response: Any) -> str:
    """Extract a readable summary from a serialized AssistantMessageResponse JSON.

    For schedule/briefing responses, preserves structured presentation data (blocks,
    meetings, deadlines) so downstream agents (e.g. ReportAgent building a PPTX) can
    use the actual schedule content rather than just a prose summary.
    """
    if not raw_response:
        return ""
    if isinstance(raw_response, str):
        try:
            parsed = json.loads(raw_response)
            if isinstance(parsed, dict):
                answer = parsed.get("answer", {})
                title = answer.get("title", "") if isinstance(answer, dict) else ""
                summary = answer.get("summary", "") if isinstance(answer, dict) else ""
                presentation = parsed.get("presentation") or {}
                weekly_plan = presentation.get("weekly_plan") if isinstance(presentation, dict) else None

                # For schedule/briefing responses: include structured plan data so
                # cross-workflow handoffs (e.g. schedule → PPTX) have the real content.
                if weekly_plan and isinstance(weekly_plan, dict):
                    blocks = weekly_plan.get("blocks") or []
                    meetings = weekly_plan.get("meetings") or []
                    deadlines = weekly_plan.get("deadlines") or []
                    parts = [f"{title}: {summary}".strip(": ").strip()] if (title or summary) else []
                    if blocks:
                        block_lines = []
                        for b in blocks[:10]:
                            label = b.get("title", "")
                            day = b.get("day_label") or b.get("starts_at", "")[:10]
                            time = b.get("time_window") or b.get("starts_at", "")[11:16]
                            reason = b.get("reason", "")
                            block_lines.append(f"  - {day} {time} | {label}" + (f": {reason}" if reason else ""))
                        parts.append("Schedule blocks:\n" + "\n".join(block_lines))
                    if meetings:
                        mtg_lines = [f"  - {m.get('title', '')} @ {m.get('starts_at', '')[:16]}" for m in meetings[:5]]
                        parts.append("Meetings:\n" + "\n".join(mtg_lines))
                    if deadlines:
                        parts.append("Deadlines: " + "; ".join(str(d) for d in deadlines[:5]))
                    return "\n".join(parts)[:1200]

                # For report/briefing without a weekly plan: title + summary is enough.
                if title or summary:
                    combined = f"{title}: {summary}".strip(": ").strip()
                    return combined[:400]
        except (json.JSONDecodeError, TypeError):
            pass
        return raw_response[:300].strip()
    return str(raw_response)[:300]


def serialize_context_stage_definitions(stage_definitions: List[ContextStageDefinition]) -> str:
    return json.dumps([stage.model_dump() for stage in stage_definitions])


_BRIEFING_WORKFLOW_TYPES = frozenset(
    {
        "email_ingestion",
        "email_watcher",
        "calendar_briefing",
        "morning_brief",
        "day_schedule_planning",
        "week_schedule_planning",
        "schedule_planning",
        "meeting_prep",
        "weekly_recap",
    }
)


def get_context_stage_definitions(workflow_type: str) -> List[ContextStageDefinition]:
    if workflow_type == "document_explanation":
        return DOCUMENT_EXPLANATION_CONTEXT_STAGE_DEFINITIONS
    if workflow_type in _BRIEFING_WORKFLOW_TYPES:
        return EVENT_BRIEFING_CONTEXT_STAGE_DEFINITIONS
    return REPORT_GENERATION_CONTEXT_STAGE_DEFINITIONS


def get_context_stage_definition(workflow_type: str, stage_name: str) -> ContextStageDefinition | None:
    for stage in get_context_stage_definitions(workflow_type):
        if stage.name == stage_name:
            return stage
    return None


# ---------------------------------------------------------------------------
# Stage handlers — one function per stage, registered in _STAGE_HANDLERS below.
# To add a new stage: write a handler function and add one entry to the dict.
# Never add an elif here.
# ---------------------------------------------------------------------------

def _stage_company_state(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    return [tool_action("get_company_state", result_key="company_state")]


def _stage_company_identity(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    return [tool_action("get_company_identity_profile", result_key="company_identity")]


def _stage_preferences(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    return [tool_action("get_preferences", result_key="preferences")]


def _stage_conversation_thread(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    return [tool_action("get_live_context", result_key="live_context")]


def _stage_situational_profile(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    return [tool_action("get_situational_profile", result_key="situational_profile")]


def _stage_project_context(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    project_id = meta.get("project_id")
    if not project_id:
        return []
    return [tool_action("get_project_context", result_key="project_context", project_id=project_id)]


def _stage_retrieve_documents(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    workflow_type = meta.get("workflow_type", "")
    limit = 6 if workflow_type == "document_explanation" else 5
    project_context = meta.get("project_context") or {}
    preferred_document_ids = project_context.get("document_ids", []) if isinstance(project_context, dict) else []
    request_plan = meta.get("request_plan") or {}
    query = task_input
    if isinstance(request_plan, dict) and request_plan.get("mode") == "compound_plan":
        retrieval_plan = request_plan.get("retrieval_plan") or {}
        retrieval_sources = retrieval_plan.get("sources", []) if isinstance(retrieval_plan, dict) else []
        source_names = [
            str(source.get("source") or "").strip()
            for source in retrieval_sources
            if isinstance(source, dict) and str(source.get("source") or "").strip()
        ]
        needed_sources = source_names or list(request_plan.get("needed_context_sources", []))
        query = (
            f"{task_input}\n"
            f"Planning horizon: {request_plan.get('time_horizon', 'unspecified')}\n"
            f"Needed context sources: {', '.join(needed_sources)}"
        )
    return [
        tool_action("semantic_search", result_key="retrieval", query=query, limit=limit, preferred_document_ids=preferred_document_ids),
        tool_action("google_drive_search", result_key="drive_retrieval", query=query, max_results=5, read_contents_limit=2),
    ]


def _stage_session_history(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    return [tool_action("get_session_history", result_key="history", limit=5)]


def _stage_signals(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    workflow_type = meta.get("workflow_type", "")
    tool_name = "get_recent_signals" if workflow_type in _BRIEFING_WORKFLOW_TYPES else "get_unread_signals"
    return [tool_action(tool_name, result_key="signals", limit=5)]


def _stage_crm(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    if meta.get("workflow_type") not in {"meeting_prep", "calendar_briefing"}:
        return []
    return [tool_action("crm_deal_context", result_key="crm_deals", action="list_deals", limit=10)]


def _stage_memories(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    request_plan = meta.get("request_plan") or {}
    query = (request_plan.get("semantic_query") if isinstance(request_plan, dict) else None) or task_input
    if not query:
        return [tool_action("memory_management", result_key="ceo_memories", action="list", limit=20)]
    from src.core.entity_extraction import extract_entities_from_text
    entities = extract_entities_from_text(query)
    actions: List[AgentAction] = [
        tool_action("memory_management", result_key="ceo_memories", action="search", query=query, limit=12)
    ]
    if entities:
        actions.append(tool_action("get_entity_context", result_key="entity_context", entities=entities, limit=8))
    return actions


_LIVE_CONNECTOR_SOURCES: Dict[str, List[str]] = {
    "email_watcher": ["email"],
    "email_ingestion": ["email"],
    "morning_brief": ["email", "calendar"],
    "weekly_recap": ["email", "calendar"],
    "calendar_briefing": ["calendar"],
    "meeting_prep": ["email", "calendar"],
    "schedule_planning": ["email", "calendar"],
    "day_schedule_planning": ["email", "calendar"],
    "week_schedule_planning": ["email", "calendar"],
}


def _stage_live_connector(task_input: str, meta: Dict[str, Any]) -> List[AgentAction]:
    workflow_type = meta.get("workflow_type", "")
    actions: List[AgentAction] = []
    sources = _LIVE_CONNECTOR_SOURCES.get(str(workflow_type), ["email", "calendar"])
    if "email" in sources:
        actions.append(tool_action("read_email_threads", result_key="live_threads", limit=10))
    if "calendar" in sources:
        actions.append(tool_action("read_calendar_events", result_key="live_events", max_results=15))
    return actions


_STAGE_HANDLERS: Dict[str, Callable[[str, Dict[str, Any]], List[AgentAction]]] = {
    "load_company_state":      _stage_company_state,
    "load_company_identity":   _stage_company_identity,
    "load_preferences":        _stage_preferences,
    "load_conversation_thread": _stage_conversation_thread,
    "load_situational_profile": _stage_situational_profile,
    "load_project_context":    _stage_project_context,
    "retrieve_documents":      _stage_retrieve_documents,
    "load_session_history":    _stage_session_history,
    "load_signals":            _stage_signals,
    "load_crm":                _stage_crm,
    "load_memories":           _stage_memories,
    "load_live_connector":     _stage_live_connector,
}


def build_context_stage_actions(
    workflow_type: str,
    stage_name: str,
    task_input: str,
    workflow_metadata: Dict[str, Any] | None = None,
) -> List[AgentAction]:
    handler = _STAGE_HANDLERS.get(stage_name)
    if handler is None:
        return []
    meta = {**(workflow_metadata or {}), "workflow_type": workflow_type}
    return handler(task_input, meta)


_HISTORY_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "i", "my", "me", "we", "our",
    "it", "its", "to", "of", "in", "on", "for", "and", "or", "do", "did", "be",
    "what", "how", "can", "you", "your", "this", "that", "with", "from", "at",
    "up", "will", "just", "about", "any", "some", "there", "have", "has",
}


def _score_history_relevance(item: Dict, task_words: set) -> int:
    """Score a history item by keyword overlap with the current task."""
    text = (
        str(item.get("query") or "") + " " +
        str(item.get("response") or "") + " " +
        str(item.get("intent") or "")
    ).lower()
    item_words = set(text.split()) - _HISTORY_STOPWORDS
    return len(task_words & item_words)


def build_retrieval_manifest(aggregate_context: Dict[str, Any]) -> RetrievalManifest:
    """Build a RetrievalManifest from the assembled context aggregate."""
    prepared = aggregate_context.get("prepared_context") or {}
    task_input = str(aggregate_context.get("task_input") or "")

    # Documents
    retrieved_docs = prepared.get("retrieved_documents") or aggregate_context.get("retrieval") or []
    retrieved_docs = retrieved_docs if isinstance(retrieved_docs, list) else []
    doc_names: List[str] = []
    gaps: List[str] = []
    doc_authorities: List[float] = []
    for doc in retrieved_docs:
        if not isinstance(doc, dict):
            continue
        authority = float(doc.get("source_authority", 0.5))
        title = doc.get("title", "Untitled")
        label = " (low authority)" if authority < 0.65 else ""
        doc_names.append(f"{title}{label}")
        doc_authorities.append(authority)
    if not doc_names:
        gaps.append("No supporting documents found")
    elif doc_authorities and max(doc_authorities) < 0.65:
        gaps.append("All sources are low-authority — no primary documents found for this topic")

    # Signals
    signals = prepared.get("signals") or aggregate_context.get("signals") or []
    signals = signals if isinstance(signals, list) else []
    signal_summaries: List[str] = []
    for s in signals[:4]:
        if not isinstance(s, dict):
            continue
        parts = [str(s.get("subject") or s.get("title") or s.get("source") or "")]
        if s.get("urgency"):
            parts.append(f"urgency:{s['urgency']}")
        signal_summaries.append(" ".join(p for p in parts if p).strip())
    if not signals:
        gaps.append("No recent signals found")

    # Memories
    memories = prepared.get("ceo_memories") or aggregate_context.get("ceo_memories") or []
    memory_summaries: List[str] = []
    for m in memories[:5] if isinstance(memories, list) else []:
        if not isinstance(m, dict):
            continue
        title = m.get("title", "")
        mem_type = m.get("memory_type", "")
        if title:
            memory_summaries.append(f"[{mem_type}] {title}" if mem_type else title)
    if not memory_summaries:
        gaps.append("No CEO memories found for this topic")

    # Live connector counts
    event_payload = prepared.get("event_payload") or aggregate_context.get("event_payload") or {}
    live_threads = event_payload.get("live_threads") or []
    live_events = event_payload.get("live_events") or []

    # Prior conversation refs — select by relevance to task_input, not just recency
    history = prepared.get("history") or prepared.get("session_history") or []
    history = history if isinstance(history, list) else []
    task_words = set(task_input.lower().split()) - _HISTORY_STOPWORDS
    scored = []
    for item in history:
        if not isinstance(item, dict) or not item.get("query"):
            continue
        score = _score_history_relevance(item, task_words)
        ts_raw = str(item.get("timestamp") or "")
        scored.append((score, ts_raw, item))
    # Sort: primary by relevance desc, secondary by recency desc
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    refs: List[PriorConversationRef] = []
    now = datetime.now()
    for _, _, item in scored[:3]:
        query = item.get("query") or ""
        response = item.get("response") or ""
        ts = str(item.get("timestamp") or "")[:10]
        intent = item.get("intent") or ""
        days_ago: int | None = None
        try:
            item_dt = datetime.fromisoformat(str(item.get("timestamp") or ""))
            days_ago = (now - item_dt).days
        except (ValueError, TypeError):
            pass
        refs.append(PriorConversationRef(
            turn_summary=f"{ts}: CEO asked: \"{query[:120]}\"" + (f" — {response[:120]}" if response else ""),
            interaction_id=item.get("id"),
            days_ago=days_ago,
            intent=intent,
        ))
    if not refs and history:
        gaps.append("Prior conversation history exists but none matched the current topic")
    elif not refs:
        gaps.append("No prior conversation history found")

    # CRM
    crm_deals = prepared.get("crm_deals") or []

    return RetrievalManifest(
        documents_loaded=doc_names,
        signals_found=len(signals),
        signals_summary=[s for s in signal_summaries if s],
        memories_surfaced=memory_summaries,
        live_threads_scanned=len(live_threads) if isinstance(live_threads, list) else 0,
        live_events_scanned=len(live_events) if isinstance(live_events, list) else 0,
        prior_conversation_refs=refs,
        retrieval_gaps=gaps,
        crm_deals_loaded=len(crm_deals) if isinstance(crm_deals, list) else 0,
    )


def finalize_context_stage(
    workflow_type: str,
    stage_name: str,
    aggregate_context: Dict[str, Any],
    attachments: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    if stage_name == "prepare_context":
        if workflow_type in _BRIEFING_WORKFLOW_TYPES:
            prepared = prepare_briefing_context(aggregate_context, attachments or [])
        elif workflow_type == "document_explanation":
            prepared = prepare_document_explanation_context(aggregate_context, attachments or [])
        else:
            prepared = prepare_report_context(aggregate_context)
        finalized: Dict[str, Any] = {"prepared_context": prepared.prompt_payload()}

        # Build and attach retrieval manifest
        aggregate_context["prepared_context"] = finalized["prepared_context"]
        manifest = build_retrieval_manifest(aggregate_context)
        finalized["retrieval_manifest"] = manifest.model_dump()

        # Build and attach persona
        preferences = aggregate_context.get("preferences") or {}
        if isinstance(preferences, dict) and preferences.get("preferences"):
            preferences = preferences["preferences"]
        persona = build_persona_from_preferences(
            preferences=preferences if isinstance(preferences, dict) else {},
            company_name=(aggregate_context.get("company_state") or {}).get("company_name", ""),
        )
        finalized["persona"] = persona.model_dump()

        # Run proactive observation scan
        situational_profile = aggregate_context.get("situational_profile") or {}
        if isinstance(situational_profile, dict) and "situational_profile" in situational_profile:
            situational_profile = situational_profile["situational_profile"]
        live_ctx = aggregate_context.get("live_context") or {}
        if isinstance(live_ctx, dict) and "live_context" in live_ctx:
            live_ctx = live_ctx["live_context"]
        signals_raw = aggregate_context.get("signals") or []
        entity_ctx_raw = aggregate_context.get("entity_context") or {}
        entity_ctx: list = (
            entity_ctx_raw.get("entity_context", [])
            if isinstance(entity_ctx_raw, dict)
            else (entity_ctx_raw if isinstance(entity_ctx_raw, list) else [])
        )
        observations = run_proactive_scan(
            task_input=aggregate_context.get("task_input") or "",
            situational_profile=situational_profile if isinstance(situational_profile, dict) else {},
            live_context=live_ctx if isinstance(live_ctx, dict) else {},
            signals=signals_raw if isinstance(signals_raw, list) else [],
            entity_context=entity_ctx,
            max_observations=3,
        )
        finalized["proactive_observations"] = [o.model_dump() for o in observations]
        finalized["proactive_observations_block"] = observations_to_prompt_block(observations)

        return finalized
    return {}


def collect_workflow_context_from_stage_outputs(
    workflow_type: str,
    stage_outputs: Dict[str, Any],
) -> Dict[str, Any]:
    aggregate: Dict[str, Any] = {}

    stage_names = (
        EVENT_BRIEFING_CONTEXT_STAGES
        if workflow_type in _BRIEFING_WORKFLOW_TYPES
        else REPORT_GENERATION_CONTEXT_STAGES
    )

    for stage_name in stage_names:
        output = stage_outputs.get(stage_name)
        if not isinstance(output, dict):
            continue
        tool_context = output.get("tool_context", {})
        if isinstance(tool_context, dict):
            aggregate.update(tool_context)
        prepared_context = output.get("prepared_context")
        if isinstance(prepared_context, dict):
            aggregate.update(
                {
                    "company_state": prepared_context.get("company_state", aggregate.get("company_state")),
                    "company_identity": prepared_context.get("company_identity", aggregate.get("company_identity")),
                    "preferences": prepared_context.get("preferences", aggregate.get("preferences")),
                    "project_context": prepared_context.get("project_context", aggregate.get("project_context")),
                    "retrieval": prepared_context.get("retrieved_documents", aggregate.get("retrieval")),
                    "signals": prepared_context.get("signals", aggregate.get("signals")),
                    "history": prepared_context.get("history", aggregate.get("history")),
                    "live_context": prepared_context.get("live_context", aggregate.get("live_context")),
                    "situational_profile": prepared_context.get("situational_profile", aggregate.get("situational_profile")),
                    "event_payload": prepared_context.get("event_payload", aggregate.get("event_payload")),
                    "prepared_context": prepared_context,
                }
            )
        retrieval_manifest = output.get("retrieval_manifest")
        if isinstance(retrieval_manifest, dict):
            aggregate["retrieval_manifest"] = retrieval_manifest
        persona = output.get("persona")
        if isinstance(persona, dict):
            aggregate["persona"] = persona
        proactive_observations = output.get("proactive_observations")
        if isinstance(proactive_observations, list):
            aggregate["proactive_observations"] = proactive_observations
        proactive_observations_block = output.get("proactive_observations_block")
        if isinstance(proactive_observations_block, str):
            aggregate["proactive_observations_block"] = proactive_observations_block

    if workflow_type == "document_explanation":
        aggregate.setdefault("prepared_context", {}).setdefault("attachments", [])

    return aggregate


def prepare_briefing_context(context: Dict[str, Any], attachments: List[Dict[str, Any]]) -> BriefingWorkflowContext:
    event_payload = _normalize_event_payload(context.get("event_payload"), attachments)
    request_plan = _normalize_request_plan(context.get("request_plan"))
    ranked_threads = [thread for thread in (context.get("ranked_threads") or []) if isinstance(thread, dict)]
    upcoming_events = _normalize_calendar_events(context.get("upcoming_events"))
    structured_watch = normalize_structured_watch(
        dict(context.get("structured_watch") or {}),
        upcoming_events=upcoming_events,
        reference_dt=datetime.now().astimezone(),
    )
    planning_context = dict(context.get("planning_context") or {})
    # Merge live connector reads into the event payload so agents see them directly
    live_threads = _extract_live_threads(context)
    live_events = _extract_live_events(context)
    if live_threads and not ranked_threads:
        ranked_threads = live_threads
    if live_events and not upcoming_events:
        upcoming_events = live_events
    if live_threads:
        event_payload["live_threads"] = live_threads
    if live_events:
        event_payload["live_events"] = live_events
    if upcoming_events:
        event_payload["upcoming_events"] = upcoming_events
    if request_plan:
        request_plan_context = build_planning_context(
            message=str(context.get("task_input") or context.get("query") or ""),
            request_plan=request_plan,
            ranked_threads=ranked_threads,
            upcoming_events=upcoming_events,
            structured_watch=structured_watch,
            document_context=dict(context.get("document_context") or {}) or None,
            execution_steps=list(context.get("execution_steps") or []),
        )
        planning_context.update(request_plan_context)
    event_payload["planning_context"] = dict(planning_context)
    history = _normalize_history(context.get("history"))
    signals = _normalize_signals(context.get("signals"))
    live_context = _normalize_live_context(context.get("live_context"))
    situational_profile = _normalize_situational_profile(context.get("situational_profile"))
    entity_context = _normalize_entity_context(context.get("entity_context"))
    retrieved_documents = _normalize_retrieval(
        context.get("retrieval"),
        drive_raw=context.get("drive_retrieval"),
    )
    unified_memory = _prepare_unified_memory(
        context=context,
        session_history=history,
        signals=signals,
        retrieved_documents=retrieved_documents,
        live_context=live_context,
        situational_profile=situational_profile,
        entity_context=entity_context,
    )
    return BriefingWorkflowContext(
        company_state=_normalize_state(context.get("company_state")),
        preferences=_normalize_preferences(context.get("preferences")),
        retrieved_documents=retrieved_documents,
        retrieval_manifest=dict(context.get("retrieval_manifest") or {}),
        history=history,
        signals=signals,
        request_plan=request_plan.model_dump(mode="json") if request_plan else {},
        ranked_threads=ranked_threads,
        upcoming_events=upcoming_events,
        structured_watch=structured_watch,
        planning_context=planning_context,
        event_payload=event_payload,
        ceo_memories=_normalize_memories(context.get("ceo_memories")),
        crm_deals=_extract_crm_deals(context.get("crm_deals")),
        live_context=live_context,
        situational_profile=situational_profile,
        entity_context=entity_context,
        unified_memory=unified_memory,
    )


def _prepare_unified_memory(
    *,
    context: Dict[str, Any],
    session_history: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    retrieved_documents: List[ContextDocument],
    live_context: Dict[str, Any],
    situational_profile: Dict[str, Any],
    entity_context: List[Dict[str, Any]],
) -> Dict[str, Any]:
    existing = unified_memory_from_payload(context.get("unified_memory"))
    if existing is None:
        existing = build_unified_memory_state(
            resolved_intent={},
            conversation_id=((live_context or {}).get("conversation_id") if isinstance(live_context, dict) else None),
            live_context=live_context,
            preferences=_normalize_preferences(context.get("preferences")),
            situational_profile=situational_profile,
            ceo_memories=_normalize_memories(context.get("ceo_memories")),
            recent_history=_compact_history_rows(session_history),
            artifact_context={},
            project_context=_normalize_project_context(context.get("project_context")),
            signals=signals,
            retrieved_documents=[document.model_dump() for document in retrieved_documents],
            retrieval_manifest=context.get("retrieval_manifest") or {},
            entity_context=entity_context,
        )
    existing.long_term_memory.preferences = _normalize_preferences(context.get("preferences"))
    existing.long_term_memory.ceo_memories = _normalize_memories(context.get("ceo_memories"))
    existing.session_memory.turn_count = int(live_context.get("turn_count") or existing.session_memory.turn_count or 0)
    existing.session_memory.current_schedule = dict(live_context.get("current_schedule") or {})
    existing.session_memory.open_decisions = [str(item) for item in (live_context.get("open_decisions") or []) if str(item)]
    existing.session_memory.open_commitments = [str(item) for item in (live_context.get("open_commitments") or []) if str(item)]
    existing.session_memory.pending_actions = [item for item in (live_context.get("pending_actions") or []) if isinstance(item, dict)]
    existing.session_memory.entities_in_play = dict(live_context.get("entities_in_play") or {})
    existing.session_memory.last_agent_contributions = [item for item in (live_context.get("last_agent_contributions") or []) if isinstance(item, dict)]
    existing.session_memory.situational_profile = situational_profile
    existing.session_memory.recent_history = _compact_history_rows(session_history)
    existing.retrieval_evidence.project_context = _normalize_project_context(context.get("project_context"))
    existing.retrieval_evidence.signals = signals
    existing.retrieval_evidence.retrieved_documents = [document.model_dump() for document in retrieved_documents]
    existing.retrieval_evidence.retrieval_manifest = dict(context.get("retrieval_manifest") or {})
    existing.retrieval_evidence.entity_context = entity_context
    return existing.model_dump(mode="json")


def _compact_history_rows(history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    compact: List[Dict[str, str]] = []
    for item in history[-6:]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "query": str(item.get("query") or "")[:220],
                "response": _extract_response_summary(item.get("response"))[:320],
                "timestamp": str(item.get("timestamp") or "")[:19],
            }
        )
    return compact


def _normalize_state(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        if "state" in raw:
            inner = raw["state"]
            return inner if isinstance(inner, dict) else {}
        return raw
    return {}


def _normalize_preferences(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        if "preferences" in raw:
            inner = raw["preferences"]
            return inner if isinstance(inner, dict) else {}
        return raw
    return {}


def _normalize_identity(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        if "company_identity" in raw:
            inner = raw["company_identity"]
            return inner if isinstance(inner, dict) else {}
        return raw
    return {}


def _normalize_project_context(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        if "project_context" in raw:
            inner = raw["project_context"]
            return inner if isinstance(inner, dict) else {}
        return raw
    return {}


def _normalize_live_context(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        if "live_context" in raw:
            inner = raw["live_context"]
            return inner if isinstance(inner, dict) else {}
        return raw
    return {}


def _normalize_situational_profile(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        if "situational_profile" in raw:
            inner = raw["situational_profile"]
            return inner if isinstance(inner, dict) else {}
        return raw
    return {}


def _normalize_entity_context(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("entity_context") or []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _normalize_retrieval(raw: Any, drive_raw: Any = None) -> List[ContextDocument]:
    if isinstance(raw, dict):
        raw = raw.get("results", [])
    if not isinstance(raw, list):
        raw = []

    documents: List[ContextDocument] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        purpose = item.get("purpose", "reference")
        identity_role = item.get("identity_role")
        authority = item.get("source_authority") or _score_authority(purpose, identity_role)
        documents.append(
            ContextDocument(
                title=item.get("title", "Untitled"),
                content=item.get("content", ""),
                source_authority=authority,
                source_type=purpose,
                document_id=item.get("document_id"),
            )
        )

    # Merge Google Drive results as supplementary references
    if drive_raw is not None:
        documents.extend(_normalize_drive_results(drive_raw))

    documents = _deduplicate_documents(documents)
    documents.sort(key=lambda d: d.source_authority, reverse=True)
    return documents


def _normalize_drive_results(raw: Any) -> List[ContextDocument]:
    """Convert google_drive_search tool result into ContextDocument list (metadata only, no content)."""
    if isinstance(raw, dict):
        raw = raw.get("files") or (raw.get("data") or {}).get("files", [])
    if not isinstance(raw, list):
        return []
    documents: List[ContextDocument] = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        name = f.get("name", "Untitled Drive File")
        file_type = f.get("type", "")
        modified = f.get("modified_at", "")
        exportable = f.get("exportable", False)
        description = str(f.get("content_excerpt") or f.get("content") or file_type)
        if modified:
            description += f" — last modified {modified[:10]}"
        if exportable:
            description += " (readable via google_drive_read)"
        documents.append(
            ContextDocument(
                title=name,
                content=description,
                source_authority=0.55,
                source_type="drive_document",
                document_id=f.get("file_id"),
            )
        )
    return documents


def _extract_crm_deals(raw: Any) -> List[Dict[str, Any]]:
    """Normalize crm_deal_context tool result into a flat list of deal dicts."""
    if isinstance(raw, dict):
        raw = raw.get("deals") or (raw.get("data") or {}).get("deals", [])
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, dict)]


def _score_authority(purpose: str, identity_role: str | None) -> float:
    if identity_role:
        return 0.90
    return {
        "identity":               0.90,
        "audited_finance_doc":    0.85,
        "weekly_finance_checkin": 0.75,
        "example_material":       0.70,
        "reference":              0.60,
    }.get(purpose, 0.50)


def _deduplicate_documents(documents: List[ContextDocument]) -> List[ContextDocument]:
    """Remove near-duplicate documents using token-level Jaccard similarity (threshold 0.70)."""
    kept: List[ContextDocument] = []
    seen_token_sets: List[set] = []
    for doc in documents:
        tokens = set(doc.content.lower().split())
        if not tokens:
            kept.append(doc)
            continue
        duplicate = False
        for existing_tokens in seen_token_sets:
            intersection = tokens & existing_tokens
            union = tokens | existing_tokens
            if union and len(intersection) / len(union) >= 0.70:
                duplicate = True
                break
        if not duplicate:
            kept.append(doc)
            seen_token_sets.append(tokens)
    return kept


def _normalize_history(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("history", [])
    if not isinstance(raw, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "id": item.get("id") or item.get("interaction_id"),
                "query": item.get("query"),
                "response": _extract_response_summary(item.get("response")),
                "timestamp": item.get("timestamp"),
                "intent": item.get("intent"),
                "status": item.get("status"),
            }
        )
    return normalized


def _normalize_signals(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("signals", [])
    if not isinstance(raw, list):
        return []
    extracted = normalize_signals(raw)
    return [s.model_dump(exclude={"raw"}) for s in extracted]


def _normalize_event_payload(raw: Any, attachments: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    if attachments:
        payload.setdefault("attachments", attachments)
    if "upcoming_events" in payload:
        payload["upcoming_events"] = _normalize_calendar_events(payload.get("upcoming_events"))
    if "live_events" in payload:
        payload["live_events"] = _normalize_calendar_events(payload.get("live_events"))
    return payload


def _normalize_request_plan(raw: Any) -> RequestPlan | None:
    if isinstance(raw, RequestPlan):
        return raw
    if isinstance(raw, dict):
        try:
            return RequestPlan.model_validate(raw)
        except Exception:
            return None
    return None


def _normalize_memories(raw: Any) -> List[Dict[str, Any]]:
    """Normalize memory_management tool result into a flat list of memory dicts."""
    if isinstance(raw, dict):
        # Tool result shape: {memories: [...]} or {data: {memories: [...]}}
        raw = raw.get("memories") or (raw.get("data") or {}).get("memories", [])
    if not isinstance(raw, list):
        return []
    return [m for m in raw if isinstance(m, dict)]


def _extract_live_threads(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract threads list from read_email_threads tool result stored in context."""
    raw = context.get("live_threads", {})
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        # Tool result shape: {data: {threads: [...]}} or {threads: [...]}
        threads = raw.get("threads") or (raw.get("data") or {}).get("threads", [])
        return threads if isinstance(threads, list) else []
    return []


def _extract_live_events(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract events list from read_calendar_events tool result stored in context."""
    raw = context.get("live_events", {})
    if isinstance(raw, list):
        return _normalize_calendar_events(raw)
    if isinstance(raw, dict):
        events = raw.get("events") or (raw.get("data") or {}).get("events", [])
        return _normalize_calendar_events(events)
    return []


def _normalize_calendar_events(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("events") or (raw.get("data") or {}).get("events", [])
    if not isinstance(raw, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for event in raw:
        if not isinstance(event, dict):
            continue
        normalized.append(
            {
                **event,
                "starts_at": event.get("starts_at") or event.get("start_time") or (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date"),
                "ends_at": event.get("ends_at") or event.get("end_time") or (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date"),
            }
        )
    return normalized


_FINANCE_SOURCE_TYPES = frozenset({"audited_finance_doc", "weekly_finance_checkin"})


def _build_finance_context(
    *,
    company_state: Dict[str, Any],
    retrieved_documents: List[ContextDocument],
    signals: List[Dict[str, Any]],
    session_history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    # Current metrics: read from structured company_state fields — no scanning.
    current_metrics: List[Dict[str, Any]] = []
    for category_name, values in (
        ("revenue", company_state.get("revenue_segmentation", {})),
        ("cost", company_state.get("cost_structure", {})),
        ("capital", company_state.get("capital_position", {})),
    ):
        if not isinstance(values, dict):
            continue
        for metric_name, metric_value in list(values.items())[:5]:
            if isinstance(metric_value, (int, float)):
                current_metrics.append({"name": metric_name, "category": category_name, "value": metric_value})

    # Finance documents: filter by source_type set at ingest — never scan titles.
    finance_docs = [doc for doc in retrieved_documents if doc.source_type in _FINANCE_SOURCE_TYPES]
    board_materials = [doc.title for doc in finance_docs[:5]]

    # Finance signals: filter by domains field set by signal_extractor — never scan text.
    variance_signals = [
        s for s in signals
        if "finance" in (s.get("domains") or []) or s.get("source") == "finance"
    ]

    return {
        "current_metrics": current_metrics[:8],
        "board_materials": board_materials,
        "variance_signals": variance_signals[:4],
    }


def _build_quantitative_evidence_bundle(
    *,
    company_state: Dict[str, Any],
    retrieved_documents: List[ContextDocument],
    finance_context: Dict[str, Any],
) -> QuantitativeEvidenceBundle:
    numeric_series: list[dict[str, Any]] = []

    for metric in finance_context.get("current_metrics") or []:
        if not isinstance(metric, dict):
            continue
        value = metric.get("value")
        name = str(metric.get("name") or metric.get("metric") or "").strip()
        category = str(metric.get("category") or "").strip()
        if not name or not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        numeric_series.append(
            {
                "metric": name,
                "category": category or "finance",
                "value": value,
                "source_ref": str(metric.get("source_ref") or name).strip(),
                "source_type": "company_state",
            }
        )

    numeric_series.extend(_collect_numeric_state_rows(company_state))
    numeric_series = _dedupe_quantitative_series(numeric_series)

    available_fields = _quantitative_available_fields(numeric_series)
    dimensions = _quantitative_dimensions(numeric_series)
    time_periods = _quantitative_time_periods(numeric_series)
    source_refs = _quantitative_source_refs(numeric_series, retrieved_documents, finance_context)

    return QuantitativeEvidenceBundle(
        numeric_series=numeric_series[:20],
        dimensions=dimensions,
        time_periods=time_periods,
        comparisons=[],
        available_fields=available_fields,
        source_refs=source_refs,
    )


def _collect_numeric_state_rows(state: Dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(prefix: list[str], value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                walk(prefix + [str(key)], nested)
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        path = ".".join(part for part in prefix if part).strip(".")
        if not path:
            return
        top_level = prefix[0] if prefix else "company_state"
        rows.append(
            {
                "metric": prefix[-1] if prefix else path,
                "category": top_level,
                "value": value,
                "source_ref": path,
                "source_type": "company_state",
            }
        )

    for key, value in (state or {}).items():
        walk([str(key)], value)
    return rows


def _dedupe_quantitative_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        metric = str(row.get("metric") or "").strip().lower()
        category = str(row.get("category") or "").strip().lower()
        source_ref = str(row.get("source_ref") or "").strip().lower()
        value = str(row.get("value") or "").strip()
        key = (metric, category, source_ref, value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _quantitative_available_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for row in rows:
        for key, value in row.items():
            if key in fields:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                fields.append(key)
    priority = ["actual", "budget", "forecast", "variance", "value", "amount", "count", "total", "delta", "plan", "target"]
    ordered: list[str] = []
    lowered = {field.lower(): field for field in fields}
    for field in priority:
        actual = lowered.get(field)
        if actual and actual not in ordered:
            ordered.append(actual)
    for field in fields:
        if field not in ordered:
            ordered.append(field)
    return ordered


def _quantitative_dimensions(rows: list[dict[str, Any]]) -> list[str]:
    candidates = ["metric", "category", "period", "time_period", "dimension", "name"]
    dimensions: list[str] = []
    for key in candidates:
        if any(key in row and str(row.get(key) or "").strip() for row in rows):
            dimensions.append(key)
    return dimensions


def _quantitative_time_periods(rows: list[dict[str, Any]]) -> list[str]:
    periods: list[str] = []
    for key in ("period", "time_period", "date"):
        for row in rows:
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text and text not in periods:
                periods.append(text)
    return periods


def _quantitative_source_refs(
    rows: list[dict[str, Any]],
    retrieved_documents: List[ContextDocument],
    finance_context: Dict[str, Any],
) -> list[str]:
    refs: list[str] = []
    for row in rows:
        ref = str(row.get("source_ref") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    for title in finance_context.get("board_materials") or []:
        ref = str(title).strip()
        if ref and ref not in refs:
            refs.append(ref)
    for document in retrieved_documents:
        if document.source_type not in _FINANCE_SOURCE_TYPES:
            continue
        ref = str(document.document_id or document.title or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    return refs[:10]

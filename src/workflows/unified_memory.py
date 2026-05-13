from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkingMemory(BaseModel):
    primary_intent: str = "report"
    mode: str = "new_request"
    execution_mode: str = "analysis"
    workflow_preference: Optional[str] = None
    task_topic: Optional[str] = None
    rationale: str = ""
    deliverable: Dict[str, Any] = Field(default_factory=dict)
    timeframe: Optional[str] = None
    deadline: Optional[str] = None
    entities: List[str] = Field(default_factory=list)
    requested_actions: List[str] = Field(default_factory=list)
    must_do: List[str] = Field(default_factory=list)
    must_not_do: List[str] = Field(default_factory=list)
    rejected_offer_classes: List[str] = Field(default_factory=list)
    last_user_message: Optional[str] = None
    resolved_action_reference: Dict[str, Any] = Field(default_factory=dict)


class SessionMemory(BaseModel):
    conversation_id: Optional[str] = None
    turn_count: int = 0
    current_schedule: Dict[str, Any] = Field(default_factory=dict)
    open_decisions: List[str] = Field(default_factory=list)
    open_commitments: List[str] = Field(default_factory=list)
    pending_actions: List[Dict[str, Any]] = Field(default_factory=list)
    entities_in_play: Dict[str, Any] = Field(default_factory=dict)
    last_agent_contributions: List[Dict[str, Any]] = Field(default_factory=list)
    recent_history: List[Dict[str, str]] = Field(default_factory=list)
    recent_artifacts: List[str] = Field(default_factory=list)
    previous_workflow_type: Optional[str] = None
    previous_response_title: Optional[str] = None
    previous_response_summary: Optional[str] = None
    situational_profile: Dict[str, Any] = Field(default_factory=dict)


class LongTermMemory(BaseModel):
    preferences: Dict[str, Any] = Field(default_factory=dict)
    ceo_memories: List[Dict[str, Any]] = Field(default_factory=list)


class RetrievalEvidence(BaseModel):
    project_context: Dict[str, Any] = Field(default_factory=dict)
    signals: List[Dict[str, Any]] = Field(default_factory=list)
    retrieved_documents: List[Dict[str, Any]] = Field(default_factory=list)
    retrieval_manifest: Dict[str, Any] = Field(default_factory=dict)
    entity_context: List[Dict[str, Any]] = Field(default_factory=list)


class UnifiedMemoryState(BaseModel):
    version: int = 1
    working_memory: WorkingMemory = Field(default_factory=WorkingMemory)
    session_memory: SessionMemory = Field(default_factory=SessionMemory)
    long_term_memory: LongTermMemory = Field(default_factory=LongTermMemory)
    retrieval_evidence: RetrievalEvidence = Field(default_factory=RetrievalEvidence)

    def prompt_payload(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


def unified_memory_from_payload(data: Dict[str, Any] | None) -> UnifiedMemoryState | None:
    if not isinstance(data, dict) or not data:
        return None
    try:
        return UnifiedMemoryState(**data)
    except Exception:
        return None


def build_unified_memory_state(
    *,
    resolved_intent: Dict[str, Any],
    conversation_id: Optional[str],
    live_context: Dict[str, Any] | None,
    preferences: Dict[str, Any] | None,
    situational_profile: Dict[str, Any] | None,
    ceo_memories: List[Dict[str, Any]] | None,
    recent_history: List[Dict[str, str]] | None,
    artifact_context: Dict[str, Any] | None,
    project_context: Dict[str, Any] | None = None,
    signals: List[Dict[str, Any]] | None = None,
    retrieved_documents: List[Dict[str, Any]] | None = None,
    retrieval_manifest: Dict[str, Any] | None = None,
    entity_context: List[Dict[str, Any]] | None = None,
    resolved_action_reference: Dict[str, Any] | None = None,
) -> UnifiedMemoryState:
    live_context = live_context or {}
    artifact_context = artifact_context or {}
    deliverable = dict(resolved_intent.get("deliverable") or {})
    working_memory = WorkingMemory(
        primary_intent=str(resolved_intent.get("primary_intent") or "report"),
        mode=str(resolved_intent.get("mode") or "new_request"),
        execution_mode=str(resolved_intent.get("execution_mode") or "analysis"),
        workflow_preference=resolved_intent.get("workflow_preference"),
        task_topic=resolved_intent.get("task_topic"),
        rationale=str(resolved_intent.get("rationale") or ""),
        deliverable=deliverable,
        timeframe=resolved_intent.get("timeframe"),
        deadline=resolved_intent.get("deadline"),
        entities=[str(item) for item in (resolved_intent.get("entities") or []) if str(item)],
        requested_actions=[str(item) for item in (resolved_intent.get("requested_actions") or []) if str(item)],
        must_do=[str(item) for item in (resolved_intent.get("must_do") or []) if str(item)],
        must_not_do=[str(item) for item in (resolved_intent.get("must_not_do") or []) if str(item)],
        rejected_offer_classes=[str(item) for item in (resolved_intent.get("rejected_offer_classes") or []) if str(item)],
        last_user_message=resolved_intent.get("last_user_message"),
        resolved_action_reference=dict(resolved_action_reference or {}),
    )
    session_memory = SessionMemory(
        conversation_id=conversation_id,
        turn_count=int(live_context.get("turn_count") or 0),
        current_schedule=dict(live_context.get("current_schedule") or {}),
        open_decisions=[str(item) for item in (live_context.get("open_decisions") or []) if str(item)],
        open_commitments=[str(item) for item in (live_context.get("open_commitments") or []) if str(item)],
        pending_actions=[item for item in (live_context.get("pending_actions") or []) if isinstance(item, dict)],
        entities_in_play=dict(live_context.get("entities_in_play") or {}),
        last_agent_contributions=[item for item in (live_context.get("last_agent_contributions") or []) if isinstance(item, dict)],
        recent_history=[item for item in (recent_history or []) if isinstance(item, dict)],
        recent_artifacts=[str(item) for item in (artifact_context.get("previous_artifacts") or []) if str(item)],
        previous_workflow_type=artifact_context.get("previous_workflow_type"),
        previous_response_title=artifact_context.get("previous_title"),
        previous_response_summary=artifact_context.get("previous_summary"),
        situational_profile=dict(situational_profile or {}),
    )
    long_term_memory = LongTermMemory(
        preferences=dict(preferences or {}),
        ceo_memories=[item for item in (ceo_memories or []) if isinstance(item, dict)],
    )
    retrieval_evidence = RetrievalEvidence(
        project_context=dict(project_context or {}),
        signals=[item for item in (signals or []) if isinstance(item, dict)],
        retrieved_documents=[item for item in (retrieved_documents or []) if isinstance(item, dict)],
        retrieval_manifest=dict(retrieval_manifest or {}),
        entity_context=[item for item in (entity_context or []) if isinstance(item, dict)],
    )
    return UnifiedMemoryState(
        working_memory=working_memory,
        session_memory=session_memory,
        long_term_memory=long_term_memory,
        retrieval_evidence=retrieval_evidence,
    )

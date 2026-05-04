import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from src.core.llm import DEFAULT_ANTHROPIC_MODEL
from src.tools.registry import ToolRegistry
from src.workflows.action_semantics import classify_action_semantics
from src.workflows.context_loading import (
    DOCUMENT_EXPLANATION_CONTEXT_STAGES,
    build_document_explanation_context_actions,
    prepare_document_explanation_context,
)

from .base import BaseAgent
from .schemas import (
    AgentInput,
    AgentMetadata,
    AgentOutput,
    complete_stage_action,
    complete_workflow_action,
    gate_action,
    tool_action,
    write_artifact_action,
)


class ExplanationSection(BaseModel):
    label: str
    content: Optional[str] = None
    items: List[str] = Field(default_factory=list)


class ExplanationAnswer(BaseModel):
    title: str
    summary: str
    sections: List[ExplanationSection] = Field(default_factory=list)


class ExplanationTrust(BaseModel):
    confidence: str
    confidence_score: float
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    data_quality: str
    calculation_used: bool = False
    missing_context: List[str] = Field(default_factory=list)


class ExplanationPayload(BaseModel):
    answer: ExplanationAnswer
    trust: ExplanationTrust
    sources: List[Dict[str, Any]] = Field(default_factory=list)


class ExplainerAgent(BaseAgent):
    COMPLETION_MODEL = os.getenv("EXPLAINER_AGENT_MODEL", DEFAULT_ANTHROPIC_MODEL)

    metadata = AgentMetadata(
        name="explainer_agent",
        description="Explains business implications of uploaded or indexed documents.",
        stage="synthesizer",
        allowed_tools=[
            "get_company_state",
            "get_preferences",
            "semantic_search",
            "structured_completion",
            "write_artifact",
        ],
        tags=["explanation", "documents", "implications"],
    )

    def __init__(self, tools: ToolRegistry):
        self.tools = tools

    async def run(self, agent_input: AgentInput, **kwargs: Any) -> AgentOutput:
        task_input = kwargs.get("task_input") or agent_input.task_input or ""
        attachments = agent_input.metadata.get("attachments", [])
        context = agent_input.context or {}

        missing_context_actions = build_document_explanation_context_actions(task_input, context)
        if missing_context_actions:
            return AgentOutput(
                agent_name=self.metadata.name,
                stage=agent_input.stage,
                success=True,
                summary="Requesting explanation context.",
                actions=missing_context_actions,
                metadata={
                    "workflow_type": "document_explanation",
                    "response_type": "explanation",
                    "phase": "context",
                    "context_stages": DOCUMENT_EXPLANATION_CONTEXT_STAGES,
                },
            )

        prepared_context = prepare_document_explanation_context(context, attachments)
        retrieval = prepared_context.prompt_payload()["retrieved_documents"]
        company_state = prepared_context.company_state
        preferences = prepared_context.preferences
        project_context = prepared_context.project_context

        if "explanation_completion" not in context:
            return AgentOutput(
                agent_name=self.metadata.name,
                stage=agent_input.stage,
                success=True,
                summary="Requesting structured explanation completion.",
                actions=[
                    tool_action(
                        "structured_completion",
                        result_key="explanation_completion",
                        prompt=self._explanation_prompt(
                            task_input,
                            retrieval,
                            attachments,
                            company_state,
                            preferences,
                            project_context,
                        ),
                        system_prompt=(
                            "You are the ExplainerAgent for a CEO. Explain business implications clearly and precisely. "
                            "Focus on what changed, why it matters, what risks exist, and what should happen next."
                        ),
                        response_model=ExplanationPayload,
                        model=self.COMPLETION_MODEL,
                    )
                ],
                metadata={
                    "workflow_type": "document_explanation",
                    "response_type": "explanation",
                    "phase": "completion",
                    "context_stages": DOCUMENT_EXPLANATION_CONTEXT_STAGES,
                },
            )

        payload = self._generate_explanation_payload(task_input, retrieval, attachments, context.get("explanation_completion"))
        markdown = self._to_markdown(payload)
        if self._needs_human_approval(task_input) and not self._approval_granted(agent_input):
            return AgentOutput(
                agent_name=self.metadata.name,
                stage=agent_input.stage,
                success=True,
                summary="Human approval required before finalizing this explanation.",
                content=markdown,
                structured_output=payload.model_dump(),
                actions=[
                    gate_action(
                        "HUMAN_APPROVAL",
                        reason="This request appears to involve external sharing, legal review, or executive sign-off.",
                        preview_title=payload.answer.title,
                    )
                ],
                metadata={
                    "workflow_type": "document_explanation",
                    "response_type": "explanation",
                    "phase": "approval",
                    "presentation": {"mode": "decision", "variant": "approval"},
                },
            )

        return AgentOutput(
            agent_name=self.metadata.name,
            stage=agent_input.stage,
            success=True,
            summary=payload.answer.summary,
            content=markdown,
            structured_output=payload.model_dump(),
            actions=[
                write_artifact_action("synthesizer", "executive_summary.md", markdown, source="explainer_agent", hidden=True),
                complete_stage_action(agent_input.stage),
                complete_workflow_action(response_type="explanation"),
            ],
            metadata={
                "workflow_type": "document_explanation",
                "response_type": "explanation",
                "presentation": self._build_presentation(payload),
            },
        )

    def _generate_explanation_payload(
        self,
        task_input: str,
        retrieval: List[Dict[str, Any]],
        attachments: List[Dict[str, Any]],
        completion: Optional[Dict[str, Any]],
    ) -> ExplanationPayload:
        if completion:
            try:
                return ExplanationPayload(**completion)
            except Exception:
                pass
        return self._fallback_payload(task_input, retrieval, attachments)

    def _explanation_prompt(
        self,
        task_input: str,
        retrieval: List[Dict[str, Any]],
        attachments: List[Dict[str, Any]],
        company_state: Dict[str, Any],
        preferences: Dict[str, Any],
        project_context: Dict[str, Any],
    ) -> str:
        sorted_retrieval = sorted(retrieval, key=lambda d: float(d.get("source_authority", 0.5)), reverse=True)
        doc_blocks = self._build_source_citation_blocks(sorted_retrieval)
        confidence_warning = self._low_confidence_warning(sorted_retrieval)

        return (
            f"CEO request: {task_input}\n\n"
            f"Referenced attachments: {attachments}\n\n"
            f"Company state: {company_state}\n\n"
            f"CEO preferences: {preferences}\n\n"
            f"Active project context: {project_context}\n\n"
            f"=== RETRIEVED DOCUMENTS (ranked by authority) ===\n{doc_blocks}\n\n"
            f"{confidence_warning}"
            "=== SYNTHESIS DISCIPLINE ===\n"
            "- Lead every claim with the most authoritative source that supports it.\n"
            "- Cite the document title in brackets (e.g., [Source: Contract Amendment Q1]) for each key assertion.\n"
            "- If two documents conflict, flag the conflict explicitly in the relevant section.\n"
            "- Do not introduce claims that cannot be attributed to at least one source above.\n"
            "- For each source used, include it in the 'sources' list with its source_id, title, authority_level, and the specific claim it supports.\n\n"
            "Return an ExplanationPayload JSON object with:\n"
            "- answer.title\n"
            "- answer.summary\n"
            "- answer.sections (What Changed, Why It Matters, Recommended CEO Questions)\n"
            "- trust (reflect retrieval confidence honestly — lower confidence_score if sources are weak or missing)\n"
            "- sources (include authority_level field per source: 'primary', 'secondary', or 'low')\n"
        )

    def _build_source_citation_blocks(self, retrieval: List[Dict[str, Any]]) -> str:
        if not retrieval:
            return "(no documents retrieved)"
        blocks: List[str] = []
        for idx, doc in enumerate(retrieval):
            authority = float(doc.get("source_authority", 0.5))
            if authority >= 0.85:
                authority_label = "PRIMARY (high confidence)"
            elif authority >= 0.65:
                authority_label = "SECONDARY (moderate confidence)"
            else:
                authority_label = "LOW AUTHORITY (treat as supporting only)"
            title = doc.get("title", f"Document {idx + 1}")
            source_type = doc.get("source_type", "reference")
            content = doc.get("content", "")
            snippet = content[:800].strip() if content else "(no content)"
            blocks.append(
                f"[{idx + 1}] {title} | Authority: {authority_label} | Type: {source_type}\n"
                f"{snippet}"
            )
        return "\n\n".join(blocks)

    def _low_confidence_warning(self, retrieval: List[Dict[str, Any]]) -> str:
        if not retrieval:
            return (
                "⚠ CONFIDENCE WARNING: No documents were retrieved for this query. "
                "The explanation will rely entirely on company state and CEO context — "
                "flag this in trust.missing_context and set a low confidence_score.\n\n"
            )
        high_authority = [d for d in retrieval if float(d.get("source_authority", 0.5)) >= 0.65]
        if not high_authority:
            return (
                "⚠ CONFIDENCE WARNING: All retrieved documents have low authority scores. "
                "Treat this explanation as provisional — surface uncertainty in trust.data_quality "
                "and set confidence_score ≤ 0.5.\n\n"
            )
        return ""

    def _fallback_payload(
        self,
        task_input: str,
        retrieval: List[Dict[str, Any]],
        attachments: List[Dict[str, Any]],
    ) -> ExplanationPayload:
        anchor = attachments[0]["filename"] if attachments else (retrieval[0].get("title") if retrieval else "the supplied material")
        return ExplanationPayload(
            answer=ExplanationAnswer(
                title=f"Business Implication Brief: {anchor}",
                summary=f"{task_input} in the context of {anchor}.",
                sections=[
                    ExplanationSection(
                        label="What Changed",
                        content=f"The most relevant material appears to be {anchor}.",
                    ),
                    ExplanationSection(
                        label="Why It Matters",
                        content="This document likely affects current decisions, risk posture, or operating assumptions.",
                    ),
                    ExplanationSection(
                        label="Recommended CEO Questions",
                        items=[
                            "What decision does this force or constrain?",
                            "Which stakeholders need alignment before acting?",
                            "Is any supporting financial or legal context missing?",
                        ],
                    ),
                ],
            ),
            trust=ExplanationTrust(
                confidence="medium",
                confidence_score=0.66,
                assumptions=["The retrieved documents are the most relevant context currently indexed."],
                open_questions=["Should the system focus on legal, financial, or strategic implications next?"],
                data_quality="medium",
                calculation_used=False,
                missing_context=[],
            ),
            sources=[
                *[
                    {
                        "source_id": attachment.get("document_id", f"attachment_{idx}"),
                        "title": attachment.get("filename", f"Attachment {idx + 1}"),
                        "type": "document",
                    }
                    for idx, attachment in enumerate(attachments[:2])
                ],
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

    def _section_to_presentation(self, section: ExplanationSection) -> Dict[str, Any]:
        return {"title": section.label, "content": section.content, "items": section.items}

    def _build_presentation(self, payload: ExplanationPayload) -> Dict[str, Any]:
        sections = {s.label: s for s in payload.answer.sections}
        priorities = sections.get("What Changed")
        risks = sections.get("Why It Matters")
        actions = sections.get("Recommended CEO Questions")
        return {
            "mode": "report",
            "variant": "document",
            "summary": payload.answer.summary,
            "priorities": [self._section_to_presentation(priorities)] if priorities else [],
            "risks": [self._section_to_presentation(risks)] if risks else [],
            "recommended_actions": [self._section_to_presentation(actions)] if actions else [],
            "details": [],
        }

    def _to_markdown(self, payload: ExplanationPayload) -> str:
        lines = [f"# {payload.answer.title}", "", payload.answer.summary]
        for section in payload.answer.sections:
            lines.extend(["", f"## {section.label}"])
            if section.content:
                lines.extend(["", section.content])
            if section.items:
                lines.extend(["", *[f"- {item}" for item in section.items]])
        return "\n".join(lines).strip()

    def _approval_granted(self, agent_input: AgentInput) -> bool:
        approvals = agent_input.workflow_state.metadata.get("approvals", {})
        stage_approval = approvals.get(agent_input.stage, {})
        return stage_approval.get("decision") == "approve"

    def _needs_human_approval(self, task_input: str) -> bool:
        return classify_action_semantics(message=task_input).external_delivery_requested

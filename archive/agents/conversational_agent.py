"""
ConversationalAgent — plain conversational responses for the CEO.

Used as the default when the request doesn't warrant a structured artifact.
Answers naturally using conversation history and available company context.
Offers to pull real data if the question would benefit from it.
"""
from __future__ import annotations

import os
from typing import Any

from src.agents.base import BaseAgent
from src.agents.schemas import (
    AgentInput,
    AgentMetadata,
    AgentOutput,
    complete_stage_action,
    complete_workflow_action,
    write_artifact_action,
)
from src.core.llm import DEFAULT_ANTHROPIC_MODEL


_SYSTEM_PROMPT = """\
You are a trusted executive AI assistant. Answer conversationally and precisely.

Rules:
- Be direct. Plain prose. Bullets only when they genuinely help.
- If you don't know something specific (figures, names, dates), say so — do not invent details.
- If the question would benefit from pulling real data (email threads, calendar, financials), \
offer that at the end: e.g. "Want me to pull the actual numbers on this?"
- Never produce a formal report structure (no "Executive Summary" headers, no trust metadata blocks) \
unless the CEO explicitly asked for one.
- Keep answers tight — a paragraph or two is usually right.
"""


class ConversationalAgent(BaseAgent):
    COMPLETION_MODEL = os.getenv("CONVERSATIONAL_AGENT_MODEL", DEFAULT_ANTHROPIC_MODEL)

    metadata = AgentMetadata(
        name="conversational_agent",
        description="Plain conversational responses for the CEO. Default when no artifact is needed.",
        stage="synthesizer",
        allowed_tools=[],
        tags=["conversational", "default"],
    )

    async def run(self, agent_input: AgentInput, **kwargs: Any) -> AgentOutput:
        task_input = kwargs.get("task_input") or agent_input.task_input or ""
        workflow_state = agent_input.workflow_state
        company_name = workflow_state.company_name or "your company"
        ceo_id = workflow_state.ceo_id
        conversation_id = agent_input.metadata.get("conversation_id")

        conversation_history = self._load_history(ceo_id, conversation_id, agent_input)
        prompt = self._build_prompt(task_input, company_name, conversation_history)

        try:
            from src.core.llm import LLMClient
            answer = LLMClient(model=self.COMPLETION_MODEL).complete(prompt, _SYSTEM_PROMPT)
        except Exception as e:
            return AgentOutput(
                agent_name=self.metadata.name,
                stage=agent_input.stage,
                success=False,
                error=f"LLM call failed: {e}",
            )

        structured = {
            "answer": {
                "title": "",
                "summary": answer,
                "sections": [],
            },
            "trust": {
                "confidence": "high",
                "confidence_score": 0.85,
                "assumptions": [],
                "open_questions": [],
                "data_quality": "conversational",
                "calculation_used": False,
            },
        }

        return AgentOutput(
            agent_name=self.metadata.name,
            stage=agent_input.stage,
            success=True,
            summary=answer,
            content=answer,
            structured_output=structured,
            actions=[
                write_artifact_action(
                    "synthesizer",
                    "executive_summary.md",
                    answer,
                    source="conversational_agent",
                    hidden=True,
                ),
                complete_stage_action(agent_input.stage),
                complete_workflow_action(response_type="conversational"),
            ],
            metadata={
                "workflow_type": "conversational",
                "response_type": "conversational",
                "presentation": {"mode": "conversational", "variant": "plain"},
            },
        )

    def _load_history(
        self,
        ceo_id: str,
        conversation_id: str | None,
        agent_input: AgentInput,
    ) -> list[dict[str, Any]]:
        """Load recent conversation turns. Falls back gracefully if unavailable."""
        # First check if history was passed in metadata (preferred path)
        history = agent_input.metadata.get("conversation_history")
        if history and isinstance(history, list):
            return history[-8:]

        # Fallback: load from DB
        if not conversation_id:
            return []
        try:
            from src.assistant.memory import build_conversation_history
            return build_conversation_history(
                ceo_id=ceo_id,
                conversation_id=conversation_id,
                current_interaction_id=agent_input.workflow_state.interaction_id,
            )[-8:]
        except Exception:
            return []

    def _build_prompt(
        self,
        task_input: str,
        company_name: str,
        history: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = [f"Company: {company_name}\n"]

        if history:
            lines.append("Recent conversation:")
            for turn in history:
                role = turn.get("role", "user")
                content = str(turn.get("content", ""))[:400]
                lines.append(f"  {role}: {content}")
            lines.append("")

        lines.append(f"CEO: {task_input}")
        return "\n".join(lines)

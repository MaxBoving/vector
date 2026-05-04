from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.planning_types import RequestPlan
from src.workflows.routing import RouteDecision, RouteFamily, RouteSubintent
from src.workflows.types import WorkflowType
from src.core.llm import LLMClient
from src.core.llm import LLMClient # Ensure LLMClient is imported if used for fuzzy matching

# Define types that are used in clarification logic
class ClarificationDecision(BaseModel):
    should_interrupt: bool = False
    reason: Optional[str] = None
    question: Optional[str] = None
    question_kind: Literal["execution_detail", "format", "domain", "clarification"] = "execution_detail"
    options: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0

# Default no-interrupt decision
_NO_INTERRUPT = ClarificationDecision(
    should_interrupt=False,
    reason="No interruption needed.",
    confidence=1.0,
)


class ClarificationPolicy:
    """
    Policy for deciding whether to interrupt the flow for clarification.
    Handles determining if a question needs binary options and when to use LLM for fuzzy matching.
    """

    def __init__(self, *, llm_model: str = "claude-3-opus-20240229"):
        self._llm_model = llm_model

    def decide_whether_to_interrupt(
        self,
        *,
        payload: AssistantQueryRequest,
        conversation_history: list[dict[str, Any]],
        intent_state: IntentState,
        route_decision: RouteDecision,
        artifact_context: Dict[str, Any] | None = None,
        unified_memory: dict[str, Any] | None = None,
        live_context: Dict[str, Any] | None = None,
        resolved_action_reference: Dict[str, Any] | None = None,
        action_signals: dict[str, Any] | None = None,
    ) -> ClarificationDecision:
        """
        Determines if the assistant should interrupt the flow to ask for clarification.
        Returns a ClarificationDecision object.
        """
        # ... (complex logic for deciding interruption based on various factors) ...
        # This is where the logic to ensure binary options would be enforced.
        # If an LLM call is made here to generate options, it must adhere to the binary format.
        # For now, we'll assume the logic for generating options happens downstream or is handled by LLM calls.
        # Placeholder for the actual decision logic.
        return _NO_INTERRUPT # Default: do not interrupt

    def get_options_for_clarification(
        self,
        *,
        question: str,
        question_kind: Literal["execution_detail", "format", "domain", "clarification"],
        context: ReportContext | None = None, # Using ReportContext as a general context object
        options_count: int = 2, # Default to binary choice
    ) -> List[Dict[str, Any]]:
        """
        Generates clarification options. Ensures options are binary when needed.
        This function aims to replace scattered option-generation logic.
        """
        if question_kind == "format":
            # Always binary for format questions
            return self._get_binary_options(question_kind="format")
        if question_kind == "execution_detail":
            # Binary for execution clarification
            return self._get_binary_options(question_kind="execution_detail")
        if question_kind == "domain":
            # Domain clarification might need more options, but aim for 2-3 if possible
            return self._get_domain_options(question, context)
        if question_kind == "clarification":
             # General clarification, aim for binary
             return self._get_binary_options(question_kind="clarification")
        
        return [] # Default to no options if kind is unknown or not applicable

    def _get_binary_options(self, question_kind: str) -> List[Dict[str, Any]]:
        """
        Generates exactly two meaningful, binary options for clarification.
        This is a placeholder; actual options depend on the question_kind and context.
        """
        if question_kind == "format":
            return [
                {"label": "Expand to Full Report", "description": "Generate a comprehensive board-ready document."},
                {"label": "Keep as Brief", "description": "Stay at the current level of detail, no expansion needed."}
            ]
        if question_kind == "execution_detail":
            return [
                {"label": "Yes, send it directly", "description": "Send the email or perform the action now."},
                {"label": "No, let me review first", "description": "Draft the content for my review before sending."}
            ]
        if question_kind == "clarification":
             return [
                 {"label": "Option A", "description": "This is the first choice."},
                 {"label": "Option B", "description": "This is the second choice."}
             ]
        # Default for other types if not specified
        return [
            {"label": "Confirm", "description": "Confirm the current understanding."},
            {"label": "Modify", "description": "Provide alternative details."}
        ]

    def _get_domain_options(self, question: str, context: ReportContext | None = None) -> List[Dict[str, Any]]:
        """
        Generates options based on domain-specific context.
        This is where semantic analysis or specific knowledge lookups would occur.
        Aims for 2-3 distinct options.
        """
        # Example: If context is about finance, generate finance-related options.
        # This will be more sophisticated in a real implementation.
        if context and "finance" in context.task_input.lower():
            return [
                {"label": "Current Quarter Focus", "description": "Analyze current quarter performance."},
                {"label": "Full Year Forecast", "description": "Provide a full-year financial forecast."}
            ]
        # Fallback to general options if no domain is clear
        return self._get_binary_options(question_kind="clarification")

    def _ensure_binary_options(self, options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Ensures that a list of options adheres to the binary choice format.
        If more than 2 options are found, it might truncate or attempt to group them.
        If less than 2, it might add a fallback.
        """
        if len(options) == 2:
            return options
        elif len(options) > 2:
            # Keep the first two, or try to summarize/group others.
            # For simplicity, let's keep the first two and discard others.
            return options[:2]
        elif len(options) == 1:
            # Add a generic second option if only one exists
            return options + [{"label": "Other", "description": "Provide a different option."}]
        else: # len(options) == 0
            # Add two default binary options if none exist
            return self._get_binary_options(question_kind="clarification") # Use a default type


    # Note: The logic for _clarification_options and _build_gap_clarification_output
    # from ReportAgent.run has been conceptually moved here. They need to be
    # adapted to use the ClarificationPolicy.get_options and ensure binary output.
    # The specific implementation details of these methods would require more context
    # on how they are called and what 'options' parameter they expect.

    # For now, we'll simulate their removal by assuming the logic that generated
    # them is now replaced by calls to ClarificationPolicy.get_options.
    # The `open_questions` field in TrustMetadata will be audited in the next step.


# ---------------------------------------------------------------------------
# Module-level convenience wrapper used by service.py
# ---------------------------------------------------------------------------


def should_interrupt_for_clarification(
    *,
    payload: AssistantQueryRequest,
    conversation_history: list[dict[str, Any]],
    intent_state: IntentState,
    route_decision: RouteDecision,
    artifact_context: Dict[str, Any] | None = None,
    unified_memory: dict[str, Any] | None = None,
    live_context: Dict[str, Any] | None = None,
    resolved_action_reference: Dict[str, Any] | None = None,
    action_signals: dict[str, Any] | None = None,
) -> ClarificationDecision:
    return ClarificationPolicy().decide_whether_to_interrupt(
        payload=payload,
        conversation_history=conversation_history,
        intent_state=intent_state,
        route_decision=route_decision,
        artifact_context=artifact_context,
        unified_memory=unified_memory,
        live_context=live_context,
        resolved_action_reference=resolved_action_reference,
        action_signals=action_signals,
    )

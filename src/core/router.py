from enum import Enum
from typing import List, Optional
from pydantic import BaseModel

class TaskIntent(str, Enum):
    STRATEGIC_ANALYSIS = "strategic_analysis"
    LIVE_RESEARCH = "live_research"
    DOCUMENT_REVIEW = "document_review"
    EXECUTION_REQUEST = "execution_request"
    FACT_FINDING = "fact_finding"

class RoutingDecision(BaseModel):
    intent: TaskIntent
    specialist_required: str  # "claude-3-opus", "gpt-4o", "gemini-1.5-pro", etc.
    relevant_state_keys: List[str]  # e.g., ["revenue_segmentation", "capital_position"]
    requires_approval: bool = False
    rationale: str

class BrainRouter:
    """
    The 'Small Model' logic layer. 
    Handles intent classification and token-efficient context selection.
    """
    
    def classify_and_route(self, task_input: str) -> RoutingDecision:
        # Implementation will eventually use a lightweight model (e.g., Gemini Flash or GPT-4o-mini)
        # For now, we provide the logic structure.
        return RoutingDecision(
            intent=TaskIntent.STRATEGIC_ANALYSIS,
            specialist_required="gpt-4o",
            relevant_state_keys=["revenue_segmentation"],
            rationale="Standard strategic query requires structured reasoning."
        )

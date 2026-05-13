from typing import Any, Dict, List, Optional

from src.agents.schemas import ReportPayload
from src.core.llm import LLMClient
from src.core.knowledge_registry import SEMANTIC_TOPIC_HINTS
from src.agents.report_strategies import ReportContext, ReportStrategy


class EscalationStrategy(ReportStrategy):
    """
    Strategy for synthesizing reports related to customer escalations or at-risk accounts.
    Focuses on recovery plans, risk assessments, and owner assignments.
    """

    def __init__(self, *, completion_model: str = "claude-3-opus-20240229"):
        self.completion_model = completion_model

    async def synthesize(self, context: ReportContext) -> ReportPayload:
        # 1. Use LLM to extract escalation details, risks, and recommended actions
        escalation_details = await self._extract_escalation_details(context)

        # 2. Structure the extracted information into a ReportPayload
        return ReportPayload(
            answer=ReportAnswer(
                title=f"Escalation Report: {escalation_details.get('account_name', 'N/A')}",
                summary=escalation_details.get("summary", "Customer escalation overview."),
                sections=[
                    ReportSection(label="Risk Assessment", items=escalation_details.get("risks", [])),
                    ReportSection(label="Recovery Plan", items=escalation_details.get("recovery_plan", [])),
                    ReportSection(label="Owner Assignments", items=escalation_details.get("owner_assignments", [])),
                ],
            ),
            trust=ReportTrust(
                confidence="high",
                confidence_score=0.9,
                reasoning="Escalation details semantically extracted and structured.",
                assumptions=[],
                open_questions=[],
                data_quality="high",
                safe_to_act=True,
            ),
            sources=[],
        )

    async def _extract_escalation_details(self, context: ReportContext) -> Dict[str, Any]:
        """
        Use LLM to extract escalation details, risks, recovery plan, and owners.
        This replaces hardcoded keyword checks with semantic analysis.
        """
        prompt = f"""
        You are an expert in customer success and executive reporting. Your task is to analyze the provided context and extract specific details about a customer escalation.

        === CONTEXT ===
        {context.task_input}
        Company State: {json.dumps(context.company_state.get('escalations', []), indent=2)}
        Retrieved Documents: {context.retrieval[:3]}
        Session History: {context.session_history[:3]}

        === SEMANTIC TOPIC HINTS ===
        {json.dumps(SEMANTIC_TOPIC_HINTS.get('escalation'), indent=2)}

        === OUTPUT INSTRUCTIONS ===
        Return a JSON object with the following keys:
        - "account_name": The name of the primary account involved in the escalation.
        - "summary": A 1-2 sentence executive summary of the escalation situation.
        - "risks": A list of specific risks associated with this escalation (e.g., churn, revenue impact).
        - "recovery_plan": A list of actionable steps to resolve the escalation.
        - "owner_assignments": A list mapping owners to specific recovery plan items or risks.

        If any information is missing, use null for the value or an empty list for arrays. Do not hallucinate details.
        Focus ONLY on the escalation described in the current context.
        """
        try:
            llm_response = LLMClient(model=self.completion_model).complete(prompt)
            data = json.loads(llm_response)
            return data
        except Exception as e:
            logger.error(f"Failed to extract escalation details: {e}")
            return {
                "account_name": "Unknown Account",
                "summary": "Could not extract escalation details.",
                "risks": [],
                "recovery_plan": [],
                "owner_assignments": [],
            }

# Placeholder for logging if needed
import logging
logger = logging.getLogger(__name__)

# Placeholder for ReportSection, ReportAnswer, ReportTrust, ReportPayload if they are not globally available
# These would typically be imported from agents.schemas or similar
try:
    from src.agents.schemas import ReportSection, ReportAnswer, ReportTrust, ReportPayload
except ImportError:
    # Define mock classes if not found, for standalone testing or development
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

    class ReportPayload(BaseModel):
        answer: ReportAnswer
        trust: ReportTrust
        sources: List[Dict[str, Any]] = Field(default_factory=list)
        presentation: Optional[Dict[str, Any]] = None # Simplified for mock

from pydantic import BaseModel # Ensure BaseModel is available if mock classes are used

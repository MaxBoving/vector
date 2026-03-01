from typing import Any, Optional, Dict
from datetime import datetime
from .models import CompanyState, CEOPreferences
from .router import BrainRouter, RoutingDecision
from .database import get_ceo_preferences, get_company_state
from ..agents.specialists import SpecialistFactory

class ExecutiveBrain:
    """
    Centralized Executive Brain (Single Decision Authority).
    Orchestrates the 'Small Models for Small Choices' logic.
    """
    
    def __init__(self, ceo_id: str, company_name: str):
        self.ceo_id = ceo_id
        self.company_name = company_name
        self.router = BrainRouter()
        
        # Load from DB or use default
        self.preferences: CEOPreferences = get_ceo_preferences(ceo_id) or CEOPreferences(ceo_id=ceo_id)
        self.company_state: CompanyState = get_company_state(company_name) or CompanyState(
            company_name=company_name, 
            last_updated=datetime.now().isoformat()
        )

    def synthesize(self, task_input: str) -> str:
        """
        The core orchestration loop (Inbound Task).
        """
        # STEP 1: Intent & Routing (Small Model)
        decision: RoutingDecision = self.router.classify_and_route(task_input)
        
        # STEP 2: Context Filtering (Token Efficiency)
        relevant_context = self._get_filtered_state(decision.relevant_state_keys)
        
        # STEP 3: Specialist Execution (Multi-Model)
        raw_specialist_output = self._call_specialist(
            decision.specialist_required, 
            task_input, 
            relevant_context
        )
        
        # STEP 4: Refinement & Tone Adaptation
        final_synthesis = self._refine_for_executive(raw_specialist_output)
        
        return final_synthesis

    def _get_filtered_state(self, keys: list[str]) -> Dict[str, Any]:
        """
        Filters the CompanyState to minimize context window bloat.
        """
        if not self.company_state:
            return {}
        # Simple extraction of requested keys from state
        state_data = {}
        # If the key is in the model, extract it
        for k in keys:
            val = getattr(self.company_state, k, None)
            if val is not None:
                state_data[k] = val
        return state_data

    def _call_specialist(self, model: str, prompt: str, context: Dict[str, Any]) -> str:
        """
        Calls the specialized workers via the Factory.
        """
        specialist = SpecialistFactory.get_specialist(model)
        return specialist.run_task(prompt, context)

    def _refine_for_executive(self, raw_output: str) -> str:
        """
        Final synthesis step to apply CEO preferences.
        """
        tone = self.preferences.preferred_tone if self.preferences else "CONCISE"
        return f"[{tone}] {raw_output}"

from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseSpecialist(ABC):
    @abstractmethod
    def run_task(self, prompt: str, context: Dict[str, Any]) -> str:
        pass

class ClaudeSpecialist(BaseSpecialist):
    def run_task(self, prompt: str, context: Dict[str, Any]) -> str:
        # Simulate Claude's long-context document analysis
        return f"[Claude Response] Context analysis of {list(context.keys())} shows strong potential for the strategic query: {prompt}"

class GPTSpecialist(BaseSpecialist):
    def run_task(self, prompt: str, context: Dict[str, Any]) -> str:
        # Simulate GPT's logic and structured synthesis
        return f"[GPT-4o Response] Based on the following data: {context}, the structured recommendation for '{prompt}' is to proceed with caution."

class GeminiSpecialist(BaseSpecialist):
    def run_task(self, prompt: str, context: Dict[str, Any]) -> str:
        # Simulate Gemini's live research capabilities
        return f"[Gemini Response] Live search for '{prompt}' indicates current market trends align with the provided state: {context}"

class SpecialistFactory:
    @staticmethod
    def get_specialist(model_name: str) -> BaseSpecialist:
        if "claude" in model_name.lower():
            return ClaudeSpecialist()
        elif "gpt" in model_name.lower():
            return GPTSpecialist()
        elif "gemini" in model_name.lower():
            return GeminiSpecialist()
        return GPTSpecialist()  # Default to GPT

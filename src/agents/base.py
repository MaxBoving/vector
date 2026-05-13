from abc import ABC, abstractmethod
import os
from pathlib import Path
from string import Template
from typing import Any

from .schemas import AgentInput, AgentMetadata, AgentOutput


class BaseAgent(ABC):
    metadata: AgentMetadata

    @abstractmethod
    async def run(self, agent_input: AgentInput, **kwargs: Any) -> AgentOutput:
        """Run the agent against a structured input envelope."""

    def load_prompt(self, template_name: str, **kwargs: Any) -> str:
        """Load a prompt template from src/agents/prompts/ and hydrate it."""
        base_dir = Path(__file__).parent / "prompts"
        file_path = base_dir / template_name
        if not file_path.suffix:
            file_path = file_path.with_suffix(".md")

        if not file_path.exists():
            return f"Template {template_name} not found at {file_path}"

        with open(file_path, "r") as f:
            content = f.read()

        if kwargs:
            try:
                # Use Template for safe substitution of ${key} or $key
                return Template(content).safe_substitute(**kwargs)
            except Exception as e:
                return f"Error hydrating template {template_name}: {e}\n\n{content}"

        return content

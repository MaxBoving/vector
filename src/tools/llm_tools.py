import os
from typing import Any

from src.core.llm import DEFAULT_OPENAI_MODEL, LLMClient

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


class StructuredCompletionTool(BaseTool):
    metadata = ToolMetadata(
        name="structured_completion",
        description="Run a structured completion against the configured LLM client.",
        read_only=False,
        side_effects=True,
        tags=["llm", "completion", "structured"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        prompt = kwargs.get("prompt")
        system_prompt = kwargs.get("system_prompt", "You are the Brain.")
        response_model = kwargs.get("response_model")
        model = kwargs.get("model", os.getenv("STRUCTURED_COMPLETION_MODEL", DEFAULT_OPENAI_MODEL))
        if not prompt or response_model is None:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="prompt and response_model are required",
            )
        client = LLMClient(model=model)
        repair_attempted = False
        try:
            # complete_structured now includes an automatic repair pass on first failure
            tokens_before = client.total_tokens_used
            completion = client.complete_structured(prompt, response_model, system_prompt)
            tokens_after = client.total_tokens_used
            repair_attempted = (tokens_after - tokens_before) > (len(prompt) // 4 + len(system_prompt) // 4)
        except Exception as exc:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error=str(exc),
                metadata={"model": client.model, "tokens_used": client.total_tokens_used},
            )
        return ToolResult(
            tool_name=self.metadata.name,
            success=completion is not None,
            data={"completion": completion.model_dump() if completion is not None else None},
            error=None if completion is not None else "structured completion failed after repair attempt",
            metadata={
                "model": client.model,
                "tokens_used": client.total_tokens_used,
                "prompt_char_count": len(prompt),
                "system_prompt_char_count": len(system_prompt),
                "response_model": getattr(response_model, "__name__", str(response_model)),
                "repair_attempted": repair_attempted,
            },
        )

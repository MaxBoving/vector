from typing import Any

from src.core.execution import MathSandbox, SecurityScan, StrategicTagger

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


class ExecuteMathTool(BaseTool):
    metadata = ToolMetadata(
        name="execute_math",
        description="Execute sandboxed mathematical Python code.",
        read_only=False,
        side_effects=True,
        tags=["execution", "math"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        code = kwargs.get("code") or kwargs.get("expression") or kwargs.get("python_code")
        if not code:
            return ToolResult(tool_name=self.metadata.name, success=False, error="code is required")
        result = MathSandbox.execute_math(code)
        return ToolResult(
            tool_name=self.metadata.name,
            success=result.get("success", False),
            data={
                "output": result.get("output"),
                "variables": result.get("variables", {}),
            },
            error=result.get("error"),
        )


class ScanFileTool(BaseTool):
    metadata = ToolMetadata(
        name="scan_file",
        description="Run the current security scan over uploaded file content.",
        read_only=True,
        side_effects=False,
        tags=["execution", "security"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        content = kwargs.get("content", "")
        filename = kwargs.get("filename", "unknown")
        result = SecurityScan.scan_file(content, filename)
        return ToolResult(
            tool_name=self.metadata.name,
            success=result.get("safe", False),
            data=result,
            error=None if result.get("safe") else result.get("reason"),
        )


class TagDocumentTool(BaseTool):
    metadata = ToolMetadata(
        name="tag_document",
        description="Assign strategic domains to a document based on the current tagger.",
        read_only=True,
        side_effects=False,
        tags=["execution", "classification"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        content = kwargs.get("content", "")
        title = kwargs.get("title", "")
        tags = StrategicTagger.tag_document(content, title)
        return ToolResult(tool_name=self.metadata.name, success=True, data={"tags": tags})

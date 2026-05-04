from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel

from src.agents.schemas import AgentAction


class RuntimeArtifactRecord(BaseModel):
    artifact_stage: str
    label: str | None = None
    format: str | None = None
    status: str | None = None
    hidden: bool = False

    def response_artifact_ref_kwargs(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "format": self.format,
            "status": self.status or "generated",
        }

    def should_expose_in_response(self) -> bool:
        return not self.hidden


class GeneratedArtifactAction(RuntimeArtifactRecord):
    TOOL_NAMES: ClassVar[set[str]] = {"create_canvas", "create_docx_memo", "create_pptx_deck", "create_workbook"}
    FILTERED_ARG_KEYS: ClassVar[set[str]] = {"artifact_stage", "preview_stage", "preview_filename", "label", "format"}

    tool_name: str
    filename: str | None = None
    preview_stage: str | None = None
    preview_filename: str | None = None
    tool_args: dict[str, Any]

    @classmethod
    def from_agent_action(cls, action: AgentAction) -> "GeneratedArtifactAction | None":
        if action.action_type.value != "call_tool" or action.target not in cls.TOOL_NAMES:
            return None
        artifact_stage = action.args.get("artifact_stage")
        if not artifact_stage:
            return None
        tool_args = {
            key: value
            for key, value in action.args.items()
            if key not in cls.FILTERED_ARG_KEYS
        }
        return cls(
            tool_name=str(action.target),
            artifact_stage=str(artifact_stage),
            filename=action.args.get("filename"),
            label=action.args.get("label"),
            format=action.args.get("format"),
            status="generated",
            preview_stage=action.args.get("preview_stage"),
            preview_filename=action.args.get("preview_filename"),
            tool_args=tool_args,
        )

    def preview_write_kwargs(
        self,
        *,
        interaction_id: int | None,
        ceo_id: str,
        agent_name: str,
        result_metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self.preview_stage or not self.preview_filename:
            return None
        preview_content = result_metadata.get("preview_content")
        if not preview_content:
            return None
        preview_format = result_metadata.get("preview_format")
        preview_metadata = result_metadata.get("preview_metadata", {}) or {}
        return {
            "interaction_id": interaction_id,
            "ceo_id": ceo_id,
            "stage": self.preview_stage,
            "filename": self.preview_filename,
            "content": preview_content,
            "metadata": {
                "source": agent_name,
                "format": preview_format,
                "status": "generated",
                "hidden": True,
                **preview_metadata,
            },
        }


class WriteArtifactAction(RuntimeArtifactRecord):
    ACTION_TYPE: ClassVar[str] = "write_artifact"

    filename: str
    content: str
    metadata: dict[str, Any]

    @classmethod
    def from_agent_action(cls, action: AgentAction) -> "WriteArtifactAction | None":
        if action.action_type.value != cls.ACTION_TYPE:
            return None
        stage = action.args.get("stage")
        filename = action.args.get("filename")
        if not stage or not filename:
            return None
        metadata = action.args.get("metadata", {}) or {}
        return cls(
            artifact_stage=str(stage),
            filename=str(filename),
            content=str(action.args.get("content") or ""),
            metadata=dict(metadata),
            label=metadata.get("label"),
            format=metadata.get("format"),
            status=metadata.get("status"),
            hidden=bool(metadata.get("hidden")),
        )

    def write_kwargs(self, *, interaction_id: int | None, ceo_id: str) -> dict[str, Any]:
        return {
            "interaction_id": interaction_id,
            "ceo_id": ceo_id,
            "stage": self.artifact_stage,
            "filename": self.filename,
            "content": self.content,
            "metadata": self.metadata,
        }

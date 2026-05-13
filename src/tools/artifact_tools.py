from pathlib import Path
from typing import Any, Dict, Optional
import re
import yaml

from src.core.workbench import AgenticWorkbench

from .base import BaseTool, ToolContext, ToolMetadata, ToolResult


WORKSPACE_ROOT = Path("./workspaces")

_BINARY_ARTIFACT_STAGES = {
    "report_docx",
    "report_pptx",
    "analysis_xlsx",
}

STAGE_SEQUENCE = [
    "planning",
    "synthesizer",
    "executive_canvas",
    "canvas_preview",
    "report_docx",
    "report_docx_preview",
    "report_pptx",
    "report_pptx_preview",
    "analysis_xlsx",
    "analysis_spec",
]

STAGE_TO_WORKBENCH_AGENT = {
    "planning": "planner",
    "synthesizer": "synthesizer",
    "executive_canvas": "synthesizer",
    "canvas_preview": "synthesizer",
    "report_docx": "synthesizer",
    "report_docx_preview": "synthesizer",
    "report_pptx": "synthesizer",
    "report_pptx_preview": "synthesizer",
    "analysis_xlsx": "synthesizer",
    "analysis_spec": "synthesizer",
}

WORKBENCH_STAGE_DIRS = {
    "planner": "00_planning",
    "librarian": "01_raw_data",
    "quant": "01b_quantitative_analysis",
    "auditor": "02_verification",
    "strategist": "03_analysis",
    "synthesizer": "04_final_brief",
}

STAGE_ARTIFACT_FILENAMES = {
    "planning": "planner_execution.json",
    "synthesizer": "executive_summary.md",
    "executive_canvas": "executive-one-pager.html",
    "canvas_preview": "canvas_preview.html",
    "report_docx": "board_memo.docx",
    "report_docx_preview": "board_memo_preview.md",
    "report_pptx": "board_deck.pptx",
    "report_pptx_preview": "board_deck_preview.md",
    "analysis_xlsx": "analysis_workbook.xlsx",
    "analysis_spec": "analysis_spec.json",
}


def get_workspace_dir(interaction_id: int, ceo_id: str) -> Path:
    return WORKSPACE_ROOT / ceo_id / f"interaction_{interaction_id}"


def get_stage_artifact_path(interaction_id: int, ceo_id: str, stage: str) -> Optional[Path]:
    filename = STAGE_ARTIFACT_FILENAMES.get(stage)
    agent_name = STAGE_TO_WORKBENCH_AGENT.get(stage)
    stage_dir = WORKBENCH_STAGE_DIRS.get(agent_name or "")
    if not filename or not stage_dir:
        return None
    return get_workspace_dir(interaction_id, ceo_id) / stage_dir / filename


def read_stage_artifact(interaction_id: int, ceo_id: str, stage: str) -> str:
    path = get_stage_artifact_path(interaction_id, ceo_id, stage)
    if not path or not path.exists():
        return ""
    if stage in _BINARY_ARTIFACT_STAGES:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        return ""


def read_stage_artifact_metadata(interaction_id: int, ceo_id: str, stage: str) -> Dict[str, Any]:
    if stage in _BINARY_ARTIFACT_STAGES:
        return {}
    raw = read_stage_artifact(interaction_id, ceo_id, stage)
    if not raw.startswith("---\n"):
        return {}
    match = re.match(r"^---\n(.*?)\n---\n?", raw, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def hydrate_stage_artifacts(
    interaction_id: int,
    ceo_id: str,
    last_stage: Optional[str] = None,
    *,
    include_binary: bool = False,
) -> Dict[str, str]:
    artifacts: Dict[str, str] = {}

    start_idx = 0
    if last_stage and last_stage in STAGE_SEQUENCE:
        start_idx = STAGE_SEQUENCE.index(last_stage) + 1

    for stage in STAGE_SEQUENCE[start_idx:]:
        if not include_binary and stage in _BINARY_ARTIFACT_STAGES:
            continue
        content = read_stage_artifact(interaction_id, ceo_id, stage)
        if content:
            artifacts[stage] = content

    return artifacts


def hydrate_stage_artifact_refs(interaction_id: int, ceo_id: str) -> Dict[str, str]:
    refs: Dict[str, str] = {}

    for stage in STAGE_SEQUENCE:
        path = get_stage_artifact_path(interaction_id, ceo_id, stage)
        if path and path.exists():
            refs[stage] = str(path)

    return refs


class ReadArtifactTool(BaseTool):
    metadata = ToolMetadata(
        name="read_artifact",
        description="Read a staged workbench artifact for an interaction.",
        read_only=True,
        side_effects=False,
        tags=["artifacts", "filesystem"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        interaction_id = kwargs.get("interaction_id") or context.interaction_id
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        stage = kwargs.get("stage")
        if interaction_id is None or not ceo_id or not stage:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="interaction_id, ceo_id, and stage are required",
            )
        content = read_stage_artifact(int(interaction_id), ceo_id, stage)
        return ToolResult(
            tool_name=self.metadata.name,
            success=bool(content),
            data={"content": content, "stage": stage},
        )


class WriteArtifactTool(BaseTool):
    metadata = ToolMetadata(
        name="write_artifact",
        description="Write a staged workbench artifact using the current workspace contract.",
        read_only=False,
        side_effects=True,
        tags=["artifacts", "filesystem", "mutation"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        interaction_id = kwargs.get("interaction_id") or context.interaction_id
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        stage = kwargs.get("stage")
        filename = kwargs.get("filename") or STAGE_ARTIFACT_FILENAMES.get(stage)
        content = kwargs.get("content", "")
        metadata = kwargs.get("metadata", {})

        if interaction_id is None or not ceo_id or not stage or not filename:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="interaction_id, ceo_id, stage, and filename are required",
            )

        workbench = AgenticWorkbench(int(interaction_id), ceo_id)
        agent_name = STAGE_TO_WORKBENCH_AGENT.get(stage)
        if not agent_name:
            return ToolResult(tool_name=self.metadata.name, success=False, error=f"unknown stage: {stage}")

        path = workbench.write_step(agent_name, filename, content, metadata=metadata)
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"path": path, "stage": stage, "filename": filename},
        )


class ListArtifactsTool(BaseTool):
    metadata = ToolMetadata(
        name="list_artifacts",
        description="List available staged workbench artifact references for an interaction.",
        read_only=True,
        side_effects=False,
        tags=["artifacts", "filesystem"],
    )

    def invoke(self, context: ToolContext, **kwargs: Any) -> ToolResult:
        interaction_id = kwargs.get("interaction_id") or context.interaction_id
        ceo_id = kwargs.get("ceo_id") or context.ceo_id
        if interaction_id is None or not ceo_id:
            return ToolResult(
                tool_name=self.metadata.name,
                success=False,
                error="interaction_id and ceo_id are required",
            )
        refs = hydrate_stage_artifact_refs(int(interaction_id), ceo_id)
        return ToolResult(tool_name=self.metadata.name, success=True, data={"artifacts": refs})

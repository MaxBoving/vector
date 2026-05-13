from __future__ import annotations

from pathlib import Path

import src.runtime.engine as engine_module
from src.agents.schemas import AgentAction, AgentOutput, ActionType
from src.api.schemas import AnswerPayload, AssistantMessageResponse, TrustMetadata
from src.core.models import User, WorkflowRun
from src.runtime.engine import RuntimeEngine
from src.runtime.state import WorkflowState
from src.tools.base import BaseTool, ToolContext, ToolMetadata, ToolResult
from src.tools.registry import ToolRegistry
from src.workflows.schedule_planning import SCHEDULE_PLANNING_WORKFLOW
from src.workflows.types import WorkflowDefinition, WorkflowStepDefinition


class FakeWriteArtifactTool(BaseTool):
    metadata = ToolMetadata(name="write_artifact", description="fake", read_only=False, side_effects=True)

    def invoke(self, context: ToolContext, **kwargs):
        return ToolResult(tool_name=self.metadata.name, success=True, data={"stage": kwargs.get("stage")})


class FakeCreateDocxTool(BaseTool):
    metadata = ToolMetadata(name="create_docx_memo", description="fake", read_only=False, side_effects=True)

    def invoke(self, context: ToolContext, **kwargs):
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"path": kwargs.get("output_path")},
            metadata={
                "preview_content": "# Preview",
                "preview_format": "md",
                "preview_metadata": {"template_id": "board_memo_v1", "theme_id": "board_formal"},
            },
        )


def test_execute_actions_uses_typed_generated_artifact_contract(monkeypatch) -> None:
    registry = ToolRegistry()
    registry.register_many([FakeWriteArtifactTool(), FakeCreateDocxTool()])

    calls: list[tuple[str, dict]] = []
    original_invoke_with_event = registry.invoke_with_event

    def traced_invoke_with_event(name, context=None, **kwargs):
        calls.append((name, kwargs))
        return original_invoke_with_event(name, context, **kwargs)

    registry.invoke_with_event = traced_invoke_with_event  # type: ignore[method-assign]

    engine = RuntimeEngine(tools=registry)
    engine._append_run_event = lambda workflow_run, payload: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        engine_module,
        "get_stage_artifact_path",
        lambda interaction_id, ceo_id, stage: Path(f"/tmp/{interaction_id}_{ceo_id}_{stage}"),
    )
    monkeypatch.setattr(engine, "_artifact_output_missing", lambda path: False)

    workflow_state = WorkflowState(
        workflow_id="wf-1",
        workflow_type="report_generation",
        interaction_id=22,
        ceo_id="ceo_test",
        company_name="Agentic Mind",
        current_stage="synthesizer",
    )
    workflow_run = WorkflowRun(
        workflow_id="wf-1",
        interaction_id=22,
        ceo_id="ceo_test",
        workflow_type="report_generation",
    )
    current_user = User(username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")
    response = AssistantMessageResponse(
        conversation_id="conv-1",
        message_id="msg-1",
        workflow_type="report_generation",
        response_type="report",
        status="completed",
        answer=AnswerPayload(title="Trace", summary="Trace", sections=[]),
        trust=TrustMetadata(),
        metadata={},
    )
    agent_output = AgentOutput(
        agent_name="report_agent",
        stage="synthesizer",
        actions=[
            AgentAction(
                action_type=ActionType.CALL_TOOL,
                target="create_docx_memo",
                args={
                    "artifact_stage": "report_docx",
                    "filename": "board_memo.docx",
                    "label": "Executive Memo",
                    "format": "docx",
                    "preview_stage": "report_docx_preview",
                    "preview_filename": "board_memo_preview.md",
                    "memo_spec": {"title": "Board Memo", "sections": [], "summary": "", "metadata": {}},
                },
            )
        ],
    )

    engine._execute_actions(agent_output, workflow_state, workflow_run, current_user, response)

    assert calls[0][0] == "create_docx_memo"
    assert sorted(calls[0][1].keys()) == ["filename", "memo_spec", "output_path"]
    assert calls[0][1]["output_path"] == "/tmp/22_ceo_test_report_docx"

    assert calls[1][0] == "write_artifact"
    assert calls[1][1]["stage"] == "report_docx_preview"
    assert calls[1][1]["filename"] == "board_memo_preview.md"
    assert calls[1][1]["metadata"]["source"] == "report_agent"
    assert calls[1][1]["metadata"]["hidden"] is True
    assert calls[1][1]["metadata"]["template_id"] == "board_memo_v1"

    assert [artifact.artifact_type for artifact in response.artifacts] == ["report_docx"]
    assert response.artifacts[0].label == "Executive Memo"
    assert response.artifacts[0].format == "docx"
    assert workflow_state.artifacts["report_docx"] == "/tmp/22_ceo_test_report_docx"
    assert workflow_state.artifacts["report_docx_preview"] == "interaction:22:report_docx_preview"


def test_execute_actions_uses_typed_write_artifact_contract() -> None:
    registry = ToolRegistry()
    registry.register(FakeWriteArtifactTool())

    calls: list[tuple[str, dict]] = []
    original_invoke_with_event = registry.invoke_with_event

    def traced_invoke_with_event(name, context=None, **kwargs):
        calls.append((name, kwargs))
        return original_invoke_with_event(name, context, **kwargs)

    registry.invoke_with_event = traced_invoke_with_event  # type: ignore[method-assign]

    engine = RuntimeEngine(tools=registry)
    engine._append_run_event = lambda workflow_run, payload: None  # type: ignore[method-assign]

    workflow_state = WorkflowState(
        workflow_id="wf-2",
        workflow_type="report_generation",
        interaction_id=23,
        ceo_id="ceo_test",
        company_name="Agentic Mind",
        current_stage="synthesizer",
    )
    workflow_run = WorkflowRun(
        workflow_id="wf-2",
        interaction_id=23,
        ceo_id="ceo_test",
        workflow_type="report_generation",
    )
    current_user = User(username="ceo", hashed_password="x", ceo_id="ceo_test", company_name="Agentic Mind")
    response = AssistantMessageResponse(
        conversation_id="conv-2",
        message_id="msg-2",
        workflow_type="report_generation",
        response_type="report",
        status="completed",
        answer=AnswerPayload(title="Trace", summary="Trace", sections=[]),
        trust=TrustMetadata(),
        metadata={},
    )
    agent_output = AgentOutput(
        agent_name="report_agent",
        stage="synthesizer",
        actions=[
            AgentAction(
                action_type=ActionType.WRITE_ARTIFACT,
                target="memo_outline",
                args={
                    "stage": "memo_outline",
                    "filename": "memo_outline.md",
                    "content": "# Outline",
                    "metadata": {
                        "label": "Memo Outline",
                        "format": "md",
                        "status": "generated",
                    },
                },
            )
        ],
    )

    engine._execute_actions(agent_output, workflow_state, workflow_run, current_user, response)

    assert calls == [
        (
            "write_artifact",
            {
                "interaction_id": 23,
                "ceo_id": "ceo_test",
                "stage": "memo_outline",
                "filename": "memo_outline.md",
                "content": "# Outline",
                "metadata": {
                    "label": "Memo Outline",
                    "format": "md",
                    "status": "generated",
                },
            },
        )
    ]
    assert [artifact.artifact_type for artifact in response.artifacts] == ["memo_outline"]
    assert response.artifacts[0].label == "Memo Outline"
    assert response.artifacts[0].format == "md"
    assert workflow_state.artifacts["memo_outline"] == "interaction:23:memo_outline"


def test_build_clarification_response_exposes_clickable_options() -> None:
    engine = RuntimeEngine(tools=ToolRegistry())
    workflow_state = WorkflowState(
        workflow_id="wf-clarify",
        workflow_type="report_generation",
        interaction_id=24,
        ceo_id="ceo_test",
        company_name="Agentic Mind",
        current_stage="synthesizer",
    )
    interaction = type(
        "InteractionStub",
        (),
        {"id": 24, "timestamp": "2026-03-29T00:00:00", "ceo_id": "ceo_test"},
    )()
    payload = type("PayloadStub", (), {"conversation_id": "conv-clarify", "message": "Board packet question"})()
    definition = WorkflowDefinition(
        workflow_type="report_generation",
        entry_step="planning",
        steps=[WorkflowStepDefinition(name="planning")],
    )
    agent_output = AgentOutput(
        agent_name="report_agent",
        stage="synthesizer",
        structured_output={
            "presentation": {"preamble": "Choose the frame you want."},
            "clarification_options": [
                {
                    "label": "Board Packet",
                    "value": "board_packet",
                    "description": "Frame this as board-facing material.",
                    "apply_text": "Frame this for the board packet.",
                },
                {
                    "label": "Finance Close",
                    "value": "finance_close",
                    "description": "Frame this for the finance close discussion.",
                    "apply_text": "Frame this for the finance close meeting.",
                },
            ],
        },
        metadata={"original_query": "What should I say about cloud spend variance?"},
    )

    response = engine._build_clarification_response(  # type: ignore[attr-defined]
        definition=definition,
        payload=payload,
        interaction=interaction,
        agent_output=agent_output,
        workflow_state=workflow_state,
        routing_decision=None,
    )

    assert response.response_type == "clarification"
    assert response.presentation.decision is not None
    assert [option.label for option in response.presentation.decision.options] == ["Board Packet", "Finance Close"]
    assert response.metadata["clarification_options"][0]["value"] == "board_packet"
    assert response.trust.question_options[0].question == "Choose the frame you want."
    assert response.trust.question_options[0].options[0].value == "board_packet"


def test_merge_default_presentation_does_not_backfill_explicit_agent_presentation() -> None:
    engine = RuntimeEngine(tools=ToolRegistry())

    merged = engine._merge_default_presentation(  # type: ignore[attr-defined]
        definition=SCHEDULE_PLANNING_WORKFLOW,
        presentation={"mode": "schedule"},
        summary="Plan",
    )

    assert merged == {"mode": "schedule"}

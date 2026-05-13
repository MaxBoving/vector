from __future__ import annotations

from types import SimpleNamespace

from src.agents.schemas import ActionType, AgentAction, AgentOutput
from src.runtime.engine import RuntimeEngine
from src.runtime.state import WorkflowState
from src.tools.base import ToolResult


class _DummyTools:
    def __init__(self) -> None:
        self.seen_context = None

    def list_tools(self) -> list[str]:
        return ["get_live_context"]

    def invoke_with_event(self, name: str, context=None, **kwargs):
        self.seen_context = context
        return (
            ToolResult(
                tool_name=name,
                success=True,
                data={"live_context": {"conversation_id": context.conversation_id}},
            ),
            SimpleNamespace(model_dump=lambda: {"tool_name": name, "status": "completed"}),
        )


def test_execute_tool_requests_passes_conversation_id(monkeypatch):
    engine = RuntimeEngine()
    engine.tools = _DummyTools()
    monkeypatch.setattr(engine, "_record_tool_event", lambda *args, **kwargs: None)

    workflow_state = WorkflowState(
        workflow_id="wf_001",
        workflow_type="morning_brief",
        interaction_id=7,
        ceo_id="ceo_001",
        company_name="Vela",
        current_stage="load_conversation_thread",
        metadata={"conversation_id": "conv:ceo_001:123"},
    )
    agent_output = AgentOutput(
        agent_name="runtime_context_loader",
        stage="load_conversation_thread",
        success=True,
        actions=[
            AgentAction(
                action_type=ActionType.CALL_TOOL,
                target="get_live_context",
                args={"result_key": "live_context"},
            )
        ],
    )
    current_user = SimpleNamespace(ceo_id="ceo_001", company_name="Vela")

    update = engine._execute_tool_requests(
        agent_output=agent_output,
        workflow_state=workflow_state,
        workflow_run=SimpleNamespace(),
        current_user=current_user,
    )

    assert engine.tools.seen_context is not None
    assert engine.tools.seen_context.conversation_id == "conv:ceo_001:123"
    assert update["live_context"]["live_context"]["conversation_id"] == "conv:ceo_001:123"

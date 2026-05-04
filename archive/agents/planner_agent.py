import json
from typing import Any

from src.workflows.plan_execution import run_planner_stage
from src.workflows.planning_types import RequestPlan

from .base import BaseAgent
from .schemas import AgentInput, AgentMetadata, AgentOutput, write_artifact_action


class PlannerAgent(BaseAgent):
    metadata = AgentMetadata(
        name="planner_agent",
        description="Executes planner-managed evidence gathering and schedule placement stages.",
        stage="planning",
        allowed_tools=["write_artifact"],
        tags=["planning", "schedule", "planner"],
    )

    async def run(self, agent_input: AgentInput, **kwargs: Any) -> AgentOutput:
        planner_stage = agent_input.metadata.get("planner_stage")
        workflow_metadata = agent_input.workflow_state.metadata or {}
        request_plan_data = workflow_metadata.get("request_plan")
        if not planner_stage or not isinstance(request_plan_data, dict):
            return AgentOutput(
                agent_name=self.metadata.name,
                stage=agent_input.stage,
                success=True,
                summary="No planner stage executed.",
                structured_output={
                    "answer": {
                        "title": "Planner Stage Skipped",
                        "summary": "No planner stage was executed.",
                        "sections": [],
                    },
                    "trust": {
                        "confidence": "low",
                        "confidence_score": 0.2,
                        "assumptions": [],
                        "open_questions": [],
                        "data_quality": "low",
                        "calculation_used": False,
                        "missing_context": ["Missing planner stage or request plan."],
                    },
                    "sources": [],
                    "status": "skipped",
                    "reason": "Missing planner stage or request plan.",
                },
            )

        request_plan = RequestPlan(**request_plan_data)
        event_payload = dict(workflow_metadata.get("event_payload", {}) or {})
        planner_execution = dict(workflow_metadata.get("planner_execution", {}) or {})
        updated_payload, updated_execution, stage_output = run_planner_stage(
            stage_key=planner_stage,
            request_plan=request_plan,
            event_payload=event_payload,
            planner_execution=planner_execution,
        )

        artifact_payload = json.dumps(
            {
                "planner_stage": planner_stage,
                "execution_mode": updated_execution.get("execution_mode"),
                "planning_horizon": updated_execution.get("planning_horizon"),
                "planning_window": updated_execution.get("planning_window"),
                "executed_plan_steps": updated_execution.get("executed_plan_steps", []),
                "evidence_summary": updated_execution.get("evidence_summary", {}),
                "sparse_guidance": updated_execution.get("sparse_guidance", False),
                "schedule_blocks": updated_execution.get("schedule_blocks", []),
            },
            indent=2,
            sort_keys=True,
        )

        return AgentOutput(
            agent_name=self.metadata.name,
            stage=agent_input.stage,
            success=True,
            summary=f"Planner stage '{planner_stage}' executed.",
            structured_output={
                "answer": {
                    "title": f"Planner Stage: {planner_stage}",
                    "summary": f"Planner stage '{planner_stage}' executed.",
                    "sections": [],
                },
                "trust": {
                    "confidence": "medium",
                    "confidence_score": 0.6,
                    "assumptions": ["This is an intermediate planner stage result, not the final executive brief."],
                    "open_questions": [],
                    "data_quality": "medium",
                    "calculation_used": False,
                    "missing_context": [],
                },
                "sources": [],
                "planner_stage": planner_stage,
                "execution_record": stage_output,
                "planner_execution": {
                    "execution_mode": updated_execution.get("execution_mode"),
                    "planning_horizon": updated_execution.get("planning_horizon"),
                    "evidence_summary": updated_execution.get("evidence_summary", {}),
                    "sparse_guidance": updated_execution.get("sparse_guidance"),
                },
            },
            actions=[
                write_artifact_action(
                    "planning",
                    "planner_execution.json",
                    artifact_payload,
                    source=self.metadata.name,
                    format="json",
                    hidden=True,
                    label="Planner Execution",
                    status="generated",
                )
            ],
            metadata={
                "event_payload": updated_payload,
                "planner_execution": updated_execution,
            },
        )

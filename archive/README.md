# Archive — Pre-Agentic Architecture

Archived 2026-05-04. These files implemented the 5-stage routing/workflow
pipeline replaced by the AgenticAssistant in src/assistant/agent.py.

Nothing in the active codebase imports from here. Safe to delete once the
new pipeline is verified in production.

## Contents
- agents/       — BriefingAgent, ReportAgent, PlannerAgent, etc.
- runtime/      — WorkflowEngine, StageHandlers, Bootstrap
- assistant/    — AssistantService, SemanticArbitration, IntentClassifier, etc.
- workflows/    — Routing pipeline (routing.py, request_planner.py, runner.py, etc.)

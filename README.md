# agenticMIND

## What This Repository Does
`agenticMIND` is a CEO-facing assistant built with a Python `FastAPI` backend and a React/Vite frontend. The active product surface is a single assistant experience for executive report generation and document explanation.

The backend accepts assistant queries, loads CEO-scoped company context and indexed documents, runs a compact workflow through the assistant runtime, persists the resulting message, and returns a structured response with trust metadata and sources.

## Active Product Surface
- `POST /auth/login`
- `POST /auth/register`
- `POST /assistant/query`
- `POST /assistant/messages/{interaction_id}/resolve`
- `POST /events/email`
- `POST /events/calendar`
- `POST /briefings/morning`
- `GET /assistant/messages/{interaction_id}`
- `GET /assistant/conversations/{conversation_id}`
- `GET /documents`
- `POST /documents/upload`
- `GET /health`

## Quick Start
Backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.api.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Configuration:

- Set `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY` in your environment.
- Optional: set `JWT_SECRET_KEY` and model overrides used by `src/core/llm.py`.
- Mixed-model defaults now support per-agent overrides. Recommended setup:
  - `ROUTER_AGENT_MODEL=gpt-4o-mini`
  - `STRUCTURED_COMPLETION_MODEL=gpt-4o-mini`
  - `REPORT_AGENT_MODEL=claude-sonnet-4-20250514`
  - `BRIEFING_AGENT_MODEL=claude-sonnet-4-20250514`
  - `EXPLAINER_AGENT_MODEL=claude-sonnet-4-20250514`
  - `FAKE_CEO_EVAL_MODEL=claude-sonnet-4-20250514`
- On first backend startup, SQLite tables are created in `agenticmind.db`.

## Common Commands
- Start API: `uvicorn src.api.main:app --reload`
- Start frontend dev server: `cd frontend && npm run dev`
- Build frontend: `cd frontend && npm run build`
- Backend syntax check: `python3 -m py_compile src/api/main.py src/workflows/document_ingestion.py`
- Quality benchmark: `pytest tests/test_integration_benchmark.py -v -s -m integration`
  Writes `docs/benchmark_report.json` with per-action thresholds, grades, latency, grounding, vagueness, and specificity gaps.
  Appends a rolling history snapshot to `docs/benchmark_history.json` for trend tracking.
  Optional: set `BENCHMARK_ENABLE_EVALUATOR=1` to add model-graded rubric scoring to each benchmark case.
- Exploratory fake CEO eval: `python3 scripts/run_fake_ceo_eval.py --scenario all`
  Writes `docs/fake_ceo_eval_report.json` with seeded multi-turn transcripts, simulator reactions, and evaluator scores.
  Appends a rolling history snapshot to `docs/fake_ceo_eval_history.json` and classifies score changes as likely noise vs possible/material shifts.

## Repository Layout
- `src/api/`: Thin assistant API boundary for auth, assistant messages, and document upload/listing.
- `src/runtime/`: Deterministic workflow execution engine and runtime state/event primitives.
- `src/workflows/`: Assistant workflow definitions, read model helpers, and document ingestion flow.
- `src/agents/`: Router, report, explainer, and specialist agent implementations.
- `src/tools/`: Assistant-runtime tool registry and deterministic adapters.
- `src/core/`: Shared persistence, prompts, knowledge retrieval, LLM clients, execution helpers, and workspace artifact I/O.
- `frontend/`: CEO-facing assistant shell.
- `inputs/`: Seed documents used to populate company knowledge.
- `docs/`: Current product and architecture references.

## Documentation Map
- Architecture overview: [ARCHITECTURE.md](./ARCHITECTURE.md)
- Detailed architecture notes: [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
- MVP product contract: [docs/MVP_PRODUCT_PLAN.md](./docs/MVP_PRODUCT_PLAN.md)
- Implementation timeline: [docs/IMPLEMENTATION_TIMELINE.md](./docs/IMPLEMENTATION_TIMELINE.md)
- Two-worker split: [docs/IMPLEMENTATION_WORKERS.md](./docs/IMPLEMENTATION_WORKERS.md)
- Collaborator UI handoff: [docs/COLLABORATOR_UI_MIGRATION.md](./docs/COLLABORATOR_UI_MIGRATION.md)
- Financial workbook implementation stories: [docs/FINANCIAL_WORKBOOK_USER_STORIES.md](./docs/FINANCIAL_WORKBOOK_USER_STORIES.md)
- CEO accuracy roadmap: [docs/CEO_ACCURACY_IMPLEMENTATION_PLAN.md](./docs/CEO_ACCURACY_IMPLEMENTATION_PLAN.md)
- Claude skills integration research: [docs/CLAUDE_SKILLS_INTEGRATION_RESEARCH.md](./docs/CLAUDE_SKILLS_INTEGRATION_RESEARCH.md)
- Email watcher roadmap: [docs/EMAIL_WATCHER_IMPLEMENTATION_PLAN.md](./docs/EMAIL_WATCHER_IMPLEMENTATION_PLAN.md)
- Router refactor roadmap: [docs/ROUTER_REFACTOR_PLAN.md](./docs/ROUTER_REFACTOR_PLAN.md)
- Google Workspace flows research plan: [docs/GOOGLE_WORKSPACE_FLOWS_RESEARCH_PLAN.md](./docs/GOOGLE_WORKSPACE_FLOWS_RESEARCH_PLAN.md)
- Google Workspace capability note: [docs/GOOGLE_WORKSPACE_CAPABILITY_NOTE.md](./docs/GOOGLE_WORKSPACE_CAPABILITY_NOTE.md)
- Google Workspace identity and scoping note: [docs/GOOGLE_WORKSPACE_IDENTITY_AND_SCOPING.md](./docs/GOOGLE_WORKSPACE_IDENTITY_AND_SCOPING.md)
- Google Workspace pilot plan: [docs/GOOGLE_WORKSPACE_PILOT_PLAN.md](./docs/GOOGLE_WORKSPACE_PILOT_PLAN.md)
- Google Workspace Calendar implementation plan: [docs/GOOGLE_WORKSPACE_CALENDAR_IMPLEMENTATION_PLAN.md](./docs/GOOGLE_WORKSPACE_CALENDAR_IMPLEMENTATION_PLAN.md)
- Artifact presentation system plan: [docs/ARTIFACT_PRESENTATION_SYSTEM_PLAN.md](./docs/ARTIFACT_PRESENTATION_SYSTEM_PLAN.md)
- Skills/documentation eval spec: [docs/SKILLS_EVAL.md](./docs/SKILLS_EVAL.md)
- Skills/documentation binary rubric: [docs/skills_eval_rubric.json](./docs/skills_eval_rubric.json)
- Contribution workflow: [CONTRIBUTING.md](./CONTRIBUTING.md)
- Agent operating constraints: [AGENTS.md](./AGENTS.md)

## Current Architecture Summary
The active runtime path is assistant-native:

1. Authenticate the user.
2. Submit a query to `/assistant/query`.
3. Create a `SessionInteraction`.
4. Classify the request into `watch`, `plan`, `act`, or `report`.
5. Select a workflow such as `report_generation`, `document_explanation`, `email_ingestion`, `calendar_briefing`, `morning_brief`, or `day_schedule_planning`.
6. Run the workflow through `RuntimeEngine`, which remains the sole executor of tools, gates, retries, and artifacts.
7. Persist the structured response and write assistant artifacts under `workspaces/{ceo_id}/interaction_{id}/...`.
8. Read messages back through the assistant read model for the frontend.

The current planning refactor is partially complete:
- structured routing and planner-led workflow selection are active
- event-driven watch workflows are active
- compound planning still relies on helper logic in `src/workflows/runner.py` and still sits outside a dedicated workflow family

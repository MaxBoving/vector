# Detailed Architecture

## Primary Runtime
The active runtime path is:

1. `POST /assistant/query`
2. `src/workflows/runner.py`
3. `src/runtime/engine.py`
4. `src/workflows/routing.py` and `src/workflows/request_planner.py`
5. `src/agents/router_agent.py`
6. `src/agents/report_agent.py`, `src/agents/explainer_agent.py`, or `src/agents/briefing_agent.py`
7. `src/tools/registry.py`
8. `src/workflows/read_model.py`

This is the only supported product path.

## Workflow Types

### `report_generation`
- Used for company-state-driven executive reporting.
- Retrieves company state, preferences, and semantic context.
- Produces a structured report payload with trust metadata and sources.

### `document_explanation`
- Used when the user attaches or references documents.
- Retrieves relevant document context and explains business implications.
- Produces a structured explanation payload with trust metadata and sources.

### `email_ingestion`
- Used for inbox watch and event-driven email triage.
- Combines provider email payloads with CEO-scoped context stages.
- Produces an executive inbox brief through `BriefingAgent`.

### `calendar_briefing`
- Used for meeting prep and calendar watch requests.
- Combines calendar payloads with company and retrieval context.
- Produces a meeting-oriented briefing through `BriefingAgent`.

### `morning_brief`
- Used for combined watch requests and scheduled brief generation.
- Pulls inbox, calendar, retrieval, and signal context into one briefing payload.

### `day_schedule_planning`
- Used for direct planning requests and planner-led compound weekly requests.
- Currently serves as the carrier workflow for compound inbox/calendar planning evidence.
- Still depends on helper assembly in `src/workflows/runner.py` for the weekly compound path.

## Routing Model
Assistant requests are first classified into four route families:
- `watch`
- `plan`
- `act`
- `report`

`src/workflows/routing.py` returns a typed `RouteDecision`. `src/workflows/request_planner.py` adds a `RequestPlan` for planner-led paths, including compound plans that mix inbox, calendar, documents, and a schedule synthesis step.

Current design note:
- the route taxonomy is implemented
- the remaining refactor is to promote planner-led compound execution out of `runner.py` into dedicated planning workflows and stages

## Persistence Model
- `SessionInteraction`: request/response history for a CEO conversation.
- `WorkflowRun`: persisted workflow state, event log, and structured assistant response.
- `CompanyState`: CEO/company-scoped business state and indexed knowledge base entries.
- `CEOPreferences`: stored executive preference state used during generation.

## Documents
`src/workflows/document_ingestion.py` is the active document ingestion flow.

It is responsible for:
- decoding the upload
- running the security scan
- extracting tags and a short summary
- indexing chunks in Chroma via `src/core/knowledge.py`
- persisting the document into `CompanyState.knowledge_base`

## Artifacts
The assistant runtime still writes workspace artifacts under `workspaces/{ceo_id}/interaction_{id}`. Those files are used as durable intermediate output and by the assistant read model.

## Frontend Contract
The frontend should rely on the assistant message envelope defined in `src/api/schemas.py`:

- `conversation_id`
- `message_id`
- `workflow_type`
- `response_type`
- `status`
- `answer`
- `trust`
- `sources`
- `artifacts`
- `metadata`

Current caveat:
- the frontend envelope is stable
- the read model in `src/workflows/read_model.py` still under-represents non-report workflow types when rebuilding responses from artifacts only

## Related Docs
- [MVP_PRODUCT_PLAN.md](./MVP_PRODUCT_PLAN.md)
- [IMPLEMENTATION_TIMELINE.md](./IMPLEMENTATION_TIMELINE.md)
- [IMPLEMENTATION_WORKERS.md](./IMPLEMENTATION_WORKERS.md)
- [COLLABORATOR_UI_MIGRATION.md](./COLLABORATOR_UI_MIGRATION.md)
- [FINANCIAL_WORKBOOK_USER_STORIES.md](./FINANCIAL_WORKBOOK_USER_STORIES.md)
- [CLAUDE_SKILLS_INTEGRATION_RESEARCH.md](./CLAUDE_SKILLS_INTEGRATION_RESEARCH.md)
- [CEO_ACCURACY_IMPLEMENTATION_PLAN.md](./CEO_ACCURACY_IMPLEMENTATION_PLAN.md)
- [EMAIL_WATCHER_IMPLEMENTATION_PLAN.md](./EMAIL_WATCHER_IMPLEMENTATION_PLAN.md)
- [ROUTER_REFACTOR_PLAN.md](./ROUTER_REFACTOR_PLAN.md)

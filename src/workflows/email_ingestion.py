from src.workflows.briefing_workflow_factory import build_briefing_workflow


# User-facing email workflow routed from conversational turns (e.g. "check my
# email"). Session history is included so the briefing agent can honour
# follow-up instructions like "focus on the board thread" — the tool falls
# back to global history when no conversation_id is available (webhook path).
EMAIL_INGESTION_WORKFLOW = build_briefing_workflow(
    workflow_type="email_ingestion",
    failure_title="Email Ingestion Failed",
    failure_summary="The system could not process the incoming email event.",
    response_type="brief",
    presentation_mode="brief",
    presentation_variant="inbox_watch",
    include_session_history=True,
    include_signals=True,
)

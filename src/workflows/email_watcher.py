from src.workflows.briefing_workflow_factory import build_briefing_workflow


# Full user-facing inbox brief workflow. Loads session history and signals so
# the briefing agent has complete CEO context when synthesising. This is what
# "scan my inbox" / "what's in my email" routes to.
EMAIL_WATCHER_WORKFLOW = build_briefing_workflow(
    workflow_type="email_watcher",
    failure_title="Inbox Watch Failed",
    failure_summary="The system could not complete your inbox briefing.",
    response_type="brief",
    presentation_mode="brief",
    presentation_variant="inbox_watch",
    include_session_history=True,
    include_signals=True,
)

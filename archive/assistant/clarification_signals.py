"""
Declarative mapping from clarification option values to (signal_type, signal_value) pairs.

Option values are structured data produced by report_agent._clarification_options().
This mapping is the single source of truth — no keyword matching, no inference.

To add a new clarification question type:
  1. Add options with structured `value` fields in _clarification_options()
  2. Add the value → signal mapping here
"""
from __future__ import annotations

# Maps option `value` field → (signal_type, signal_value) for DB persistence.
# signal_type must match the key used in CEOPreferences.learned_defaults and
# ConversationLiveContext.resolved_clarifications.
OPTION_VALUE_TO_SIGNAL: dict[str, tuple[str, str]] = {
    # Output format / framing
    "board_packet":       ("output_format", "board_presentation"),
    "board_presentation": ("output_format", "board_presentation"),
    "personal_decision":  ("output_format", "personal_decision"),
    "operating_decision": ("output_format", "personal_decision"),
    # Day / schedule optimisation
    "calendar_first":     ("day_optimization", "calendar_first"),
    "inbox_deadlines":    ("day_optimization", "inbox_deadlines"),
    "meeting_focused":    ("day_optimization", "meeting_focused"),
    "focus_blocks":       ("day_optimization", "focus_blocks"),
    # Data source anchor
    "close_workbook":     ("data_source", "close_workbook"),
    "company_state":      ("data_source", "company_state"),
    # Time / period anchor
    "current_month":      ("time_anchor", "current_month"),
    "quarter_close":      ("time_anchor", "quarter_close"),
    # Escalation response mode
    "draft_response":     ("escalation_mode", "draft_response"),
    "brief_only":         ("escalation_mode", "brief_only"),
}

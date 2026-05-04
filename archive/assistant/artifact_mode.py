"""
Artifact mode detection: action-offer acceptance (C1/C2) and direct request (D1).

Exports three public symbols used by the runner and tests:

  detect_action_offer_acceptance  — C1/C2: CEO accepts a prior action_offer
  detect_artifact_type_from_request — D1: CEO's message explicitly requests
                                      a specific output artifact type
  resolve_artifact_mode           — orchestrator: runs C1→C2→D1 and merges
                                    results into an ArtifactModeResult

Internal helpers (_infer_artifact_type_from_offer, lookup tables) are kept
private to this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.workflows.message_scaffolding import extract_visible_request_text
from src.workflows.request_planner import plan_request
from src.workflows.intent_state import parse_turn_intent
from src.workflows.types import WorkflowType

# ---------------------------------------------------------------------------
# C1/C2 — action offer acceptance
# ---------------------------------------------------------------------------

# Maps artifact_type → default output modality for file delivery.
# Single source of truth — adding a new offer type requires one line here.
_ARTIFACT_TYPE_MODALITY: dict[str, str] = {
    "board_brief": "docx",
    "action_plan": "docx",
    "email":       "inline",
}

# Maps offer text keywords → artifact_type for D1/D2 consumption.
_OFFER_ARTIFACT_TYPE_MAP: list[tuple[list[str], str]] = [
    (["brief", "board materials", "board prep", "full data-backed", "comprehensive brief",
      "working doc", "full document", "board document"], "board_brief"),
    (["action plan", "action items", "owners", "next steps with owners"], "action_plan"),
    (["email", "draft email", "draft message", "delegation email", "delegate"], "email"),
]


def detect_action_offer_acceptance(
    prev_response: dict | None,
    ceo_message: str,
) -> tuple[bool, str | None]:
    """
    Return (True, offer_text) if the CEO message is an affirmative acceptance
    of a prior action_offer question option; (False, None) otherwise.

    Checks:
    1. Prior assistant response had ≥1 question_option with offer_type == "action_offer"
    2. Current turn semantically behaves like a follow-up/choice to that offer
    3. Message content aligns with one of the offered options
    """
    if not prev_response or not ceo_message:
        return False, None

    trust = prev_response.get("trust") or {}
    question_options = trust.get("question_options") or []
    action_offers = [
        qo for qo in question_options
        if isinstance(qo, dict) and qo.get("offer_type") == "action_offer"
    ]
    if not action_offers:
        return False, None

    parsed = parse_turn_intent(
        message=ceo_message,
        previous_state=None,
        artifact_context=None,
    )
    if parsed.mode not in {"clarification_response", "continuation", "correction"}:
        return False, None

    message_tokens = _semantic_tokens(ceo_message)
    best_score = 0.0
    best_text: str | None = None
    for offer in action_offers:
        question_text = str(offer.get("question") or "").strip()
        options = offer.get("options") or []
        if not options:
            # If no options are present but this is a continuation-style turn,
            # treat the offer question itself as the accepted target.
            if question_text:
                return True, question_text
            continue
        for option in options:
            if not isinstance(option, dict):
                continue
            candidate = " ".join(
                [
                    str(option.get("label") or ""),
                    str(option.get("value") or "").replace("_", " "),
                    str(option.get("apply_text") or ""),
                    question_text,
                ]
            ).strip()
            if not candidate:
                continue
            score = _token_overlap(message_tokens, _semantic_tokens(candidate))
            if score > best_score:
                best_score = score
                best_text = candidate

    if best_text and best_score >= 0.15:
        return True, best_text
    return False, None


def _semantic_tokens(text: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in str(text or ""))
    tokens = {t for t in cleaned.split() if len(t) >= 3}
    return tokens


def _token_overlap(lhs: set[str], rhs: set[str]) -> float:
    if not lhs or not rhs:
        return 0.0
    return len(lhs.intersection(rhs)) / max(1, len(rhs))


def _infer_artifact_type_from_offer(offer_text: str) -> str | None:
    """Map offer text to an artifact_type string for D1/D2 consumption."""
    t = (offer_text or "").lower()
    for keywords, artifact_type in _OFFER_ARTIFACT_TYPE_MAP:
        if any(kw in t for kw in keywords):
            return artifact_type
    return None


# ---------------------------------------------------------------------------
# D1 — direct artifact-type request
# ---------------------------------------------------------------------------

# Maps artifact_type → keywords that indicate the CEO is directly requesting
# that output mode (not via an offer acceptance).
_REQUEST_ARTIFACT_TYPE_MAP: list[tuple[list[str], str]] = [
    (["board brief", "board materials", "board packet", "board prep",
      "full data-backed brief", "comprehensive brief", "working document",
      "working doc", "full document", "actual brief", "actual document",
      "board-ready", "board ready",
      # D1 fix: common variants the CEO uses when requesting a board presentation
      "board presentation", "presentation package", "executive dashboard",
      "slides", "slide deck", "presentation slides", "powerpoint",
      "powerpoint-style", "full board",
      # Board meeting prep requests ("board meeting next week / tomorrow")
      "board meeting", "for the board meeting", "board materials for"], "board_brief"),
    (["action plan", "action items", "owner names", "dollar impact",
      "next steps with owners", "numbered actions", "list with owners"], "action_plan"),
    (["draft email", "delegation email", "draft a delegation", "write an email",
      "send an email", "compose an email", "draft the email",
      # delegation scenario: "draft me an email", "draft an email for me to"
      "draft me an email", "write me an email", "an email to", "email draft to",
      "draft an email to", "draft an email", "outreach email", "immediate outreach email",
      "executive outreach email", "customer outreach email", "draft an outreach email",
      # executive communication: "draft the executive recovery response", "draft a response to"
      "executive recovery", "recovery response", "executive response",
      "draft the response", "draft a response", "draft the executive",
      "compose a response", "write the response", "write a response to",
      "draft the message", "compose the message"], "email"),
]

# Workflow types whose direct requests should never be reclassified as a
# report-generation artifact type.
_SCHEDULE_LIKE_WORKFLOWS = {
    WorkflowType.SCHEDULE_PLANNING,
    WorkflowType.MEETING_PREP,
    WorkflowType.CALENDAR_BRIEFING,
    WorkflowType.MORNING_BRIEF,
    WorkflowType.WEEKLY_RECAP,
    WorkflowType.DOCUMENT_EXPLANATION,
}


def detect_artifact_type_from_request(message: str) -> str | None:
    """
    Detect artifact_type from the CEO's direct request text.
    Returns None when no specific artifact intent is found (defaults to "report").

    Generic schedule/calendar requests are explicitly guarded — they cannot
    be reclassified as board_brief, action_plan, or email.
    """
    visible_message = extract_visible_request_text(message)
    semantic_plan = plan_request(visible_message, has_attachments=False)
    if semantic_plan.direct_workflow in _SCHEDULE_LIKE_WORKFLOWS:
        return None

    parsed_intent = parse_turn_intent(
        message=visible_message,
        previous_state=None,
        artifact_context=None,
    )
    semantic_artifact_type = str(parsed_intent.deliverable.artifact_type or "").strip()
    if semantic_artifact_type:
        return semantic_artifact_type

    m = visible_message.lower()
    for keywords, artifact_type in _REQUEST_ARTIFACT_TYPE_MAP:
        if any(kw in m for kw in keywords):
            return artifact_type
    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class ArtifactModeResult:
    """Merged output of C1→C2→D1 artifact-mode detection."""
    artifact_type: Optional[str] = None
    # Default file delivery format for this artifact_type, looked up from
    # _ARTIFACT_TYPE_MODALITY.  None means let _select_output_modality decide.
    output_modality: Optional[str] = None
    executing_action_offer: Optional[str] = None
    # True when C1 matched (offer acceptance from prior response).
    offer_accepted: bool = False
    # True when D1 matched (direct request in message text).
    direct_request: bool = False
    # True when workflow should be pinned to report_generation.
    pin_to_report: bool = False

    def as_meta_dict(self) -> dict:
        """Flat dict suitable for merging into runner extra_metadata."""
        result: dict = {}
        if self.executing_action_offer is not None:
            result["executing_action_offer"] = self.executing_action_offer
        if self.artifact_type is not None:
            result["artifact_type"] = self.artifact_type
        if self.output_modality is not None:
            result["pinned_output_modality"] = self.output_modality
        return result


def resolve_artifact_mode(
    *,
    prev_response: dict | None,
    ceo_message: str,
    resolved_deliverable_artifact_type: str | None = None,
) -> ArtifactModeResult:
    """
    Run the full C1→D1 detection pipeline and return a merged ArtifactModeResult.

    Precedence:
      C1. Offer acceptance → infer artifact_type from offer text (C2).
      D1. Direct request → always run; email overrides everything else.
    """
    result = ArtifactModeResult()

    # C1/C2: offer acceptance
    accepted, offer_text = detect_action_offer_acceptance(prev_response, ceo_message)
    if accepted:
        result.offer_accepted = True
        result.executing_action_offer = offer_text
        inferred = _infer_artifact_type_from_offer(offer_text or "")
        if inferred:
            result.artifact_type = inferred
            result.output_modality = _ARTIFACT_TYPE_MODALITY.get(inferred)
        result.pin_to_report = True

    # D1: direct artifact-type request
    direct_type = detect_artifact_type_from_request(ceo_message)
    if direct_type:
        result.direct_request = True
        current_type = result.artifact_type
        # Email explicitly requested → always override.
        if direct_type == "email" or not current_type:
            result.artifact_type = direct_type
            result.output_modality = _ARTIFACT_TYPE_MODALITY.get(direct_type)
        # Board brief or action plan → pin to report_generation.
        if direct_type in ("board_brief", "action_plan"):
            result.pin_to_report = True

    # Deliverable fallback: if intent state already has an artifact_type and
    # neither C1 nor D1 produced one, carry it forward.
    if resolved_deliverable_artifact_type and not result.artifact_type:
        result.artifact_type = resolved_deliverable_artifact_type
        result.output_modality = _ARTIFACT_TYPE_MODALITY.get(resolved_deliverable_artifact_type)

    return result

from __future__ import annotations

from typing import Any

# Maps logical capability names to the provider names that satisfy them.
_CAPABILITY_PROVIDERS: dict[str, frozenset[str]] = {
    "email_send": frozenset({"gmail", "outlook"}),
    "calendar_write": frozenset({"google_calendar", "outlook_calendar"}),
}


class CapabilityGuard:
    """
    Post-generation deterministic filter.
    Removes question_options entries that claim capabilities not available
    in the current runtime's connected provider set.

    Rules:
    - Only inspects action_offer entries. Clarification entries are always kept.
    - Within an action_offer, strips individual options whose capability_requires
      contains any capability not satisfied by connected_providers.
    - If all options of a question are stripped, the question is removed too.
    - Options with empty capability_requires are never stripped.
    """

    def strip(
        self,
        question_options: list[dict[str, Any]],
        connected_providers: set[str],
    ) -> list[dict[str, Any]]:
        result = []
        for entry in question_options:
            offer_type = entry.get("offer_type")
            if offer_type != "action_offer":
                result.append(entry)
                continue

            filtered_options = [
                opt for opt in (entry.get("options") or [])
                if self._option_is_satisfiable(opt, connected_providers)
            ]
            if not filtered_options:
                continue  # drop the whole question — all options were stripped

            result.append({**entry, "options": filtered_options})
        return result

    def _option_is_satisfiable(
        self,
        option: dict[str, Any],
        connected_providers: set[str],
    ) -> bool:
        required = option.get("capability_requires") or []
        for capability in required:
            satisfying = _CAPABILITY_PROVIDERS.get(capability, frozenset())
            if not satisfying.intersection(connected_providers):
                return False
        return True

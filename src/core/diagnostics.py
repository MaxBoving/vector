from typing import Any, Dict, List, Optional

class DiagnosticReporter:
    """Tracks and explains the 'Why' behind agentic decisions, specifically for questions and offers."""
    
    def __init__(self):
        self.trace: List[Dict[str, Any]] = []

    def log_decision(self, *, decision_type: str, item_label: str, reason: str, context_snapshot: Dict[str, Any]):
        """Logs a decision with the reasoning and the state of the world at that moment."""
        self.trace.append({
            "decision_type": decision_type,
            "item_label": item_label,
            "reason": reason,
            "context_snapshot": context_snapshot
        })

    def get_summary(self) -> str:
        """Returns a human-readable trace of the agent's recent decisions."""
        if not self.trace:
            return "No diagnostic trace available."
        lines = ["=== AGENT DECISION DIAGNOSTIC ==="]
        for entry in self.trace:
            lines.append(f"[{entry['decision_type'].upper()}] '{entry['item_label']}': {entry['reason']}")
        return "\n".join(lines)

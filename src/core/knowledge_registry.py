from typing import Any, Dict, List, Tuple
from src.finance.metrics import CANONICAL_METRIC_DEFINITIONS

# Source authority rankings for trust calculation
SOURCE_TRUST_RANKS: Dict[str, int] = {
    "audited_finance_doc": 100,
    "weekly_finance_checkin": 90,
    "company_state": 80,
    "internal_finance_memo": 70,
    "retrieved_document": 60,
    "historical_artifact": 55,
    "artifact": 50,
    "state": 45,
    "derived_metric": 25,
    "fallback": 10,
}

# Canonical Metric Schema derived from Finance domain models
# This is passed to the LLM to ensure semantic mapping follows the project's data model.
CANONICAL_METRIC_SCHEMA: List[Dict[str, Any]] = [
    {
        "metric": d.metric_label,
        "key": d.metric_key,
        "category": d.category,
        "unit": d.unit,
        "description": d.description
    }
    for d in CANONICAL_METRIC_DEFINITIONS
]

# Semantic hints for the LLM (formerly hardcoded markers)
# These are used as EXAMPLES in prompts, not as brittle string-matching rules.
SEMANTIC_TOPIC_HINTS: Dict[str, List[str]] = {
    "action_plan": [
        "action plan", "specific actions", "detailed breakdown", "what do we do next",
        "immediately", "detailed plan", "timeline", "responsible parties", "be specific"
    ],
    "escalation": [
        "escalation", "customer issue", "at-risk customer", "recovery plan", "Apex Health", "Redwood Systems"
    ],
    "hiring": [
        "candidate", "hire", "interview", "onboard", "comp package", "panel feedback", "VP Engineering"
    ],
    "finance": [
        "aws", "cloud spend", "variance", "burn", "runway", "board packet", "budget"
    ]
}

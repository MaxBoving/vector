from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlmodel import Session

from src.api.schemas import AssistantMessageResponse
from src.core.database import engine
from src.core.models import SessionInteraction

_UNSET = object()


def serialize_interaction_response(response: AssistantMessageResponse) -> str:
    metadata = dict(response.metadata or {})
    preserved_metadata = {
        key: metadata.get(key)
        for key in ("original_query", "envelope_version", "semantic_source")
        if metadata.get(key) is not None
    }
    summary_payload = {
        "conversation_id": response.conversation_id,
        "workflow_type": response.workflow_type,
        "response_type": response.response_type,
        "status": response.status,
        "answer": {
            "title": response.answer.title,
            "summary": response.answer.summary,
        },
        "artifacts": [
            {
                "artifact_type": artifact.artifact_type,
                "label": artifact.label,
            }
            for artifact in (response.artifacts or [])
        ],
        "trust": {
            "question_options": [
                option.model_dump(mode="json")
                if hasattr(option, "model_dump")
                else option
                for option in ((response.trust.question_options or []) if response.trust else [])
            ],
            "semantic_context": (
                response.trust.semantic_context.model_dump(mode="json")
                if response.trust and getattr(response.trust, "semantic_context", None) is not None
                else None
            ),
        },
        "metadata": preserved_metadata,
    }
    return json.dumps(summary_payload)


def persist_interaction_state(
    interaction_id: int | None,
    *,
    status: str | None = None,
    current_stage: str | None = None,
    response: str | None = None,
    intent: str | None = None,
    gate_type: Any = _UNSET,
    context: Any = _UNSET,
) -> None:
    if interaction_id is None:
        return

    with Session(engine) as session:
        interaction = session.get(SessionInteraction, interaction_id)
        if not interaction:
            return

        if status is not None:
            interaction.status = status
        if current_stage is not None:
            interaction.current_stage = current_stage
        if response is not None:
            interaction.response = response
        if intent is not None:
            interaction.intent = intent
        if gate_type is not _UNSET:
            interaction.gate_type = gate_type
        if context is not _UNSET:
            interaction.missing_data_context = json.dumps(context) if context else None

        interaction.last_updated = datetime.now().isoformat()
        session.add(interaction)
        session.commit()

from __future__ import annotations

import json

from src.api.schemas import (
    AnswerPayload,
    AssistantMessageResponse,
    SemanticContext,
    SourceRef,
    TrustMetadata,
)
from src.workflows.interaction_persistence import serialize_interaction_response


def test_serialize_interaction_response_preserves_semantic_context() -> None:
    response = AssistantMessageResponse(
        conversation_id="conv:test",
        message_id="msg:test",
        workflow_type="morning_brief",
        response_type="brief",
        status="completed",
        answer=AnswerPayload(title="Morning Brief", summary="Brief summary"),
        trust=TrustMetadata(
            semantic_context=SemanticContext(
                topic="P&L 2025-10",
                importance=86.0,
                date="2026-05-12T09:00:00-07:00",
                families=["finance"],
                source_ids=["thread-1"],
                confidence_score=0.32,
                evidence_state="sparse",
                missing_context=["Calendar context is thin."],
                needs_more_info=True,
                summary="4 important threads need attention.",
                workflow_type="morning_brief",
                response_type="brief",
            ),
        ),
        sources=[
            SourceRef(
                source_id="thread-1",
                title="P&L 2025-10",
                type="state",
            )
        ],
    )

    summary = json.loads(serialize_interaction_response(response))

    assert summary["trust"]["semantic_context"]["topic"] == "P&L 2025-10"
    assert summary["trust"]["semantic_context"]["needs_more_info"] is True

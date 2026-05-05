"""Assistant query, conversation, project, and demo seeding routes."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from pydantic import BaseModel

from src.api.routes.auth import get_current_user
from src.api.schemas import (
    ApprovalResolutionRequest,
    AssistantMessageResponse,
    AssistantQueryRequest,
    ConversationListItemResponse,
    ConversationResponse,
    ConversationUpdateRequest,
    DemoCompanyProfileSeedResponse,
    DemoExecutiveContextSeedRequest,
    DemoExecutiveContextSeedResponse,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
)


class QuickActionRequest(BaseModel):
    prompt: str
    intent: str  # "draft-reply" | "summarize-email"


class QuickActionResponse(BaseModel):
    result: str
    intent: str
from src.core.database import (
    append_interaction_to_conversation,
    create_assistant_conversation,
    create_assistant_project,
    delete_assistant_conversation,
    delete_assistant_project,
    engine,
    get_assistant_conversation as get_assistant_conversation_record,
    get_assistant_project,
    get_interactions_for_conversation,
    get_unassigned_session_history,
    list_assistant_conversations,
    list_assistant_projects,
    save_object,
    update_assistant_conversation,
    update_assistant_project,
)
from src.core.models import SessionInteraction, User
from src.assistant.agent import AgenticAssistant
from src.assistant.approval import execute_approval, reject_approval
from src.api.schemas import AnswerPayload, TrustMetadata
from src.workflows.read_model import (
    build_assistant_message_response,
    build_conversation_response,
    get_default_conversation_id,
)
from src.workflows.demo_company_profile import seed_demo_company_profile_env as _seed_demo_company_profile_env
from src.workflows.demo_executive_context import seed_demo_executive_context as _seed_demo_executive_context

_agent = AgenticAssistant()

router = APIRouter(tags=["assistant"])


async def generate_native_assistant_response(
    payload: AssistantQueryRequest,
    interaction: SessionInteraction,
    current_user: User,
) -> AssistantMessageResponse:
    return await _agent.handle(
        payload=payload,
        interaction=interaction,
        current_user=current_user,
    )


@router.post("/assistant/query", response_model=AssistantMessageResponse)
async def assistant_query(
    payload: AssistantQueryRequest,
    current_user: User = Depends(get_current_user),
):
    conversation = get_assistant_conversation_record(current_user.ceo_id, payload.conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    interaction = SessionInteraction(ceo_id=current_user.ceo_id, query=payload.message, status="PENDING")
    saved_interaction = save_object(interaction)
    append_interaction_to_conversation(
        current_user.ceo_id,
        payload.conversation_id,
        saved_interaction.id,
        query=payload.message,
    )

    try:
        result = await generate_native_assistant_response(payload, saved_interaction, current_user)
        with Session(engine) as session:
            stored = session.get(SessionInteraction, saved_interaction.id)
            if stored:
                stored.status = "COMPLETED"
                stored.response = result.answer.summary
                stored.last_updated = datetime.now().isoformat()
                session.add(stored)
                session.commit()
        return result
    except Exception as exc:
        with Session(engine) as session:
            stored_interaction = session.get(SessionInteraction, saved_interaction.id)
            if stored_interaction:
                stored_interaction.status = "FAILED"
                stored_interaction.response = str(exc)
                stored_interaction.last_updated = datetime.now().isoformat()
                session.add(stored_interaction)
                session.commit()
        raise HTTPException(status_code=500, detail=f"Assistant workflow failed: {str(exc)}") from exc


@router.get("/assistant/messages/{interaction_id}", response_model=AssistantMessageResponse)
async def get_assistant_message(interaction_id: int, current_user: User = Depends(get_current_user)):
    with Session(engine) as session:
        interaction = session.get(SessionInteraction, interaction_id)
        if not interaction or interaction.ceo_id != current_user.ceo_id:
            raise HTTPException(status_code=404, detail="Interaction not found")

    return build_assistant_message_response(interaction, current_user=current_user)


@router.post("/assistant/messages/{interaction_id}/resolve", response_model=AssistantMessageResponse)
async def resolve_assistant_message(
    interaction_id: int,
    resolution: ApprovalResolutionRequest,
    current_user: User = Depends(get_current_user),
):
    conversation_id = resolution.conversation_id
    if not conversation_id:
        # Recover conversation_id from the interaction → conversation mapping
        for conv in list_assistant_conversations(current_user.ceo_id):
            if interaction_id in (conv.interaction_ids or []):
                conversation_id = conv.conversation_id
                break
    if not conversation_id:
        raise HTTPException(status_code=404, detail="Conversation not found for this interaction.")
    try:
        if resolution.decision == "approve":
            action_result = execute_approval(
                ceo_id=current_user.ceo_id,
                conversation_id=conversation_id,
                interaction_id=interaction_id,
            )
            summary = f"Done. {action_result.get('executed', 'Action')} executed."
        else:
            reject_approval(
                ceo_id=current_user.ceo_id,
                conversation_id=conversation_id,
                interaction_id=interaction_id,
            )
            summary = "Got it, action cancelled."
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return AssistantMessageResponse(
        conversation_id=conversation_id,
        message_id=str(interaction_id),
        workflow_type="conversational",
        response_type="conversational",
        status="completed",
        answer=AnswerPayload(title="", summary=summary, sections=[]),
        trust=TrustMetadata(),
    )


@router.get("/assistant/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_assistant_conversation_route(conversation_id: str, current_user: User = Depends(get_current_user)):
    conversation = get_assistant_conversation_record(current_user.ceo_id, conversation_id)
    if conversation:
        interactions = get_interactions_for_conversation(current_user.ceo_id, conversation.interaction_ids or [])
    elif conversation_id == get_default_conversation_id(current_user.ceo_id):
        interactions = get_unassigned_session_history(current_user.ceo_id)
    else:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    return build_conversation_response(
        conversation_id=conversation_id or get_default_conversation_id(current_user.ceo_id),
        interactions=interactions,
        current_user=current_user,
    )


@router.get("/assistant/conversations", response_model=list[ConversationListItemResponse])
async def list_conversations(current_user: User = Depends(get_current_user)):
    conversations = list_assistant_conversations(current_user.ceo_id)
    items: list[ConversationListItemResponse] = []

    unassigned_interactions = get_unassigned_session_history(current_user.ceo_id)
    if unassigned_interactions:
        latest_unassigned = unassigned_interactions[-1]
        items.append(
            ConversationListItemResponse(
                conversation_id=get_default_conversation_id(current_user.ceo_id),
                title="Earlier conversation",
                pinned=False,
                archived=False,
                created_at=unassigned_interactions[0].timestamp,
                updated_at=latest_unassigned.last_updated,
                message_count=len(unassigned_interactions),
                latest_query=latest_unassigned.query,
                latest_timestamp=latest_unassigned.timestamp,
            )
        )

    for conversation in conversations:
        interactions = get_interactions_for_conversation(current_user.ceo_id, conversation.interaction_ids or [])
        latest_interaction = interactions[-1] if interactions else None
        items.append(
            ConversationListItemResponse(
                conversation_id=conversation.conversation_id,
                title=conversation.title,
                pinned=conversation.pinned,
                archived=conversation.archived,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                message_count=len(interactions),
                latest_query=latest_interaction.query if latest_interaction else None,
                latest_timestamp=latest_interaction.timestamp if latest_interaction else conversation.updated_at,
            )
        )

    items.sort(key=lambda item: item.updated_at or item.created_at or "", reverse=True)
    return items


@router.post("/assistant/conversations", response_model=ConversationListItemResponse)
async def create_conversation(current_user: User = Depends(get_current_user)):
    conversation = create_assistant_conversation(current_user.ceo_id)
    return ConversationListItemResponse(
        conversation_id=conversation.conversation_id,
        title=conversation.title,
        pinned=conversation.pinned,
        archived=conversation.archived,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=0,
        latest_query=None,
        latest_timestamp=None,
    )


@router.delete("/assistant/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, current_user: User = Depends(get_current_user)):
    deleted = delete_assistant_conversation(current_user.ceo_id, conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"ok": True}


@router.patch("/assistant/conversations/{conversation_id}", response_model=ConversationListItemResponse)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    if payload.title is None and payload.pinned is None and payload.archived is None:
        raise HTTPException(status_code=400, detail="No conversation update was provided.")

    title = payload.title.strip() if payload.title is not None else None
    if payload.title is not None and not title:
        raise HTTPException(status_code=400, detail="Conversation title is required.")

    conversation = update_assistant_conversation(
        current_user.ceo_id,
        conversation_id,
        title=title[:120] if title is not None else None,
        pinned=payload.pinned,
        archived=payload.archived,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    interactions = get_interactions_for_conversation(current_user.ceo_id, conversation.interaction_ids or [])
    latest_interaction = interactions[-1] if interactions else None
    return ConversationListItemResponse(
        conversation_id=conversation.conversation_id,
        title=conversation.title,
        pinned=conversation.pinned,
        archived=conversation.archived,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        message_count=len(interactions),
        latest_query=latest_interaction.query if latest_interaction else None,
        latest_timestamp=latest_interaction.timestamp if latest_interaction else conversation.updated_at,
    )


@router.get("/assistant/projects", response_model=list[ProjectResponse])
async def list_projects(current_user: User = Depends(get_current_user)):
    return [
        ProjectResponse(
            project_id=project.project_id,
            name=project.name,
            description=project.description,
            created_at=project.created_at,
            updated_at=project.updated_at,
            document_ids=project.document_ids or [],
            conversation_ids=project.conversation_ids or [],
        )
        for project in list_assistant_projects(current_user.ceo_id)
    ]


@router.post("/assistant/projects", response_model=ProjectResponse)
async def create_project(
    payload: ProjectCreateRequest,
    current_user: User = Depends(get_current_user),
):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Project name is required.")
    project = create_assistant_project(
        current_user.ceo_id,
        name=payload.name.strip(),
        description=payload.description.strip() if payload.description else None,
    )
    return ProjectResponse(
        project_id=project.project_id,
        name=project.name,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
        document_ids=project.document_ids or [],
        conversation_ids=project.conversation_ids or [],
    )


@router.patch("/assistant/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    payload: ProjectUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    project = get_assistant_project(current_user.ceo_id, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    updated = update_assistant_project(
        current_user.ceo_id,
        project_id,
        name=payload.name.strip() if payload.name is not None else None,
        description=payload.description.strip() if payload.description is not None else None,
        document_ids=payload.document_ids,
        conversation_ids=payload.conversation_ids,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found.")

    return ProjectResponse(
        project_id=updated.project_id,
        name=updated.name,
        description=updated.description,
        created_at=updated.created_at,
        updated_at=updated.updated_at,
        document_ids=updated.document_ids or [],
        conversation_ids=updated.conversation_ids or [],
    )


@router.delete("/assistant/projects/{project_id}")
async def remove_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
):
    deleted = delete_assistant_project(current_user.ceo_id, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found.")
    return {"ok": True}


@router.post("/assistant/quick", response_model=QuickActionResponse)
async def quick_action(
    request: QuickActionRequest,
    current_user: User = Depends(get_current_user),
):
    from src.core.llm import LLMClient

    system_prompts = {
        "draft-reply": (
            "You are drafting a concise, professional email reply on behalf of a CEO. "
            "Keep the reply direct, decisive, and under 120 words. Do not include subject line or sign-off placeholder."
        ),
        "summarize-email": (
            "You are summarizing an email thread for a CEO. "
            "Return a tight 3-5 bullet list of: the core ask, key deadlines, decisions needed, and any blockers. "
            "Each bullet must be a concrete action or fact — no filler."
        ),
        "report-deeper": (
            "You are expanding a section of an executive report for a CEO. "
            "Add 3-5 sentences of deeper analysis, supporting evidence, or implications. "
            "Be specific and substantive — no generic filler. Write in flowing prose."
        ),
        "finance-drill": (
            "You are a CFO-level analyst drilling into a financial signal for a CEO. "
            "In 3-4 bullet points explain: what is driving this, what the risk or opportunity is, "
            "and one concrete action the CEO should consider. Be specific with numbers where possible."
        ),
        "finance-scenario": (
            "You are running a scenario analysis for a CEO. "
            "Given the metric described, outline: a base case, a downside case, and an upside case — "
            "each in one sentence with a concrete numerical impact estimate. Then give one recommended action."
        ),
        "schedule-prep": (
            "You are preparing a meeting prep brief for a CEO. "
            "Return: (1) the likely agenda in 2-3 bullet points, (2) the key decision or outcome needed, "
            "(3) 2-3 questions the CEO should ask, and (4) any risk or blocker to flag. "
            "Be specific and brief — this is a scan before a meeting, not a full memo."
        ),
        "draft-revise": (
            "You are revising an email draft for a CEO. "
            "Return only the revised body text — no subject line, no greeting label, no explanation. "
            "Apply the requested tone change while preserving the core message and intent."
        ),
    }
    system = system_prompts.get(request.intent, "You are a concise executive assistant.")
    llm = LLMClient(model="gpt-4o-mini")
    result = await llm.complete_async(request.prompt, system_prompt=system)
    return QuickActionResponse(result=result.strip(), intent=request.intent)


# ---------------------------------------------------------------------------
# Demo seeding routes
# ---------------------------------------------------------------------------

@router.post("/demo/executive-context/seed", response_model=DemoExecutiveContextSeedResponse)
async def seed_demo_executive_context_route(
    payload: DemoExecutiveContextSeedRequest,
    current_user: User = Depends(get_current_user),
):
    bundle = _seed_demo_executive_context(
        ceo_id=current_user.ceo_id,
        company_name=current_user.company_name,
        scenario=payload.scenario,
        anchor_date=payload.anchor_date,
    )

    return DemoExecutiveContextSeedResponse(
        scenario=bundle["scenario"],
        message="Demo executive inbox and calendar context seeded successfully.",
        seeded_email_threads=len(bundle["email_event"].get("ranked_threads", [])),
        seeded_calendar_events=len(bundle["calendar_event"].get("upcoming_events", [])),
        seeded_signals=len(bundle["signals"]),
        demo_account_email=bundle["demo_account_email"],
    )


@router.post("/demo/company-profile-env/seed", response_model=DemoCompanyProfileSeedResponse)
async def seed_demo_company_profile_env_route(
    payload: DemoExecutiveContextSeedRequest,
    current_user: User = Depends(get_current_user),
):
    bundle = _seed_demo_company_profile_env(
        ceo_id=current_user.ceo_id,
        company_name=current_user.company_name,
        scenario=payload.scenario,
        anchor_date=payload.anchor_date,
    )
    record = bundle["profile_record"]
    context_bundle = bundle["context_bundle"]
    return DemoCompanyProfileSeedResponse(
        scenario=bundle["scenario"],
        message="Demo company-profile environment seeded successfully.",
        company_name=record.company_name,
        readiness_summary=record.readiness_summary,
        authoritative_coverage_ratio=record.authoritative_coverage_ratio,
        seeded_email_threads=len(context_bundle["email_event"].get("ranked_threads", [])),
        seeded_calendar_events=len(context_bundle["calendar_event"].get("upcoming_events", [])),
        seeded_signals=len(context_bundle["signals"]),
        demo_account_email=context_bundle["demo_account_email"],
    )

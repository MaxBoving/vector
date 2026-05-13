"""Document upload/list, artifact preview/download, identity, and onboarding profile routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session

from src.api.routes.auth import get_current_user
from src.api.schemas import (
    ArtifactPreviewResponse,
    CompanyIdentityProfileResponse,
    CompanyProfileResponse,
    DocumentSummaryResponse,
    DocumentUploadResponse,
    WatcherPreferenceResponse,
    WatcherPreferenceUpdateRequest,
    WorkbookViewResponse,
)
from src.core.company_profile import CompanyProfile
from src.core.database import (
    engine,
    get_or_create_ceo_preferences,
    record_preference_signal,
    update_watcher_preferences,
)
from src.core.models import SessionInteraction, User, normalize_preferences_payload
from src.tools.artifact_tools import get_stage_artifact_path, read_stage_artifact, read_stage_artifact_metadata
from src.workflows.company_identity import get_or_build_company_identity_profile
from src.workflows.company_profile_intake import company_profile_identity_view, get_company_profile, upsert_company_profile
from src.workflows.document_ingestion import list_documents_for_user, upload_document_to_knowledge_base
from src.workflows.workbook_view import build_workbook_view_response

router = APIRouter(tags=["documents"])


class PreferenceSignalRequest(BaseModel):
    signal_type: str   # "output_format" | "framing" | "depth" | "response_style"
    value: str         # e.g. "personal_decision" | "board_presentation" | "operator" | "brief"


@router.get("/documents", response_model=list[DocumentSummaryResponse])
async def list_documents(current_user: User = Depends(get_current_user)):
    return list_documents_for_user(current_user)


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    title: str,
    purpose: str = "reference",
    identity_role: str | None = None,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    return await upload_document_to_knowledge_base(
        title,
        file,
        current_user,
        purpose=purpose,
        identity_role=identity_role,
    )


@router.get("/artifacts/{interaction_id}/{artifact_type}", response_model=ArtifactPreviewResponse)
async def get_artifact_preview(
    interaction_id: int,
    artifact_type: str,
    current_user: User = Depends(get_current_user),
):
    with Session(engine) as session:
        interaction = session.get(SessionInteraction, interaction_id)
        if not interaction or interaction.ceo_id != current_user.ceo_id:
            raise HTTPException(status_code=404, detail="Interaction not found")

    path = get_stage_artifact_path(interaction_id, current_user.ceo_id, artifact_type)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")

    preview_stage_map = {
        "executive_canvas": "canvas_preview",
        "report_docx": "report_docx_preview",
        "report_pptx": "report_pptx_preview",
        "analysis_xlsx": "analysis_spec",
    }
    preview_format_map = {
        "executive_canvas": "html",
        "report_docx": "md",
        "report_pptx": "md",
        "analysis_xlsx": "json",
    }
    content = (
        read_stage_artifact(interaction_id, current_user.ceo_id, preview_stage_map.get(artifact_type, artifact_type))
        if artifact_type in preview_stage_map
        else read_stage_artifact(interaction_id, current_user.ceo_id, artifact_type)
    )
    if not content:
        raise HTTPException(status_code=404, detail="Artifact preview not found")

    format_map = {
        "planning": "json",
        "synthesizer": "md",
        "executive_canvas": "html",
        "canvas_preview": "html",
        "report_docx": "md",
        "report_docx_preview": "md",
        "report_pptx": "md",
        "report_pptx_preview": "md",
        "analysis_xlsx": "json",
        "analysis_spec": "json",
    }
    label_map = {
        "planning": "Planner Execution",
        "synthesizer": "Synthesizer",
        "executive_canvas": "Executive Canvas",
        "canvas_preview": "Canvas Preview",
        "report_docx": "Executive Memo",
        "report_docx_preview": "Board Memo Preview",
        "report_pptx": "Executive Deck",
        "report_pptx_preview": "Board Deck Preview",
        "analysis_xlsx": "Analysis Workbook",
        "analysis_spec": "Analysis Spec",
    }

    return ArtifactPreviewResponse(
        artifact_id=f"interaction:{interaction_id}:{artifact_type}",
        artifact_type=artifact_type,
        label=label_map.get(artifact_type, artifact_type.replace("_", " ").title()),
        format=preview_format_map.get(artifact_type, format_map.get(artifact_type)),
        status="generated",
        content=content,
        metadata={
            key: value
            for key, value in read_stage_artifact_metadata(
                interaction_id,
                current_user.ceo_id,
                preview_stage_map.get(artifact_type, artifact_type),
            ).items()
            if key in {"theme_id", "template_id", "presentation_version"}
        },
    )


@router.get("/artifacts/{interaction_id}/{artifact_type}/download")
async def download_artifact(
    interaction_id: int,
    artifact_type: str,
    current_user: User = Depends(get_current_user),
):
    with Session(engine) as session:
        interaction = session.get(SessionInteraction, interaction_id)
        if not interaction or interaction.ceo_id != current_user.ceo_id:
            raise HTTPException(status_code=404, detail="Interaction not found")

    path = get_stage_artifact_path(interaction_id, current_user.ceo_id, artifact_type)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")

    media_type = {
        "executive_canvas": "text/html; charset=utf-8",
        "report_docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "report_pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "analysis_xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(artifact_type, "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/artifacts/{interaction_id}/analysis_xlsx/view", response_model=WorkbookViewResponse)
async def get_workbook_view(
    interaction_id: int,
    current_user: User = Depends(get_current_user),
):
    with Session(engine) as session:
        interaction = session.get(SessionInteraction, interaction_id)
        if not interaction or interaction.ceo_id != current_user.ceo_id:
            raise HTTPException(status_code=404, detail="Interaction not found")

    return build_workbook_view_response(interaction_id=interaction_id, ceo_id=current_user.ceo_id)


@router.get("/identity/profile", response_model=CompanyIdentityProfileResponse)
async def get_identity_profile(current_user: User = Depends(get_current_user)):
    onboarding_profile = get_company_profile(current_user.ceo_id)
    if onboarding_profile:
        profile_data = company_profile_identity_view(onboarding_profile.profile_data)
        return CompanyIdentityProfileResponse(
            company_name=profile_data.get("company_name") or current_user.company_name,
            has_examples=bool(profile_data.get("has_examples")),
            tone=profile_data.get("tone"),
            preferred_formats=profile_data.get("preferred_formats", []) or [],
            section_patterns=profile_data.get("section_patterns", []) or [],
            reference_titles=profile_data.get("reference_titles", []) or [],
        )

    profile = get_or_build_company_identity_profile(current_user)
    profile_data = profile.profile_data or {}
    return CompanyIdentityProfileResponse(
        company_name=current_user.company_name,
        has_examples=bool(profile_data.get("has_examples")),
        tone=profile_data.get("tone"),
        preferred_formats=profile_data.get("preferred_formats", []) or [],
        section_patterns=profile_data.get("section_patterns", []) or [],
        reference_titles=profile_data.get("reference_titles", []) or [],
    )


@router.put("/onboarding/company-profile", response_model=CompanyProfileResponse)
async def upsert_company_profile_route(
    payload: CompanyProfile,
    current_user: User = Depends(get_current_user),
):
    company_name = (
        payload.company_identity.operating_name
        or payload.company_identity.legal_name
        or current_user.company_name
    )
    record = upsert_company_profile(
        ceo_id=current_user.ceo_id,
        company_name=company_name,
        profile=payload,
    )
    return CompanyProfileResponse(
        ceo_id=current_user.ceo_id,
        company_name=record.company_name,
        last_updated=record.last_updated,
        readiness_summary=record.readiness_summary,
        authoritative_coverage_ratio=record.authoritative_coverage_ratio,
        profile_data=record.profile_data,
    )


@router.get("/onboarding/company-profile", response_model=CompanyProfileResponse)
async def get_company_profile_route(current_user: User = Depends(get_current_user)):
    record = get_company_profile(current_user.ceo_id)
    if not record:
        raise HTTPException(status_code=404, detail="Company profile not found.")
    return CompanyProfileResponse(
        ceo_id=current_user.ceo_id,
        company_name=record.company_name,
        last_updated=record.last_updated,
        readiness_summary=record.readiness_summary,
        authoritative_coverage_ratio=record.authoritative_coverage_ratio,
        profile_data=record.profile_data,
    )


@router.get("/identity/watcher-preferences", response_model=WatcherPreferenceResponse)
async def get_watcher_preferences(current_user: User = Depends(get_current_user)):
    preferences = normalize_preferences_payload(get_or_create_ceo_preferences(current_user.ceo_id))
    return WatcherPreferenceResponse(
        priority_senders=preferences.get("priority_senders", []) or [],
        priority_domains=preferences.get("priority_domains", []) or [],
        ignored_senders=preferences.get("ignored_senders", []) or [],
        ignored_domains=preferences.get("ignored_domains", []) or [],
    )


@router.post("/identity/watcher-preferences", response_model=WatcherPreferenceResponse)
async def set_watcher_preferences(
    payload: WatcherPreferenceUpdateRequest,
    current_user: User = Depends(get_current_user),
):
    preferences = normalize_preferences_payload(
        update_watcher_preferences(
            current_user.ceo_id,
            action=payload.action,
            sender=payload.sender,
            domain=payload.domain,
        )
    )
    return WatcherPreferenceResponse(
        priority_senders=preferences.get("priority_senders", []) or [],
        priority_domains=preferences.get("priority_domains", []) or [],
        ignored_senders=preferences.get("ignored_senders", []) or [],
        ignored_domains=preferences.get("ignored_domains", []) or [],
    )


@router.post("/identity/preference-signal", status_code=204)
async def record_preference_signal_endpoint(
    payload: PreferenceSignalRequest,
    current_user: User = Depends(get_current_user),
):
    """Called when the CEO makes a clarification choice (e.g. 'My decision' vs 'Board presentation').
    Accumulates frequency counts in learned_defaults so the system can skip the question
    once a preference is established.
    """
    record_preference_signal(
        current_user.ceo_id,
        signal_type=payload.signal_type,
        value=payload.value,
    )

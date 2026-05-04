from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PendingAction(BaseModel):
    action_id: str
    action_type: str
    status: str = "ready"
    target_entity: Optional[str] = None
    target_person: Optional[str] = None
    source_artifact_id: Optional[str] = None
    source_interaction_id: Optional[int] = None
    proposal: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


def resolve_action_reference(
    *,
    message: str,
    pending_actions: List[Dict[str, Any]] | None,
) -> Dict[str, Any]:
    lowered = (message or "").lower()
    actions = [PendingAction(**item) for item in (pending_actions or []) if isinstance(item, dict)]
    if not actions:
        return {}

    wants_email_execution = any(token in lowered for token in ("send that email", "send the email", "send this email", "actually send", "send it"))
    wants_schedule_execution = any(token in lowered for token in ("schedule that call", "schedule the call", "book that call", "get that call on", "schedule it"))

    entity_hints = {token for token in ("alphasystems", "redwood", "apex", "sarah chen", "rachel lim") if token in lowered}

    candidates = actions
    if entity_hints:
        matched = [
            item
            for item in actions
            if any(
                hint in f"{(item.target_entity or '').lower()} {(item.target_person or '').lower()} {(item.proposal.get('subject') or '').lower()} {(item.proposal.get('title') or '').lower()}"
                for hint in entity_hints
            )
        ]
        if matched:
            candidates = matched

    if wants_email_execution:
        for item in reversed(candidates):
            if item.action_type == "send_email" and item.status in {"ready", "drafted"}:
                return item.model_dump(mode="json")
    if wants_schedule_execution:
        for item in reversed(candidates):
            if item.action_type == "schedule_call" and item.status in {"ready", "drafted"}:
                return item.model_dump(mode="json")
    return {}


def merge_pending_actions(
    *,
    existing: List[Dict[str, Any]] | None,
    new_actions: List[Dict[str, Any]] | None = None,
    updates: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    actions = [PendingAction(**item) for item in (existing or []) if isinstance(item, dict)]
    index = {item.action_id: item for item in actions}

    for raw in (new_actions or []):
        try:
            item = PendingAction(**raw)
        except Exception:
            continue
        index[item.action_id] = item

    for raw in (updates or []):
        action_id = str((raw or {}).get("action_id") or "")
        if not action_id or action_id not in index:
            continue
        current = index[action_id].model_dump(mode="json")
        current.update({k: v for k, v in raw.items() if v is not None})
        current["updated_at"] = datetime.now().isoformat()
        index[action_id] = PendingAction(**current)

    merged = list(index.values())
    merged.sort(key=lambda item: item.updated_at)
    return [item.model_dump(mode="json") for item in merged[-12:]]


def infer_pending_actions_from_response(
    *,
    response: Any,
    interaction_id: int | None,
) -> List[Dict[str, Any]]:
    if response is None:
        return []
    title = str(getattr(getattr(response, "answer", None), "title", "") or "")
    sections = list(getattr(getattr(response, "answer", None), "sections", None) or [])
    artifacts = list(getattr(response, "artifacts", None) or [])
    section_text = "\n".join(
        [
            title,
            str(getattr(getattr(response, "answer", None), "summary", "") or ""),
            *[str(item) for section in sections for item in (getattr(section, "items", None) or [])],
        ]
    )
    actions: List[Dict[str, Any]] = []

    email_section = next((section for section in sections if str(getattr(section, "label", "")).lower() == "email draft"), None)
    if email_section and "subject:" in section_text.lower():
        items = list(getattr(email_section, "items", None) or [])
        subject = ""
        body = ""
        if items:
            first = str(items[0])
            if first.lower().startswith("subject:"):
                subject = first.split(":", 1)[1].strip()
            if len(items) > 1:
                body = str(items[1])
        target_person = _extract_target_person(title, body)
        target_entity = _extract_target_entity(title, body, subject)
        artifact_id = next((getattr(item, "artifact_id", None) for item in artifacts if getattr(item, "artifact_type", None) == "report_docx"), None)
        actions.append(
            PendingAction(
                action_id=f"send_email:{interaction_id or 'pending'}:{target_entity or target_person or 'unknown'}",
                action_type="send_email",
                status="ready",
                target_entity=target_entity,
                target_person=target_person,
                source_artifact_id=artifact_id,
                source_interaction_id=interaction_id,
                proposal={
                    "to": "",
                    "subject": subject,
                    "body": body,
                    "cc": [],
                },
            ).model_dump(mode="json")
        )
    return actions


def _extract_target_person(title: str, body: str) -> Optional[str]:
    match = re.search(r"to\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", title)
    if match:
        return match.group(1)
    body_match = re.search(r"hi\s+([A-Z][a-z]+)", body)
    if body_match:
        return body_match.group(1)
    return None


def _extract_target_entity(title: str, body: str, subject: str) -> Optional[str]:
    combined = f"{title} {body} {subject}"
    for label in ("AlphaSystems", "Redwood Systems", "Apex Health"):
        if label.lower() in combined.lower():
            return label
    return None

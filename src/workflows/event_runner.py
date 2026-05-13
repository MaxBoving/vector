import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.api.schemas import (
    AssistantMessageResponse,
    AssistantQueryRequest,
    CalendarBriefingRequest,
    EmailIngestionRequest,
    MorningBriefRequest,
)
from src.core.database import save_object
from src.core.database import get_world_reference_datetime
from src.core.models import IncomingSignal, SessionInteraction, User
from src.integrations.providers import ProviderIntegrationError, fetch_calendar_event, fetch_email_event
from src.runtime.engine import RuntimeEngine
from src.tools.registry import ToolRegistry, build_default_tool_registry
from src.workflows.calendar_briefing import CALENDAR_BRIEFING_WORKFLOW
from src.workflows.email_ingestion import EMAIL_INGESTION_WORKFLOW
from src.workflows.event_payloads import build_watch_event_payload
from src.workflows.morning_brief import MORNING_BRIEF_WORKFLOW
from src.workflows.planning_types import RequestPlan, RetrievalPlan, RetrievalSourceRequest
from src.workflows.types import WorkflowType

logger = logging.getLogger(__name__)


class EventWorkflowRunner:
    def __init__(self, tools: ToolRegistry | None = None) -> None:
        self.tools = tools or build_default_tool_registry()
        self.runtime = RuntimeEngine(self.tools)

    def _safe_fetch_email_event(self, ceo_id: str) -> dict:
        try:
            return fetch_email_event(ceo_id)
        except ProviderIntegrationError:
            return {}

    def _safe_fetch_calendar_event(self, ceo_id: str) -> dict:
        try:
            return fetch_calendar_event(ceo_id)
        except ProviderIntegrationError:
            return {}

    def _create_interaction(self, *, ceo_id: str, query: str) -> SessionInteraction:
        return save_object(
            SessionInteraction(
                ceo_id=ceo_id,
                query=query,
                status="PENDING",
            )
        )

    def _build_query_payload(self, *, ceo_id: str, message: str, conversation_suffix: str) -> AssistantQueryRequest:
        return AssistantQueryRequest(
            message=message,
            conversation_id=f"conv:{ceo_id}:{conversation_suffix}",
        )

    def _morning_brief_message(
        self,
        request: MorningBriefRequest,
        *,
        ceo_id: str,
        reference_dt: datetime | None = None,
    ) -> tuple[str, RequestPlan]:
        try:
            timezone = ZoneInfo(request.timezone)
        except ZoneInfoNotFoundError:
            timezone = datetime.now().astimezone().tzinfo
        reference_dt = reference_dt or get_world_reference_datetime(ceo_id, tzinfo_value=timezone)
        if reference_dt is None:
            reference_dt = datetime.now(timezone) if timezone else datetime.now().astimezone()
        logger.info(
            "event_runner.morning_brief anchor ceo_id=%s reference_dt=%s timezone=%s requested_scheduled_for=%s",
            ceo_id,
            reference_dt.isoformat() if reference_dt else None,
            timezone,
            request.scheduled_for,
        )
        try:
            scheduled_dt = datetime.fromisoformat(request.scheduled_for.replace("Z", "+00:00"))
            if scheduled_dt.tzinfo is None:
                scheduled_dt = scheduled_dt.replace(tzinfo=timezone)
            if timezone:
                scheduled_dt = scheduled_dt.astimezone(timezone)
        except ValueError:
            message = "Prepare the scheduled executive brief"
            logger.info(
                "event_runner.morning_brief fallback ceo_id=%s message=%r target_date=%s horizon=%s",
                ceo_id,
                message,
                reference_dt.date() if reference_dt else None,
                "today",
            )
            return message, self._morning_brief_request_plan(
                target_date=reference_dt.date() if reference_dt else datetime.now().date(),
                time_horizon="today",
                target_label="Today",
                reference_dt=reference_dt,
            )

        reference_date = reference_dt.date()
        target_date = scheduled_dt.date()
        delta_days = (target_date - reference_date).days
        weekday_label = scheduled_dt.strftime("%A")
        if delta_days == 0:
            message = "Prepare a morning brief for today"
            plan = self._morning_brief_request_plan(
                target_date=target_date,
                time_horizon="today",
                target_label="Today",
                reference_dt=reference_dt,
            )
            logger.info(
                "event_runner.morning_brief resolved ceo_id=%s message=%r target_date=%s horizon=%s target_label=%s",
                ceo_id,
                message,
                target_date,
                "today",
                "Today",
            )
            return message, plan
        if delta_days == 1:
            message = "Prepare a morning brief for tomorrow"
            plan = self._morning_brief_request_plan(
                target_date=target_date,
                time_horizon="tomorrow",
                target_label="Tomorrow",
                reference_dt=reference_dt,
            )
            logger.info(
                "event_runner.morning_brief resolved ceo_id=%s message=%r target_date=%s horizon=%s target_label=%s",
                ceo_id,
                message,
                target_date,
                "tomorrow",
                "Tomorrow",
            )
            return message, plan
        if 0 < delta_days <= 7:
            label = f"{weekday_label} this week"
            message = f"Prepare a morning brief for {label}"
            plan = self._morning_brief_request_plan(
                target_date=target_date,
                time_horizon="this_week",
                target_label=label,
                reference_dt=reference_dt,
            )
            logger.info(
                "event_runner.morning_brief resolved ceo_id=%s message=%r target_date=%s horizon=%s target_label=%s",
                ceo_id,
                message,
                target_date,
                "this_week",
                label,
            )
            return message, plan
        label = f"Next {weekday_label}"
        message = f"Prepare a morning brief for {label.lower()}"
        plan = self._morning_brief_request_plan(
            target_date=target_date,
            time_horizon="next_week",
            target_label=label,
            reference_dt=reference_dt,
        )
        logger.info(
            "event_runner.morning_brief resolved ceo_id=%s message=%r target_date=%s horizon=%s target_label=%s",
            ceo_id,
            message,
            target_date,
            "next_week",
            label,
        )
        return message, plan

    def _morning_brief_request_plan(
        self,
        *,
        target_date: date,
        time_horizon: str,
        target_label: str,
        reference_dt: datetime | None,
    ) -> RequestPlan:
        return RequestPlan(
            mode="direct_workflow",
            target_workflow=WorkflowType.MORNING_BRIEF,
            direct_workflow=WorkflowType.MORNING_BRIEF,
            needed_context_sources=["email", "calendar", "signals"],
            retrieval_plan=RetrievalPlan(
                sources=[
                    RetrievalSourceRequest(source="email", required=True, priority=0, rationale="Morning brief needs inbox evidence."),
                    RetrievalSourceRequest(source="calendar", required=True, priority=1, rationale="Morning brief needs the day's meetings."),
                    RetrievalSourceRequest(source="signals", required=True, priority=2, rationale="Morning brief needs recent executive signals."),
                ],
                time_horizon=time_horizon,  # type: ignore[arg-type]
                target_date=target_date,
                target_label=target_label,
                rationale="Scheduled morning brief trigger derived from the current local datetime.",
                planner_version="v1",
                execution_model="carrier_workflow_with_planner_execution",
            ),
            time_horizon=time_horizon,
            target_date=target_date,
            target_label=target_label,
            rationale="Scheduled morning brief trigger derived from the current local datetime.",
            planning_metadata={
                "planner_version": "v1",
                "execution_model": "carrier_workflow_with_planner_execution",
                "request_surface": "morning_brief",
                "reference_source": "current_local_datetime",
                "reference_dt": reference_dt.isoformat() if reference_dt else None,
            },
        )

    async def run_email_ingestion(self, request: EmailIngestionRequest, current_user: User) -> AssistantMessageResponse:
        logger.info(
            "event_runner.email_ingestion ceo_id=%s sender=%r subject=%r",
            current_user.ceo_id,
            request.sender,
            request.subject,
        )
        fetched_event_payload: dict | None = None
        if not request.sender or not request.subject or not request.content:
            fetched_event_payload = self._safe_fetch_email_event(current_user.ceo_id)
            request = EmailIngestionRequest(
                **{
                    key: value
                    for key, value in {
                        **fetched_event_payload,
                        **request.model_dump(exclude_none=True),
                    }.items()
                    if key in EmailIngestionRequest.model_fields
                }
            )
        ranked_threads = list((fetched_event_payload or {}).get("ranked_threads", []))
        primary_signal = save_object(
            IncomingSignal(
                ceo_id=current_user.ceo_id,
                source="Email",
                sender=request.sender,
                subject=request.subject,
                content=request.content,
                importance=str((fetched_event_payload or {}).get("importance", "MEDIUM")).upper(),
                strategic_concepts=list((fetched_event_payload or {}).get("importance_reasons", [])),
                talking_points=[
                    f"{thread.get('subject', 'Thread')} ({str(thread.get('importance_level', 'medium')).upper()})"
                    for thread in ranked_threads[:3]
                ],
            )
        )
        related_signal_ids = [primary_signal.id]
        for thread in ranked_threads[1:3]:
            signal = save_object(
                IncomingSignal(
                    ceo_id=current_user.ceo_id,
                    source="Email",
                    sender=str(thread.get("latest_sender") or "Unknown sender"),
                    subject=str(thread.get("subject") or "Recent thread"),
                    content="\n\n".join(
                        str(message.get("body_preview") or "").strip()
                        for message in thread.get("messages", [])[:2]
                        if str(message.get("body_preview") or "").strip()
                    )
                    or str(thread.get("snippet") or ""),
                    importance=str(thread.get("importance_level", "medium")).upper(),
                    strategic_concepts=list(thread.get("importance_reasons", [])),
                    talking_points=[str(thread.get("category") or "operations")],
                )
            )
            related_signal_ids.append(signal.id)
        interaction = self._create_interaction(
            ceo_id=current_user.ceo_id,
            query=f"Summarize important inbox threads: {request.subject}",
        )
        payload = self._build_query_payload(
            ceo_id=current_user.ceo_id,
            message=f"Summarize important inbox threads from {request.sender}: {request.subject}",
            conversation_suffix="email",
        )
        return await self.runtime.run(
            definition=EMAIL_INGESTION_WORKFLOW,
            payload=payload,
            interaction=interaction,
            current_user=current_user,
            extra_metadata={
                "event_payload": {
                    "signal_id": primary_signal.id,
                    "signal_ids": related_signal_ids,
                    "sender": request.sender,
                    "subject": request.subject,
                    "content": request.content,
                    "thread_id": request.thread_id,
                    "labels": request.labels,
                    "received_at": request.received_at or datetime.now().isoformat(),
                    "importance": (fetched_event_payload or {}).get("importance"),
                    "importance_score": (fetched_event_payload or {}).get("importance_score"),
                    "importance_reasons": (fetched_event_payload or {}).get("importance_reasons", []),
                    "ranked_threads": ranked_threads,
                }
            },
        )

    async def run_calendar_briefing(self, request: CalendarBriefingRequest, current_user: User) -> AssistantMessageResponse:
        if not request.meeting_id or not request.title or not request.starts_at:
            request = CalendarBriefingRequest(
                **{**self._safe_fetch_calendar_event(current_user.ceo_id), **request.model_dump(exclude_none=True)}
            )
        logger.info(
            "event_runner.calendar_briefing ceo_id=%s meeting_id=%r title=%r starts_at=%r",
            current_user.ceo_id,
            request.meeting_id,
            request.title,
            request.starts_at,
        )
        interaction = self._create_interaction(
            ceo_id=current_user.ceo_id,
            query=f"Prepare calendar briefing: {request.title}",
        )
        payload = self._build_query_payload(
            ceo_id=current_user.ceo_id,
            message=f"Prepare a meeting brief for {request.title}",
            conversation_suffix="calendar",
        )
        return await self.runtime.run(
            definition=CALENDAR_BRIEFING_WORKFLOW,
            payload=payload,
            interaction=interaction,
            current_user=current_user,
            extra_metadata={
                "event_payload": request.model_dump(),
            },
        )

    async def run_morning_brief(self, request: MorningBriefRequest, current_user: User) -> AssistantMessageResponse:
        email_event = self._safe_fetch_email_event(current_user.ceo_id)
        calendar_event = self._safe_fetch_calendar_event(current_user.ceo_id)
        try:
            timezone = ZoneInfo(request.timezone)
        except ZoneInfoNotFoundError:
            timezone = datetime.now().astimezone().tzinfo
        reference_dt = get_world_reference_datetime(current_user.ceo_id, tzinfo_value=timezone)
        message, request_plan = self._morning_brief_message(request, ceo_id=current_user.ceo_id, reference_dt=reference_dt)
        logger.info(
            "event_runner.run_morning_brief ceo_id=%s message=%r target_date=%s horizon=%s",
            current_user.ceo_id,
            message,
            request_plan.target_date,
            request_plan.time_horizon,
        )
        interaction = self._create_interaction(
            ceo_id=current_user.ceo_id,
            query=message,
        )
        payload = self._build_query_payload(
            ceo_id=current_user.ceo_id,
            message=message,
            conversation_suffix="morning",
        )
        return await self.runtime.run(
            definition=MORNING_BRIEF_WORKFLOW,
            payload=payload,
            interaction=interaction,
            current_user=current_user,
            extra_metadata={
                "event_payload": build_watch_event_payload(
                    email_event=email_event,
                    calendar_event=calendar_event,
                    message=message,
                    request_plan=request_plan,
                    extra_payload=request.model_dump(),
                ),
            },
        )

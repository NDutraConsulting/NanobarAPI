"""Service layer for the blog domain -- each `NanobarAPIService` subclass is one distinct business
operation, mirroring `tests/test_validator_gate.py`'s `GreetController`/`GreetGate` worked
example scaled to a real multi-route domain. Backed by `app/crud/blog_crud.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.repositories.blog_repository import AppointmentRepository, NotificationRepository, PostRepository
from app.libraries.blog_serializer import appointment_to_dict, notification_to_dict, post_to_dict
from app.models.blog_model import PostStateFields
from nanobar_api.eventbus.dispatch import NanobarEventBus
from nanobar_api.framework.nanobar_api_service import NanobarAPIService, ServiceResult, ServiceResultBody
from nanobar_api.telemetry import NanobarTelemetry


@dataclass
class CreatePostRequest:
    title: str
    body: str
    #: ISO 8601 datetime string, parsed here rather than by `nanobar_api.validation.parse()`
    #: (which doesn't understand `datetime` fields) -- `None` means "save as a draft."
    scheduled_at: str | None = None


class CreatePostService(NanobarAPIService):
    """Creates a post as a draft, or -- when `scheduled_at` is given -- validates and applies
    the `draft -> scheduled` transition through `PostStateFields`' declared state machine (not
    a bare field assignment) before persisting."""

    def __init__(self, telemetry: NanobarTelemetry, repository: PostRepository) -> None:
        super().__init__(telemetry)
        self.repository = repository

    def handle(self, request: CreatePostRequest) -> ServiceResult:
        post = self.repository.create(title=request.title, body=request.body)
        if request.scheduled_at is not None:
            state_machine = PostStateFields.state_machine_for(
                "status", post.status)
            state_machine.transition_to("scheduled")
            updated = self.repository.update_status(
                post.id, status=state_machine.state, scheduled_at=datetime.fromisoformat(
                    request.scheduled_at)
            )
            assert updated is not None
            post = updated
        return ServiceResult(
            status="success",
            result=ServiceResultBody(type="object", data=post_to_dict(
                post), msg_summary="post created"),
        )


@dataclass
class UpdatePostRequest:
    post_id: str
    title: str
    body: str


class UpdatePostService(NanobarAPIService):
    """Overwrites an existing post's title/body in place -- no state-machine involvement (unlike
    `CreatePostService`'s `scheduled_at` branch), since editing content doesn't change `status`.
    Same not-found shape as `MarkNotificationReadService`: a missing id is a real business
    outcome a validator can't catch by shape alone."""

    def __init__(self, telemetry: NanobarTelemetry, repository: PostRepository) -> None:
        super().__init__(telemetry)
        self.repository = repository

    def handle(self, request: UpdatePostRequest) -> ServiceResult:
        post = self.repository.update_content(
            request.post_id, title=request.title, body=request.body)
        if post is None:
            return ServiceResult(
                status="error",
                result=ServiceResultBody(
                    type="object", data=None, msg_summary=f"post {request.post_id!r} not found"),
            )
        return ServiceResult(
            status="success",
            result=ServiceResultBody(type="object", data=post_to_dict(
                post), msg_summary="post updated"),
        )


@dataclass
class BookAppointmentRequest:
    name: str
    email: str
    note: str = ""


class BookAppointmentService(NanobarAPIService):
    """Books the appointment, then publishes a `domain.appointments` event rather than doing
    anything about the notification itself -- `app/services/blog_notification_callback.py`'s
    `NanobarCallback` subscriber does that asynchronously. The one real, non-test consumer of
    `NanobarEventBus` in this codebase."""

    def __init__(
        self, telemetry: NanobarTelemetry, repository: AppointmentRepository, event_bus: NanobarEventBus
    ) -> None:
        super().__init__(telemetry)
        self.repository = repository
        self.event_bus = event_bus

    def handle(self, request: BookAppointmentRequest) -> ServiceResult:
        appointment = self.repository.create(
            name=request.name, email=request.email, note=request.note)
        self.event_bus.publish(
            "domain.appointments",
            {
                "appointment_id": appointment.id,
                "name": appointment.name,
                "email": appointment.email,
                "note": appointment.note,
            },
        )
        return ServiceResult(
            status="success",
            result=ServiceResultBody(
                type="object", data=appointment_to_dict(appointment), msg_summary="appointment booked"
            ),
        )


@dataclass
class MarkNotificationReadRequest:
    notification_id: str


class MarkNotificationReadService(NanobarAPIService):
    """The one operation here with a real business-outcome failure a validator can't catch by
    shape alone (the id is a well-formed string either way) -- genuinely needs a DB lookup."""

    def __init__(self, telemetry: NanobarTelemetry, repository: NotificationRepository) -> None:
        super().__init__(telemetry)
        self.repository = repository

    def handle(self, request: MarkNotificationReadRequest) -> ServiceResult:
        notification = self.repository.mark_read(request.notification_id)
        if notification is None:
            return ServiceResult(
                status="error",
                result=ServiceResultBody(
                    type="object", data=None, msg_summary=f"notification {request.notification_id!r} not found"
                ),
            )
        return ServiceResult(
            status="success",
            result=ServiceResultBody(
                type="object", data=notification_to_dict(notification), msg_summary="notification marked read"
            ),
        )

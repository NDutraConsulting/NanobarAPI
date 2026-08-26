"""`NanobarCallback` subscriber turning a booked appointment into a `Notification` row -- the
first real, non-test consumer of `NanobarEventBus`/`NanobarCallback` in this codebase. Runs on
`NanobarEventBus`'s own background thread (`run_forever()`), so booking a request never blocks
on this -- exactly the "so the fanouts do not need an await" framing
`nanobar_type_taxonomy_and_expected_coverage_buildplan-with-tasks.md`/the Worker-Domain plan
already established for `IntegrationTestWorker`, demonstrated here first with a real caller.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from nanobar_api.eventbus.dispatch import NanobarCallback
from nanobar_api.eventbus.events import Event

from .blog_repositories import NotificationRepository


class AppointmentNotificationCallback(NanobarCallback):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def handle(self, event: Event) -> Any:
        session = self.session_factory()
        try:
            payload = event.payload
            notification = NotificationRepository(session).create(
                kind="appointment_booked",
                message=f"New appointment request from {payload.get('name')} ({payload.get('email')})",
            )
            return {"notification_id": notification.id}
        finally:
            session.close()

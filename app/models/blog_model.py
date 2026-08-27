"""SQLAlchemy ORM models for the blog domain -- the first real `NanobarRepository`/
`NanobarORMWrapper` consumer in this codebase (both have sat unused since the Service-Domain
build; `NanobarRepository.__init__` has always required a real SQLAlchemy `Session`, and nothing
until now ever constructed one). `Post`/`Appointment`/`Notification` are plain SQLAlchemy
declarative models.

**`Post` has a companion `NanobarModel` subclass (`PostStateFields`) declaring its
state-machine-governed field and idempotency contract, consulted by the service layer at the
point of a transition -- not mixed directly into the ORM row.** Found via live verification, not
assumed: `NanobarModel(ABC)` uses `abc.ABCMeta`, which conflicts with SQLAlchemy's
`DeclarativeBase` metaclass (`class Post(Base, NanobarModel)` raises `TypeError: metaclass
conflict` immediately). Composition, not inheritance, is the only way to use both on one entity.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from nanobar_api.models import NanobarModel


class Base(DeclarativeBase):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


POST_STATUSES = ("draft", "scheduled", "published")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _new_id("post"))
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PostStateFields(NanobarModel):
    """Declares `Post.status`'s valid transitions (`draft -> scheduled -> published`, any
    declared state to any other per `NanobarModel`'s own flat-graph limitation) and its
    idempotency contract -- consulted by `PostService.schedule_post()`/`publish_post()`, not
    mixed into the ORM row (see module docstring)."""

    monitored_state_fields = {"status": POST_STATUSES}
    idempotent_fields = ("id",)


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _new_id("appt"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _new_id("notif"))
    kind: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

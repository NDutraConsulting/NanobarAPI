"""Repository layer for the blog domain -- `NanobarAPIRepository`'s first real usage in this
codebase (see `app/models/blog_model.py`'s module docstring for why it took this long: nothing
before this domain ever constructed a real SQLAlchemy `Session` to hand it).
"""

from __future__ import annotations

from datetime import datetime

from app.models.blog_model import Appointment, Notification, Post
from nanobar_api.framework.nanobar_api_repository import NanobarAPIRepository


class PostRepository(NanobarAPIRepository):
    def cache_key(self, *args: object, **kwargs: object) -> str:
        return f"post:{args[0]}" if args else "post:all"

    def create(self, *, title: str, body: str) -> Post:
        post = Post(title=title, body=body)
        self.session.add(post)
        self.session.commit()
        self.session.refresh(post)
        return post

    def seed(self, *, id: str, title: str, body: str) -> Post:
        """Inserts a `Post` row with an *explicit* id -- unlike `create()` (which always mints a
        fresh random id via the model's own `default=`), regression-brick replay-time seeding
        (`app/db/blog_seeders.py`) needs to reconstruct a specific, already-known id a captured
        brick depends on, not create a new unrelated one. Not part of the normal create/read/
        update pipeline -- callers own their own idempotency (checking `get()` first); this
        method always inserts, matching every other repository `create()`'s own unconditional-add
        shape rather than silently no-opping on a collision.
        """
        post = Post(id=id, title=title, body=body)
        self.session.add(post)
        self.session.commit()
        self.session.refresh(post)
        return post

    def list_all(self) -> list[Post]:
        return list(self.session.query(Post).order_by(Post.created_at.desc()).all())

    def list_published(self) -> list[Post]:
        return list(
            self.session.query(Post).filter(Post.status == "published").order_by(
                Post.published_at.desc()).all()
        )

    def get(self, post_id: str) -> Post | None:
        cached = self.get_cached(post_id)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        post = self.session.get(Post, post_id)
        if post is not None:
            self.set_cached(post, post_id)
        return post

    def update_status(
        self,
        post_id: str,
        *,
        status: str,
        scheduled_at: datetime | None = None,
        published_at: datetime | None = None,
    ) -> Post | None:
        post = self.session.get(Post, post_id)
        if post is None:
            return None
        post.status = status
        if scheduled_at is not None:
            post.scheduled_at = scheduled_at
        if published_at is not None:
            post.published_at = published_at
        self.session.commit()
        self.session.refresh(post)
        self.invalidate(post_id)
        return post

    def update_content(self, post_id: str, *, title: str, body: str) -> Post | None:
        post = self.session.get(Post, post_id)
        if post is None:
            return None
        post.title = title
        post.body = body
        self.session.commit()
        self.session.refresh(post)
        self.invalidate(post_id)
        return post

    def list_due_for_publish(self, *, now: datetime) -> list[Post]:
        return list(self.session.query(Post).filter(Post.status == "scheduled", Post.scheduled_at <= now).all())

    def delete(self, post_id: str) -> None:
        """Hard delete -- the blog domain has no soft-delete concept for posts (unlike `Nanobar`/
        `RegressionBrick`). Only real call site is replay-time teardown
        (`app/db/blog_seeders.py`'s `seed_post_for_update()`), removing a row *it* seeded into
        the shadow db once a replay is done with it -- a no-op if `post_id` doesn't exist, so a
        caller doesn't need its own existence check first.
        """
        post = self.session.get(Post, post_id)
        if post is None:
            return
        self.session.delete(post)
        self.session.commit()
        self.invalidate(post_id)


class AppointmentRepository(NanobarAPIRepository):
    def cache_key(self, *args: object, **kwargs: object) -> str:
        return f"appointment:{args[0]}" if args else "appointment:all"

    def create(self, *, name: str, email: str, note: str = "") -> Appointment:
        appointment = Appointment(name=name, email=email, note=note)
        self.session.add(appointment)
        self.session.commit()
        self.session.refresh(appointment)
        return appointment


class NotificationRepository(NanobarAPIRepository):
    def cache_key(self, *args: object, **kwargs: object) -> str:
        return f"notification:{args[0]}" if args else "notification:all"

    def create(self, *, kind: str, message: str) -> Notification:
        notification = Notification(kind=kind, message=message)
        self.session.add(notification)
        self.session.commit()
        self.session.refresh(notification)
        return notification

    def list_all(self) -> list[Notification]:
        return list(self.session.query(Notification).order_by(Notification.created_at.desc()).all())

    def mark_read(self, notification_id: str) -> Notification | None:
        notification = self.session.get(Notification, notification_id)
        if notification is None:
            return None
        notification.read = True
        self.session.commit()
        self.session.refresh(notification)
        return notification

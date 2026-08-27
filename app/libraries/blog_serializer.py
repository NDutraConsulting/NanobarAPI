"""ORM row -> plain JSON-able dict, shared by `app/services/blog_service.py` (service response
payloads) and the plain read-only handlers in `app/admin/app/routes.py`/`app/api/routes/blog.py`.
"""

from __future__ import annotations

from typing import Any

from app.models.blog_model import Appointment, Notification, Post


def post_to_dict(post: Post) -> dict[str, Any]:
    return {
        "id": post.id,
        "title": post.title,
        "body": post.body,
        "status": post.status,
        "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at is not None else None,
        "published_at": post.published_at.isoformat() if post.published_at is not None else None,
        "created_at": post.created_at.isoformat(),
    }


def appointment_to_dict(appointment: Appointment) -> dict[str, Any]:
    return {
        "id": appointment.id,
        "name": appointment.name,
        "email": appointment.email,
        "note": appointment.note,
        "requested_at": appointment.requested_at.isoformat(),
    }


def notification_to_dict(notification: Notification) -> dict[str, Any]:
    return {
        "id": notification.id,
        "kind": notification.kind,
        "message": notification.message,
        "created_at": notification.created_at.isoformat(),
        "read": notification.read,
    }

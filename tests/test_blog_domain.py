"""Integration tests for the blog/booking demo domain (Tier 3 Phase 3) -- real app, real
SQLAlchemy ORM, real `NanobarEventBus`, real `NanobarValidatorGate`/`NanobarController`/
`NanobarService` pipeline. `demo/` isn't part of `scripts/coverage`'s 100%-branch gate (see
`pyproject.toml`'s `[tool.coverage.run]` `source_pkgs`), but this project's established practice
(`test_demo_dashboard.py`) is to test demo code substantively regardless -- this file follows
that same bar for the new blog domain.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from demo.dashboard.app import build_app
from demo.dashboard.blog_publisher_worker import PostPublisherThread
from nanobar_api.admin_auth import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME


@pytest.fixture
def blog_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "blog.db")


@pytest.fixture
def client(tmp_path: Path, blog_db_path: str) -> Iterator[TestClient]:
    """`with TestClient(app) as test_client` -- not just `TestClient(app)` -- is required, not
    stylistic: only entering the context manager fires the ASGI lifespan startup event, which is
    what actually starts `NanobarEventBus.run_forever()`'s background thread. Without it, a
    published `domain.appointments` event sits in the queue forever uncomsumed, and any test
    waiting for a resulting notification hangs until its own poll loop gives up. Found via live
    verification -- the first version of this fixture didn't use `with` and every
    notification-dependent test below failed silently that way.
    """
    app = build_app(
        db_path=str(tmp_path / "regression_bricks.db"),
        events_db_path=str(tmp_path / "events.db"),
        admin_db_path=str(tmp_path / "admin.db"),
        blog_db_path=blog_db_path,
    )
    with TestClient(app) as test_client:
        test_client.get("/admin/login")
        test_client.headers["x-nanobar-csrf-token"] = test_client.cookies["nanobar_csrftoken"]
        login_response = test_client.post(
            "/admin/login", json={"username": DEFAULT_ADMIN_USERNAME, "password": DEFAULT_ADMIN_PASSWORD}
        )
        assert login_response.status_code == 200
        yield test_client


# ------------------------------------------------------------------------- posts: create/list ---


def test_create_draft_post(client: TestClient) -> None:
    response = client.post("/admin/app/api/posts", json={"title": "Hello", "body": "World"})

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data["title"] == "Hello"
    assert data["status"] == "draft"
    assert data["scheduled_at"] is None
    assert data["published_at"] is None


def test_create_scheduled_post_applies_declared_state_transition(client: TestClient) -> None:
    scheduled_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    response = client.post(
        "/admin/app/api/posts", json={"title": "Later", "body": "Body", "scheduled_at": scheduled_at}
    )

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data["status"] == "scheduled"
    assert data["scheduled_at"] is not None


def test_create_post_missing_title_is_rejected(client: TestClient) -> None:
    response = client.post("/admin/app/api/posts", json={"body": "Body only"})

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_admin_list_posts_returns_drafts_and_published(client: TestClient) -> None:
    client.post("/admin/app/api/posts", json={"title": "A", "body": "a"})
    client.post("/admin/app/api/posts", json={"title": "B", "body": "b"})

    response = client.get("/admin/app/api/posts")

    assert response.status_code == 200
    assert len(response.json()["result"]["data"]) == 2


def test_edit_post_page_is_served(client: TestClient) -> None:
    created = client.post("/admin/app/api/posts", json={"title": "A", "body": "a"})
    post_id = created.json()["result"]["data"]["id"]

    response = client.get(f"/admin/app/posts/{post_id}/edit")

    assert response.status_code == 200
    assert "Edit post" in response.text


def test_admin_get_post_returns_a_draft(client: TestClient) -> None:
    created = client.post("/admin/app/api/posts", json={"title": "Draft", "body": "d"})
    post_id = created.json()["result"]["data"]["id"]

    response = client.get(f"/admin/app/api/posts/{post_id}")

    assert response.status_code == 200
    assert response.json()["result"]["data"]["title"] == "Draft"


def test_admin_get_post_unknown_id_is_404(client: TestClient) -> None:
    response = client.get("/admin/app/api/posts/does-not-exist")

    assert response.status_code == 404


def test_update_post_overwrites_title_and_body(client: TestClient) -> None:
    created = client.post("/admin/app/api/posts", json={"title": "Original", "body": "original body"})
    post_id = created.json()["result"]["data"]["id"]

    response = client.post(f"/admin/app/api/posts/{post_id}", json={"title": "Edited", "body": "edited body"})

    assert response.status_code == 200
    data = response.json()["result"]["data"]
    assert data["title"] == "Edited"
    assert data["body"] == "edited body"

    refetched = client.get("/admin/app/api/posts").json()["result"]["data"]
    assert refetched[0]["title"] == "Edited"


def test_update_post_unknown_id_is_404(client: TestClient) -> None:
    response = client.post("/admin/app/api/posts/does-not-exist", json={"title": "T", "body": "B"})

    assert response.status_code == 404


def test_update_post_missing_title_is_rejected(client: TestClient) -> None:
    created = client.post("/admin/app/api/posts", json={"title": "Original", "body": "b"})
    post_id = created.json()["result"]["data"]["id"]

    response = client.post(f"/admin/app/api/posts/{post_id}", json={"body": "b"})

    assert response.status_code == 400
    assert response.json()["status"] == "error"


def test_public_list_posts_excludes_drafts_and_scheduled(client: TestClient) -> None:
    client.post("/admin/app/api/posts", json={"title": "Draft", "body": "d"})
    scheduled_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    client.post("/admin/app/api/posts", json={"title": "Scheduled", "body": "s", "scheduled_at": scheduled_at})

    response = client.get("/api/posts")

    assert response.status_code == 200
    assert response.json()["result"]["data"] == []


def test_public_get_post_not_found_for_unpublished(client: TestClient) -> None:
    created = client.post("/admin/app/api/posts", json={"title": "Draft", "body": "d"})
    post_id = created.json()["result"]["data"]["id"]

    response = client.get(f"/api/posts/{post_id}")

    assert response.status_code == 404


def test_public_get_post_unknown_id_is_404(client: TestClient) -> None:
    response = client.get("/api/posts/does-not-exist")

    assert response.status_code == 404


def test_blog_index_and_post_pages_are_served(client: TestClient) -> None:
    index = client.get("/")
    assert index.status_code == 200
    assert "Blog · NanobarAPI Demo" in index.text

    detail = client.get("/posts/some-id")
    assert detail.status_code == 200
    assert "Post · NanobarAPI Demo" in detail.text


def test_book_appointment_page_is_served(client: TestClient) -> None:
    response = client.get("/book-appointment")

    assert response.status_code == 200
    assert "Book an appointment" in response.text


def test_admin_app_dashboard_page_is_served(client: TestClient) -> None:
    response = client.get("/admin/app/dashboard")

    assert response.status_code == 200
    assert "App admin" in response.text


# ------------------------------------------------------------- publisher sweep (time-based) ---


def test_publisher_flips_due_scheduled_posts_to_published(client: TestClient, blog_db_path: str) -> None:
    from demo.dashboard.blog_db import build_session_factory
    from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository

    scheduled_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()  # already due
    created = client.post("/admin/app/api/posts", json={"title": "Due now", "body": "b", "scheduled_at": scheduled_at})
    post_id = created.json()["result"]["data"]["id"]

    session_factory = build_session_factory(
        blog_db_path, repository=EventQueueRepository([ChannelConfig(name="snapshot")])
    )
    publisher = PostPublisherThread(session_factory)
    published_count = publisher.run_once()

    assert published_count == 1

    detail = client.get(f"/api/posts/{post_id}")
    assert detail.status_code == 200
    assert detail.json()["result"]["data"]["status"] == "published"


def test_publisher_run_once_is_zero_when_nothing_is_due(client: TestClient, blog_db_path: str) -> None:
    from demo.dashboard.blog_db import build_session_factory
    from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository

    session_factory = build_session_factory(
        blog_db_path, repository=EventQueueRepository([ChannelConfig(name="snapshot")])
    )
    publisher = PostPublisherThread(session_factory)

    assert publisher.run_once() == 0


def test_publisher_lifespan_starts_and_stops_cleanly(blog_db_path: str) -> None:
    from demo.dashboard.blog_db import build_session_factory
    from demo.dashboard.blog_publisher_worker import post_publisher_lifespan
    from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository

    session_factory = build_session_factory(
        blog_db_path, repository=EventQueueRepository([ChannelConfig(name="snapshot")])
    )
    publisher = PostPublisherThread(session_factory, poll_interval_s=0.05)

    async def run() -> None:
        async with post_publisher_lifespan(publisher):
            pass

    import anyio

    anyio.run(run)  # must not hang or raise -- proves stop()/thread.join() actually terminate it


# ---------------------------------------------------------------------- appointments/notify ---


def test_book_appointment_creates_a_notification_via_the_domain_event_bus(client: TestClient) -> None:
    response = client.post("/book-appointment", json={"name": "Ada Lovelace", "email": "ada@example.com"})

    assert response.status_code == 200
    assert response.json()["result"]["data"]["name"] == "Ada Lovelace"

    # The callback runs asynchronously on NanobarEventBus's own background thread -- poll briefly
    # rather than assuming a fixed sleep is always enough.
    for _ in range(50):
        notifications = client.get("/admin/app/api/notifications").json()["result"]["data"]
        if notifications:
            break
        time.sleep(0.05)
    else:
        pytest.fail("notification never appeared")

    assert "Ada Lovelace" in notifications[0]["message"]
    assert notifications[0]["read"] is False


def test_book_appointment_missing_email_is_rejected(client: TestClient) -> None:
    response = client.post("/book-appointment", json={"name": "Ada"})

    assert response.status_code == 400


def test_mark_notification_read(client: TestClient) -> None:
    client.post("/book-appointment", json={"name": "Ada", "email": "ada@example.com"})

    notification_id = None
    for _ in range(50):
        notifications = client.get("/admin/app/api/notifications").json()["result"]["data"]
        if notifications:
            notification_id = notifications[0]["id"]
            break
        time.sleep(0.05)
    assert notification_id is not None

    response = client.post(f"/admin/app/api/notifications/{notification_id}/read")

    assert response.status_code == 200
    assert response.json()["result"]["data"]["read"] is True


def test_mark_unknown_notification_read_is_404(client: TestClient) -> None:
    response = client.post("/admin/app/api/notifications/does-not-exist/read")

    assert response.status_code == 404


# ------------------------------------------------------------------------- auth gating ---


def test_unauthenticated_admin_app_dashboard_redirects_to_login(tmp_path: Path, blog_db_path: str) -> None:
    app = build_app(
        db_path=str(tmp_path / "b.db"),
        events_db_path=str(tmp_path / "e.db"),
        admin_db_path=str(tmp_path / "a.db"),
        blog_db_path=blog_db_path,
    )
    client = TestClient(app, follow_redirects=False)

    response = client.get("/admin/app/dashboard")

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/login"


def test_unauthenticated_admin_app_api_gets_401(tmp_path: Path, blog_db_path: str) -> None:
    app = build_app(
        db_path=str(tmp_path / "b.db"),
        events_db_path=str(tmp_path / "e.db"),
        admin_db_path=str(tmp_path / "a.db"),
        blog_db_path=blog_db_path,
    )
    client = TestClient(app, follow_redirects=False)

    response = client.get("/admin/app/api/posts")

    assert response.status_code == 401

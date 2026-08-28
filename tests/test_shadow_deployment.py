from __future__ import annotations

import pytest

from app.admin.nanobar.shadow_deployment import BLOG_SHADOW_PROFILE
from nanobar_api.bricks.shadow_profile import resolve_shadow_connection


def test_blog_shadow_profile_defaults_to_a_sibling_shadow_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BLOG_SHADOW_PROFILE.connection_secret_ref, raising=False)
    assert resolve_shadow_connection("/tmp/blog.db", profile=BLOG_SHADOW_PROFILE) == "/tmp/blog_shadow.db"

from __future__ import annotations

import pytest

from nanobar_api.bricks.shadow_profile import ShadowPersistenceProfile, resolve_shadow_connection


def test_resolve_shadow_connection_defaults_to_a_local_sibling_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NANOBAR_TEST_SHADOW_DB", raising=False)
    profile = ShadowPersistenceProfile(profile_id="postprod-sqlite", connection_secret_ref="NANOBAR_TEST_SHADOW_DB")

    assert resolve_shadow_connection("/tmp/blog.db", profile=profile) == "/tmp/blog_shadow.db"


def test_resolve_shadow_connection_uses_the_env_var_override_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NANOBAR_TEST_SHADOW_DB", "postgresql://user:pass@remote-host/shadow_db")
    profile = ShadowPersistenceProfile(profile_id="postprod-full", connection_secret_ref="NANOBAR_TEST_SHADOW_DB")

    assert resolve_shadow_connection("/tmp/blog.db", profile=profile) == "postgresql://user:pass@remote-host/shadow_db"

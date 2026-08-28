from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from nanobar_api.framework.nanobar_api_repository import (
    CacheBackend,
    InMemoryCacheBackend,
    NanobarAPIRepository,
    Repository,
)


class _OrderRepository(NanobarAPIRepository):
    def cache_key(self, *args: Any, **kwargs: Any) -> str:
        return f"order:{args[0]}"


def _session() -> Session:
    engine = create_engine("sqlite://")
    return Session(engine)


def test_cannot_instantiate_abstract_nanobar_repository_directly() -> None:
    session = _session()
    try:
        with pytest.raises(TypeError):
            NanobarAPIRepository(session)  # type: ignore[abstract]
    finally:
        session.close()


def test_defaults_to_in_memory_cache_backend() -> None:
    session = _session()
    try:
        repo = _OrderRepository(session)
        assert isinstance(repo._cache, InMemoryCacheBackend)
    finally:
        session.close()


def test_get_cached_returns_none_when_not_set() -> None:
    session = _session()
    try:
        repo = _OrderRepository(session)
        assert repo.get_cached("order-1") is None
    finally:
        session.close()


def test_set_cached_then_get_cached_round_trips() -> None:
    session = _session()
    try:
        repo = _OrderRepository(session)
        repo.set_cached({"id": "order-1", "total": 42}, "order-1")

        assert repo.get_cached("order-1") == {"id": "order-1", "total": 42}
    finally:
        session.close()


def test_invalidate_clears_cached_value() -> None:
    session = _session()
    try:
        repo = _OrderRepository(session)
        repo.set_cached({"id": "order-1"}, "order-1")

        repo.invalidate("order-1")

        assert repo.get_cached("order-1") is None
    finally:
        session.close()


def test_different_cache_keys_are_independent() -> None:
    session = _session()
    try:
        repo = _OrderRepository(session)
        repo.set_cached("A", "order-1")
        repo.set_cached("B", "order-2")

        assert repo.get_cached("order-1") == "A"
        assert repo.get_cached("order-2") == "B"
    finally:
        session.close()


def test_accepts_a_custom_cache_backend() -> None:
    class _RecordingCacheBackend:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, key: str) -> Any | None:
            self.calls.append(f"get:{key}")
            return None

        def set(self, key: str, value: Any) -> None:
            self.calls.append(f"set:{key}")

        def invalidate(self, key: str) -> None:
            self.calls.append(f"invalidate:{key}")

    backend: CacheBackend = _RecordingCacheBackend()
    session = _session()
    try:
        repo = _OrderRepository(session, cache=backend)
        repo.get_cached("order-1")
        repo.set_cached("x", "order-1")
        repo.invalidate("order-1")

        assert isinstance(backend, _RecordingCacheBackend)
        assert backend.calls == ["get:order:order-1", "set:order:order-1", "invalidate:order:order-1"]
    finally:
        session.close()


def test_repository_still_bare_usable() -> None:
    session = _session()
    try:
        repo = Repository(session)
        assert repo.session is session
    finally:
        session.close()

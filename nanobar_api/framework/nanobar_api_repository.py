"""`NanobarAPIRepository` — extends the existing `Repository` (unchanged, still usable bare) with
cache monitoring.

Per `.focusari/nanobar_ServiceDomain_abstract_class_buildplan-with-tasks.md` §2: **not** a
capture-producing boundary — the real DB-write/read capture mechanism is a single SQLAlchemy
engine-level event hook (`nanobar_api.orm.NanobarORMWrapper`), not per-repository manual
instrumentation ("capture no longer depends on every repository correctly emitting events by
hand"). This class is about caching only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol

from sqlalchemy.orm import Session


class Repository:
    def __init__(self, session: Session) -> None:
        self.session = session


class CacheBackend(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any) -> None: ...
    def invalidate(self, key: str) -> None: ...


class InMemoryCacheBackend:
    """Default. A plain process-local dict — no persistence, no cross-process sharing, the same
    two limitations `InMemoryRateLimitBackend` (`nanobar_api/access.py`) already documents for
    the identical reason. A Redis-backed implementation is named, not built — `redis` isn't a
    project dependency today; see this plan's Open Decision 1 (the four-way shared Redis
    decision, alongside `RateLimitBackend` and the backlogged admin doc's `SessionBackend`).
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def set(self, key: str, value: Any) -> None:
        self._store[key] = value

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)


class NanobarAPIRepository(Repository, ABC):
    def __init__(self, session: Session, cache: CacheBackend | None = None) -> None:
        super().__init__(session)
        self._cache = cache if cache is not None else InMemoryCacheBackend()

    @abstractmethod
    def cache_key(self, *args: Any, **kwargs: Any) -> str:
        """Deterministic cache key for a given call's arguments — same contract `functools.
        lru_cache` keys on, just explicit rather than auto-derived, since a cache key here also
        needs to be usable for `invalidate()` from call sites that don't have the cached value
        on hand to derive a key from automatically."""

    def get_cached(self, *args: Any, **kwargs: Any) -> Any | None:
        return self._cache.get(self.cache_key(*args, **kwargs))

    def set_cached(self, value: Any, *args: Any, **kwargs: Any) -> None:
        """Not in the source spec's own pseudocode (`get_cached`/`invalidate` only) — but
        without a way to populate the cache keyed the same way `get_cached`/`invalidate` derive
        their key, the cache could never actually be written to. A necessary completion, not an
        invention: same `cache_key(*args, **kwargs)` derivation as its siblings.
        """
        self._cache.set(self.cache_key(*args, **kwargs), value)

    def invalidate(self, *args: Any, **kwargs: Any) -> None:
        self._cache.invalidate(self.cache_key(*args, **kwargs))

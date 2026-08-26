from __future__ import annotations

import ipaddress

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from nanobar_api.access import (
    DEFAULT_INTRANET_CIDRS,
    InMemoryRateLimitBackend,
    IntranetOnlyMiddleware,
    RateLimitMiddleware,
    _resolve_client_address,
    private,
    public,
    rate_limited,
)


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _raw_ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse({"ok": True})(scope, receive, send)


def _app_with_middleware(*middleware: Middleware) -> Starlette:
    return Starlette(routes=[Route("/x", _ok)], middleware=list(middleware))


# --------------------------------------------------------------------- public()


def test_public_is_the_identity_element() -> None:
    assert public() == ()


# ------------------------------------------------------------- IntranetOnlyMiddleware


def test_private_returns_one_middleware_wrapping_intranet_only() -> None:
    tiers = private()

    assert len(tiers) == 1
    assert tiers[0].cls is IntranetOnlyMiddleware  # type: ignore[comparison-overlap]


def test_intranet_only_allows_default_cidr_client() -> None:
    app = _app_with_middleware(Middleware(IntranetOnlyMiddleware))
    client = TestClient(app, client=("10.1.2.3", 12345))

    response = client.get("/x")

    assert response.status_code == 200


def test_intranet_only_blocks_non_listed_client() -> None:
    app = _app_with_middleware(Middleware(IntranetOnlyMiddleware))
    client = TestClient(app, client=("8.8.8.8", 12345))

    response = client.get("/x")

    assert response.status_code == 403
    assert response.json()["status"] == "error"


def test_intranet_only_respects_custom_allowed_networks() -> None:
    app = _app_with_middleware(Middleware(IntranetOnlyMiddleware, allowed_networks=("203.0.113.0/24",)))

    allowed = TestClient(app, client=("203.0.113.5", 1)).get("/x")
    blocked = TestClient(app, client=("10.0.0.5", 1)).get("/x")

    assert allowed.status_code == 200
    assert blocked.status_code == 403


@pytest.mark.anyio
async def test_intranet_only_blocks_when_scope_has_no_client() -> None:
    middleware = IntranetOnlyMiddleware(_raw_ok_app, allowed_networks=DEFAULT_INTRANET_CIDRS)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "client": None, "method": "GET", "path": "/x", "headers": []}
    await middleware(scope, receive, send)

    assert sent[0]["status"] == 403


@pytest.mark.anyio
async def test_intranet_only_blocks_unparseable_client_address() -> None:
    middleware = IntranetOnlyMiddleware(_raw_ok_app)
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "client": ("testclient", 1), "method": "GET", "path": "/x", "headers": []}
    await middleware(scope, receive, send)

    assert sent[0]["status"] == 403


@pytest.mark.anyio
async def test_intranet_only_passes_through_non_http_scopes() -> None:
    calls: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope["type"])

    middleware = IntranetOnlyMiddleware(app)
    scope: Scope = {"type": "lifespan"}

    async def receive() -> Message:
        return {}

    async def send(message: Message) -> None:
        pass

    await middleware(scope, receive, send)

    assert calls == ["lifespan"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ------------------------------------------------------------- InMemoryRateLimitBackend


def test_in_memory_backend_allows_up_to_capacity_then_denies() -> None:
    backend = InMemoryRateLimitBackend()

    results = [backend.consume("k", capacity=3, rate=0.0) for _ in range(4)]

    assert results == [True, True, True, False]


def test_in_memory_backend_refills_over_time(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = InMemoryRateLimitBackend()
    times = iter([0.0, 0.0, 0.0, 10.0])
    monkeypatch.setattr("nanobar_api.access.time.monotonic", lambda: next(times))

    assert backend.consume("k", capacity=1, rate=1.0) is True  # t=0, starts full
    assert backend.consume("k", capacity=1, rate=1.0) is False  # t=0, no refill yet
    assert backend.consume("k", capacity=1, rate=1.0) is False  # t=0 again, still denied

    assert backend.consume("k", capacity=1, rate=1.0) is True  # t=10, refilled well past capacity


def test_in_memory_backend_tracks_keys_independently() -> None:
    backend = InMemoryRateLimitBackend()

    assert backend.consume("a", capacity=1, rate=0.0) is True
    assert backend.consume("b", capacity=1, rate=0.0) is True
    assert backend.consume("a", capacity=1, rate=0.0) is False


def test_in_memory_backend_evicts_oldest_beyond_max_tracked_clients() -> None:
    backend = InMemoryRateLimitBackend(max_tracked_clients=2)

    backend.consume("a", capacity=1, rate=0.0)
    backend.consume("b", capacity=1, rate=0.0)
    backend.consume("c", capacity=1, rate=0.0)

    assert list(backend._buckets) == ["b", "c"]


# --------------------------------------------------------------------- rate_limited()


def test_rate_limited_returns_one_middleware() -> None:
    tiers = rate_limited()

    assert len(tiers) == 1
    assert tiers[0].cls is RateLimitMiddleware  # type: ignore[comparison-overlap]


def test_rate_limited_uses_given_backend_instance() -> None:
    backend = InMemoryRateLimitBackend()

    tiers = rate_limited(backend=backend)

    assert tiers[0].kwargs["backend"] is backend


def test_rate_limit_middleware_allows_then_429s_with_retry_after() -> None:
    app = _app_with_middleware(Middleware(RateLimitMiddleware, requests_per_minute=60, burst=1))
    client = TestClient(app, client=("10.0.0.1", 1))

    first = client.get("/x")
    second = client.get("/x")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["status"] == "error"
    assert second.headers["retry-after"] == "1"


def test_rate_limit_middleware_tracks_clients_independently() -> None:
    app = _app_with_middleware(Middleware(RateLimitMiddleware, requests_per_minute=60, burst=1))

    client_a = TestClient(app, client=("10.0.0.1", 1))
    client_b = TestClient(app, client=("10.0.0.2", 1))

    client_a.get("/x")  # exhausts client A's single token

    assert client_a.get("/x").status_code == 429
    assert client_b.get("/x").status_code == 200


@pytest.mark.anyio
async def test_rate_limit_middleware_passes_through_non_http_scopes() -> None:
    calls: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope["type"])

    middleware = RateLimitMiddleware(app)
    scope: Scope = {"type": "lifespan"}

    async def receive() -> Message:
        return {}

    async def send(message: Message) -> None:
        pass

    await middleware(scope, receive, send)

    assert calls == ["lifespan"]


@pytest.mark.anyio
async def test_rate_limit_middleware_uses_unknown_key_when_scope_has_no_client() -> None:
    backend = InMemoryRateLimitBackend()
    middleware = RateLimitMiddleware(_raw_ok_app, requests_per_minute=60, burst=1, backend=backend)
    statuses: list[int] = []

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    scope: Scope = {"type": "http", "client": None, "method": "GET", "path": "/x", "headers": []}
    await middleware(scope, receive, send)
    await middleware(scope, receive, send)

    assert statuses == [200, 429]
    assert backend.consume("unknown", capacity=0, rate=0.0) is False


# --------------------------------------------------------------- _resolve_client_address()

_A_TRUSTED_PROXY = (ipaddress.ip_network("10.0.0.0/8"),)


def _scope_with(client: tuple[str, int] | None, headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {"type": "http", "client": client, "headers": headers or [], "method": "GET", "path": "/x"}


def test_resolve_client_address_no_trusted_proxies_returns_peer_unchanged() -> None:
    scope = _scope_with(("8.8.8.8", 1), headers=[(b"x-forwarded-for", b"1.2.3.4")])

    assert _resolve_client_address(scope, ()) == "8.8.8.8"


def test_resolve_client_address_no_client_returns_none() -> None:
    scope = _scope_with(None)

    assert _resolve_client_address(scope, _A_TRUSTED_PROXY) is None


def test_resolve_client_address_untrusted_peer_ignores_forwarded_header() -> None:
    # 8.8.8.8 is not in the trusted proxy CIDR -- its X-Forwarded-For can't be trusted, since
    # an untrusted client could set that header itself to claim any address.
    scope = _scope_with(("8.8.8.8", 1), headers=[(b"x-forwarded-for", b"1.2.3.4")])

    assert _resolve_client_address(scope, _A_TRUSTED_PROXY) == "8.8.8.8"


def test_resolve_client_address_trusted_peer_uses_forwarded_header() -> None:
    scope = _scope_with(("10.0.0.5", 1), headers=[(b"x-forwarded-for", b"203.0.113.7")])

    assert _resolve_client_address(scope, _A_TRUSTED_PROXY) == "203.0.113.7"


def test_resolve_client_address_walks_multi_hop_chain_to_first_untrusted() -> None:
    # "client, proxy1, proxy2" -- proxy2 (closest to server) and proxy1 are both trusted;
    # the real client is the first (leftmost-remaining) untrusted address found walking
    # right-to-left.
    scope = _scope_with(("10.0.0.5", 1), headers=[(b"x-forwarded-for", b"203.0.113.7, 10.0.0.3, 10.0.0.5")])

    assert _resolve_client_address(scope, _A_TRUSTED_PROXY) == "203.0.113.7"


def test_resolve_client_address_trusted_peer_no_header_falls_back_to_peer() -> None:
    scope = _scope_with(("10.0.0.5", 1), headers=[])

    assert _resolve_client_address(scope, _A_TRUSTED_PROXY) == "10.0.0.5"


def test_resolve_client_address_all_hops_trusted_falls_back_to_peer() -> None:
    scope = _scope_with(("10.0.0.5", 1), headers=[(b"x-forwarded-for", b"10.0.0.1, 10.0.0.2")])

    assert _resolve_client_address(scope, _A_TRUSTED_PROXY) == "10.0.0.5"


def test_resolve_client_address_malformed_peer_returns_it_unchanged() -> None:
    scope = _scope_with(("not-an-ip", 1))

    assert _resolve_client_address(scope, _A_TRUSTED_PROXY) == "not-an-ip"


def test_resolve_client_address_malformed_forwarded_hop_is_skipped() -> None:
    # Walked right-to-left: the malformed hop is closest to the server (checked first, skipped
    # via `continue`), and the real, valid client address is the next one examined.
    scope = _scope_with(("10.0.0.5", 1), headers=[(b"x-forwarded-for", b"203.0.113.7, not-an-ip")])

    assert _resolve_client_address(scope, _A_TRUSTED_PROXY) == "203.0.113.7"


# ------------------------------------------------- IntranetOnlyMiddleware trusted_proxies


def test_intranet_only_untrusted_proxy_forwarded_header_is_ignored() -> None:
    app = _app_with_middleware(
        Middleware(IntranetOnlyMiddleware, trusted_proxies=("10.0.0.0/8",)),
    )
    # The immediate peer (8.8.8.8) isn't a trusted proxy -- its claimed X-Forwarded-For
    # (an intranet address) must not be trusted, or a public client could spoof its way in.
    client = TestClient(app, client=("8.8.8.8", 1))

    response = client.get("/x", headers={"X-Forwarded-For": "10.0.0.5"})

    assert response.status_code == 403


def test_intranet_only_trusted_proxy_forwards_real_client() -> None:
    app = _app_with_middleware(
        Middleware(IntranetOnlyMiddleware, trusted_proxies=("10.0.0.0/8",)),
    )
    # The immediate peer IS a trusted proxy -- its X-Forwarded-For is honored, and the real
    # client behind it is outside the intranet allow-list.
    client = TestClient(app, client=("10.0.0.1", 1))

    response = client.get("/x", headers={"X-Forwarded-For": "8.8.8.8"})

    assert response.status_code == 403


def test_intranet_only_trusted_proxy_forwards_real_intranet_client() -> None:
    app = _app_with_middleware(
        Middleware(IntranetOnlyMiddleware, trusted_proxies=("10.0.0.0/8",)),
    )
    client = TestClient(app, client=("10.0.0.1", 1))

    response = client.get("/x", headers={"X-Forwarded-For": "192.168.1.5"})

    assert response.status_code == 200


def test_private_factory_threads_trusted_proxies_through() -> None:
    tiers = private(trusted_proxies=("10.0.0.0/8",))

    assert tiers[0].kwargs["trusted_proxies"] == ("10.0.0.0/8",)


# ------------------------------------------------------ RateLimitMiddleware trusted_proxies


def test_rate_limit_trusted_proxy_keys_by_real_client_not_proxy_address() -> None:
    app = _app_with_middleware(
        Middleware(RateLimitMiddleware, requests_per_minute=60, burst=1, trusted_proxies=("10.0.0.0/8",))
    )
    client = TestClient(app, client=("10.0.0.1", 1))

    first = client.get("/x", headers={"X-Forwarded-For": "203.0.113.1"})
    second_different_client = client.get("/x", headers={"X-Forwarded-For": "203.0.113.2"})
    third_same_client_as_first = client.get("/x", headers={"X-Forwarded-For": "203.0.113.1"})

    assert first.status_code == 200
    assert second_different_client.status_code == 200  # different real client behind the same proxy
    assert third_same_client_as_first.status_code == 429  # same real client, burst already spent


def test_rate_limited_factory_threads_trusted_proxies_through() -> None:
    tiers = rate_limited(trusted_proxies=("10.0.0.0/8",))

    assert tiers[0].kwargs["trusted_proxies"] == ("10.0.0.0/8",)

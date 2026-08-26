"""Composable, domain-agnostic middleware-tier factories for `NanobarRouteSet.middleware`/
`NanobarRouteRule.middleware` (`nanobar_api/routing.py`).

Each factory returns a plain `tuple[Middleware, ...]` and tiers compose — a route can be both
`private()` and `rate_limited()` at once, since they check different things, not mutually
exclusive levels of one enum. The admin-specific tiers (`protected()`/`session_protected()`/
`csrf_protected()`) live in the (not-yet-active) admin domain's own plan — these three don't need
that surface to be useful.
"""

from __future__ import annotations

import ipaddress
import math
import time
from collections import OrderedDict
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from starlette.middleware import Middleware
from starlette.responses import JSONResponse

from nanobar_api.envelope import error

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

#: Private-use ranges (RFC 1918) plus loopback — a reasonable default for "intranet", not an
#: exhaustive network-topology decision. Callers with a different notion of "internal" pass
#: their own `allowed_networks`.
DEFAULT_INTRANET_CIDRS: tuple[str, ...] = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")


def _resolve_client_address(
    scope: Scope, trusted_proxies: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
) -> str | None:
    """Resolves the real client address, trusting `X-Forwarded-For` only when the *immediate*
    ASGI peer (`scope["client"]`) is itself one of `trusted_proxies` — otherwise any client could
    simply set its own `X-Forwarded-For` header and spoof an arbitrary address. When trusted,
    walks the header's comma-separated chain right-to-left (closest-to-server first, per the
    standard's own append-on-each-hop convention), skipping further trusted-proxy hops, and
    returns the first untrusted address found — the real client. Falls back to the immediate
    peer when `trusted_proxies` is empty (the default — preserves this middleware's original,
    proxy-unaware behavior exactly for anyone not opting into this).

    This is the fix for `IntranetOnlyMiddleware`/`RateLimitMiddleware`'s shared, previously
    undocumented-as-solved gap (default-domains plan Open Decision 2): both silently used the
    proxy's own address instead of the real client's behind any reverse proxy.
    """
    client = scope.get("client")
    peer: str | None = client[0] if client is not None else None
    if peer is None or not trusted_proxies:
        return peer

    try:
        peer_addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not any(peer_addr in network for network in trusted_proxies):
        return peer  # immediate peer isn't a trusted proxy -- its X-Forwarded-For isn't trustworthy

    headers = dict(scope.get("headers", []))
    forwarded_for = headers.get(b"x-forwarded-for")
    if forwarded_for is None:
        return peer

    hops: list[str] = [hop.strip() for hop in forwarded_for.decode("latin-1").split(",")]
    for hop in reversed(hops):
        try:
            hop_addr = ipaddress.ip_address(hop)
        except ValueError:
            continue
        if not any(hop_addr in network for network in trusted_proxies):
            return hop
    return peer  # every hop claimed to be a trusted proxy -- fall back to the immediate peer


def public() -> tuple[Middleware, ...]:
    """No restriction — the identity element. Exists so a rule can explicitly opt out of its
    route set's shared middleware, documenting intent at the call site instead of a bare
    `middleware=()` reading as an oversight."""
    return ()


class IntranetOnlyMiddleware:
    """Restricts by client network origin, not identity — defense-in-depth, not a substitute
    for real network segmentation.

    Checks `scope["client"][0]`, the *immediate* ASGI-observed peer address, against
    `allowed_networks` — by default. Behind any reverse proxy or load balancer — a common
    deployment shape, not an edge case — `scope["client"]` is the proxy's address, not the real
    client's: if the proxy's own address isn't in the allow-list, this blocks everyone, including
    legitimate intranet traffic; if the proxy happens to run on a private address (common), this
    becomes a silent no-op that appears to work while checking nothing meaningful. Fixed by
    passing `trusted_proxies` (empty by default, preserving the original proxy-unaware behavior
    exactly): when the immediate peer is itself a trusted proxy, the real client is resolved from
    `X-Forwarded-For` instead (`_resolve_client_address` above) — never trusted blindly, since an
    untrusted client could otherwise set that header itself to spoof any address. The robust way
    to actually guarantee "intranet only" is still deployment-level (bind to an internal-only
    network interface, internal DNS/security-group routing); this middleware is defense-in-depth
    on top of that, never a substitute for it, with or without `trusted_proxies` configured.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_networks: Sequence[str] = DEFAULT_INTRANET_CIDRS,
        trusted_proxies: Sequence[str] = (),
    ) -> None:
        self.app = app
        self.networks = tuple(ipaddress.ip_network(cidr) for cidr in allowed_networks)
        self.trusted_proxies = tuple(ipaddress.ip_network(cidr) for cidr in trusted_proxies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        address = _resolve_client_address(scope, self.trusted_proxies)
        allowed = address is not None and self._is_allowed(address)
        if not allowed:
            response = JSONResponse(error("access restricted to the intranet"), status_code=403)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _is_allowed(self, address: str) -> bool:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(parsed in network for network in self.networks)


def private(
    *, allowed_networks: Sequence[str] = DEFAULT_INTRANET_CIDRS, trusted_proxies: Sequence[str] = ()
) -> tuple[Middleware, ...]:
    """Restricts by client network origin, not identity. See `IntranetOnlyMiddleware`'s
    docstring for the reverse-proxy handling `trusted_proxies` opts into."""
    return (Middleware(IntranetOnlyMiddleware, allowed_networks=allowed_networks, trusted_proxies=trusted_proxies),)


class RateLimitBackend(Protocol):
    def consume(self, key: str, capacity: float, rate: float) -> bool:
        """Attempt to consume one token for `key`, refilling based on `rate` (tokens/second)
        since that key's last known state, up to `capacity`. Returns whether the request is
        allowed. Implementations own however they track/refill state — in-memory dict, a
        shared SQLite file, or a networked store."""
        ...


class InMemoryRateLimitBackend:
    """Default. An `OrderedDict`-based token bucket — one process's memory, LRU-evicted at
    `max_tracked_clients` to bound growth. Two DIFFERENT limitations follow from being
    in-memory, not one: doesn't survive a process restart (no persistence at all, not
    specifically a "remote" problem — even a local durable store fixes this half), and doesn't
    share state across multiple worker processes (this half genuinely needs a *shared* store
    reachable by every process — commonly Redis, but a single SQLite file every process opens,
    this project's own `events.db` pattern, would work too).
    """

    def __init__(self, max_tracked_clients: int = 10_000) -> None:
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key -> (tokens, last_refill)
        self._max_tracked_clients = max_tracked_clients

    def consume(self, key: str, capacity: float, rate: float) -> bool:
        now = time.monotonic()
        tokens, last_refill = self._buckets.pop(key, (capacity, now))
        tokens = min(capacity, tokens + (now - last_refill) * rate)

        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0

        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        if len(self._buckets) > self._max_tracked_clients:
            self._buckets.popitem(last=False)

        return allowed


class RateLimitMiddleware:
    """`trusted_proxies` fixes the same real gap `IntranetOnlyMiddleware` had: without it, the
    rate-limit key is the immediate ASGI peer, which behind any reverse proxy is the proxy's own
    address — every real client sharing that proxy gets rate-limited together as one "client."
    Empty by default, preserving the original proxy-unaware behavior exactly.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        requests_per_minute: int = 60,
        burst: int = 10,
        backend: RateLimitBackend | None = None,
        trusted_proxies: Sequence[str] = (),
    ) -> None:
        self.app = app
        self.rate = requests_per_minute / 60.0  # tokens per second
        self.capacity = float(burst)
        self.backend = backend if backend is not None else InMemoryRateLimitBackend()
        self.trusted_proxies = tuple(ipaddress.ip_network(cidr) for cidr in trusted_proxies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        address = _resolve_client_address(scope, self.trusted_proxies)
        key = address if address is not None else "unknown"

        if not self.backend.consume(key, self.capacity, self.rate):
            retry_after_seconds = max(1, math.ceil(1.0 / self.rate))
            response = JSONResponse(
                error("rate limit exceeded"),
                status_code=429,
                headers={"Retry-After": str(retry_after_seconds)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def rate_limited(
    *,
    requests_per_minute: int = 60,
    burst: int = 10,
    backend: RateLimitBackend | None = None,
    trusted_proxies: Sequence[str] = (),
) -> tuple[Middleware, ...]:
    return (
        Middleware(
            RateLimitMiddleware,
            requests_per_minute=requests_per_minute,
            burst=burst,
            backend=backend if backend is not None else InMemoryRateLimitBackend(),
            trusted_proxies=trusted_proxies,
        ),
    )

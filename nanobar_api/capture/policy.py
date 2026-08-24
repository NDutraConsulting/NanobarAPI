"""Allow-list-based capture policy for request/response snapshotting.

Per the data-privacy ADR (`.focusari/data-privacy-adr.md` §3), a `CapturePolicy` is a
structured allow-list, not an opaque id: headers and query params are captured only when
their (lowercased) name is explicitly listed, never captured-then-redacted. Anything not
listed is simply never observed, so new sensitive fields introduced later (e.g. a new auth
header) are safe by default rather than silently exposed until someone remembers to add
them to a blocklist.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl


@dataclass(frozen=True)
class CapturePolicy:
    """What a snapshot capture is permitted to observe.

    `header_allowlist` and `query_param_allowlist` names are matched case-insensitively
    (HTTP header names are case-insensitive; query param names are conventionally treated
    the same way here for consistency) — store and compare them lowercased.
    """

    header_allowlist: tuple[str, ...] = ()
    query_param_allowlist: tuple[str, ...] = ()
    body_cap_bytes: int = 65536  # 64 KB, per the design doc's own example figure


def default_capture_policy() -> CapturePolicy:
    """A conservative, safe-by-default policy.

    Captures only the header names that never carry credentials or PII by convention
    (content-type, accept, user-agent). `authorization`, `cookie`, and `set-cookie` are
    excluded by omission — allow-list semantics mean there is nothing to redact, they are
    simply never captured. Query params are captured only when an app explicitly opts a
    name in (empty by default), since query strings commonly carry tokens or emails.
    """
    return CapturePolicy(
        header_allowlist=("content-type", "accept", "user-agent"),
        query_param_allowlist=(),
    )


def apply_header_allowlist(policy: CapturePolicy, headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    """Filter raw ASGI scope headers down to the policy's allow-listed names.

    `headers` is the raw ASGI scope header list — `(name, value)` byte-tuples, names
    lowercase per the ASGI spec. Values are decoded as latin-1, the correct encoding for
    HTTP header bytes per the ASGI spec (not utf-8). If a header name appears multiple
    times in the raw list (e.g. repeated `Set-Cookie`-style headers), the values are
    joined with ", " — the standard HTTP semantics for combining repeated header fields.
    """
    allowed = set(policy.header_allowlist)
    result: dict[str, str] = {}
    for raw_name, raw_value in headers:
        name = raw_name.decode("latin-1").lower()
        if name not in allowed:
            continue
        value = raw_value.decode("latin-1")
        if name in result:
            result[name] = f"{result[name]}, {value}"
        else:
            result[name] = value
    return result


def apply_query_param_allowlist(policy: CapturePolicy, query_string: bytes) -> dict[str, str]:
    """Filter a raw ASGI scope query string down to the policy's allow-listed param names.

    `query_string` is the raw ASGI scope `query_string` bytes (url-encoded). An empty or
    missing query string returns `{}`. If a param name appears multiple times, the first
    occurrence is kept (later duplicates are ignored).
    """
    if not query_string:
        return {}
    allowed = set(policy.query_param_allowlist)
    result: dict[str, str] = {}
    for name, value in parse_qsl(query_string.decode("latin-1"), keep_blank_values=True):
        if name not in allowed or name in result:
            continue
        result[name] = value
    return result

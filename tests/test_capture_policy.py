from nanobar_api.capture.policy import (
    CapturePolicy,
    apply_header_allowlist,
    apply_query_param_allowlist,
    default_capture_policy,
)


def test_default_policy_contents() -> None:
    policy = default_capture_policy()

    assert policy.header_allowlist == ("content-type", "accept", "user-agent")
    assert policy.query_param_allowlist == ()
    assert "authorization" not in policy.header_allowlist
    assert "cookie" not in policy.header_allowlist
    assert "set-cookie" not in policy.header_allowlist


def test_default_body_cap_bytes() -> None:
    assert CapturePolicy().body_cap_bytes == 65536


def test_header_allowlist_matches_case_insensitively() -> None:
    policy = CapturePolicy(header_allowlist=("content-type",))
    headers = [(b"Content-Type", b"application/json")]

    result = apply_header_allowlist(policy, headers)

    assert result == {"content-type": "application/json"}


def test_header_decoding_uses_latin1_not_utf8() -> None:
    policy = CapturePolicy(header_allowlist=("x-test",))
    # \xe9 is a valid latin-1 byte ("é") but is not valid standalone utf-8.
    headers = [(b"x-test", b"\xe9")]

    result = apply_header_allowlist(policy, headers)

    assert result == {"x-test": "\xe9"}


def test_repeated_headers_are_joined_with_comma_space() -> None:
    policy = CapturePolicy(header_allowlist=("accept",))
    headers = [(b"accept", b"text/html"), (b"accept", b"application/json")]

    result = apply_header_allowlist(policy, headers)

    assert result == {"accept": "text/html, application/json"}


def test_header_not_in_allowlist_is_excluded() -> None:
    policy = CapturePolicy(header_allowlist=("content-type",))
    headers = [(b"authorization", b"Bearer secret"), (b"content-type", b"text/plain")]

    result = apply_header_allowlist(policy, headers)

    assert result == {"content-type": "text/plain"}
    assert "authorization" not in result


def test_query_param_allowlist_filters_to_named_params() -> None:
    policy = CapturePolicy(query_param_allowlist=("a",))

    result = apply_query_param_allowlist(policy, b"a=1&b=2")

    assert result == {"a": "1"}
    assert "b" not in result


def test_empty_query_string_returns_empty_dict() -> None:
    policy = CapturePolicy(query_param_allowlist=("a",))

    assert apply_query_param_allowlist(policy, b"") == {}


def test_repeated_query_param_keeps_first_occurrence() -> None:
    policy = CapturePolicy(query_param_allowlist=("a",))

    result = apply_query_param_allowlist(policy, b"a=1&a=2")

    assert result == {"a": "1"}

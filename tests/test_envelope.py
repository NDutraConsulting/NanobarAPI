from nanobar_api import error, is_error, success, timeout


def test_success() -> None:
    envelope = success({"id": 1})
    assert envelope == {"status": "success", "msg": "", "result": {"type": "object", "data": {"id": 1}}}
    assert is_error(envelope) is False


def test_success_with_type() -> None:
    envelope = success([1, 2], type_="array")
    assert envelope["result"]["type"] == "array"


def test_error() -> None:
    envelope = error("bad request")
    assert envelope == {"status": "error", "msg": "bad request", "result": {"type": "object", "data": None}}
    assert is_error(envelope) is True


def test_error_with_data() -> None:
    envelope = error("bad request", data={"field": "name"})
    assert envelope["result"]["data"] == {"field": "name"}


def test_timeout_default_message() -> None:
    envelope = timeout()
    assert envelope["status"] == "timeout"
    assert envelope["msg"] == "Operation timed out"
    assert is_error(envelope) is True


def test_timeout_custom_message() -> None:
    envelope = timeout("took too long")
    assert envelope["msg"] == "took too long"

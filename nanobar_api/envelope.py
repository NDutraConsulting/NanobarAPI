from __future__ import annotations

from typing import Any, Literal, TypedDict

Status = Literal["success", "error", "timeout"]
ResultType = Literal["object", "array", "map"]


class Result(TypedDict):
    type: ResultType
    data: Any


class Envelope(TypedDict):
    status: Status
    msg: str
    result: Result


def success(data: Any, type_: ResultType = "object") -> Envelope:
    return Envelope(status="success", msg="", result=Result(type=type_, data=data))


def error(msg: str, data: Any = None, type_: ResultType = "object") -> Envelope:
    return Envelope(status="error", msg=msg, result=Result(type=type_, data=data))


def timeout(msg: str = "Operation timed out") -> Envelope:
    return Envelope(status="timeout", msg=msg, result=Result(type="object", data=None))


def is_error(envelope: Envelope) -> bool:
    return envelope["status"] != "success"

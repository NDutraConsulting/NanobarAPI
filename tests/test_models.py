from __future__ import annotations

import pytest

from nanobar_api.framework.nanobar_api_model import NanobarAPIModel
from nanobar_api.state_machine import InvalidTransition


class _Order(NanobarAPIModel):
    monitored_state_fields = {"status": ("pending", "shipped", "delivered")}
    idempotent_fields = ("order_id", "sku")


class _NoDeclarationsModel(NanobarAPIModel):
    pass


def test_state_machine_for_builds_a_working_state_machine() -> None:
    machine = _Order.state_machine_for("status", "pending")

    assert machine.state == "pending"
    assert machine.can_transition_to("shipped") is True
    machine.transition_to("shipped")
    assert machine.state == "shipped"


def test_state_machine_for_allows_any_declared_state_to_any_other() -> None:
    machine = _Order.state_machine_for("status", "delivered")

    assert machine.can_transition_to("pending") is True


def test_state_machine_for_disallows_transition_to_undeclared_state() -> None:
    machine = _Order.state_machine_for("status", "pending")

    assert machine.can_transition_to("cancelled") is False
    with pytest.raises(InvalidTransition):
        machine.transition_to("cancelled")


def test_state_machine_for_disallows_transition_to_self() -> None:
    machine = _Order.state_machine_for("status", "pending")

    assert machine.can_transition_to("pending") is False


def test_state_machine_for_unknown_field_raises_key_error() -> None:
    with pytest.raises(KeyError):
        _Order.state_machine_for("does_not_exist", "pending")


def test_is_idempotent_retry_true_when_all_idempotent_fields_match() -> None:
    previous = {"order_id": "o1", "sku": "widget", "status": "pending"}
    candidate = {"order_id": "o1", "sku": "widget", "status": "shipped"}

    assert _Order.is_idempotent_retry(previous, candidate) is True


def test_is_idempotent_retry_false_when_any_idempotent_field_differs() -> None:
    previous = {"order_id": "o1", "sku": "widget"}
    candidate = {"order_id": "o1", "sku": "gadget"}

    assert _Order.is_idempotent_retry(previous, candidate) is False


def test_is_idempotent_retry_false_when_no_idempotent_fields_declared() -> None:
    assert _NoDeclarationsModel.is_idempotent_retry({"a": 1}, {"a": 1}) is False


def test_monitored_state_fields_and_idempotent_fields_default_to_empty() -> None:
    assert _NoDeclarationsModel.monitored_state_fields == {}
    assert _NoDeclarationsModel.idempotent_fields == ()

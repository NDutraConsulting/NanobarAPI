from typing import Literal

import pytest

from nanobar_api import InvalidTransition, StateMachine

State = Literal["draft", "published", "archived"]

TRANSITIONS: dict[State, set[State]] = {
    "draft": {"published"},
    "published": {"archived"},
    "archived": set(),
}


def test_valid_transition() -> None:
    machine: StateMachine[State] = StateMachine("draft", TRANSITIONS)
    machine.transition_to("published")
    assert machine.state == "published"


def test_can_transition_to() -> None:
    machine: StateMachine[State] = StateMachine("draft", TRANSITIONS)
    assert machine.can_transition_to("published") is True
    assert machine.can_transition_to("archived") is False


def test_invalid_transition_raises() -> None:
    machine: StateMachine[State] = StateMachine("draft", TRANSITIONS)
    with pytest.raises(InvalidTransition) as exc_info:
        machine.transition_to("archived")
    assert exc_info.value.from_state == "draft"
    assert exc_info.value.to_state == "archived"
    assert machine.state == "draft"


def test_on_transition_callback_fires() -> None:
    events: list[tuple[State, State]] = []
    machine: StateMachine[State] = StateMachine(
        "draft", TRANSITIONS, on_transition=lambda old, new: events.append((old, new))
    )
    machine.transition_to("published")
    assert events == [("draft", "published")]


def test_no_callback_is_fine() -> None:
    machine: StateMachine[State] = StateMachine("draft", TRANSITIONS)
    machine.transition_to("published")
    assert machine.state == "published"


def test_terminal_state_has_no_transitions() -> None:
    machine: StateMachine[State] = StateMachine("archived", TRANSITIONS)
    assert machine.can_transition_to("draft") is False

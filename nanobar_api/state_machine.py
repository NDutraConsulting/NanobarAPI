from __future__ import annotations

from collections.abc import Callable, Mapping, Set


class InvalidTransition(Exception):
    def __init__(self, from_state: object, to_state: object) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"cannot transition from {from_state!r} to {to_state!r}")


class StateMachine[StateT]:
    def __init__(
        self,
        initial: StateT,
        transitions: Mapping[StateT, Set[StateT]],
        on_transition: Callable[[StateT, StateT], None] | None = None,
    ) -> None:
        self.state = initial
        self._transitions = transitions
        self._on_transition = on_transition

    def can_transition_to(self, new_state: StateT) -> bool:
        return new_state in self._transitions.get(self.state, set())

    def transition_to(self, new_state: StateT) -> None:
        if not self.can_transition_to(new_state):
            raise InvalidTransition(self.state, new_state)
        old_state = self.state
        self.state = new_state
        if self._on_transition is not None:
            self._on_transition(old_state, new_state)

"""`NanobarAPIModel` — a declaration surface on top of the existing, generic
`StateMachine[StateT]` (`nanobar_api/state_machine.py`, not touched by this module), registering
which fields of a model/dataclass are state-machine-governed and which are idempotency-relevant.

Per `.focusari/nanobar_ServiceDomain_abstract_class_buildplan-with-tasks.md` §3: this builds the
declaration surface *only* — wiring declared fields to the eventbus (boundary 4, "State-machine
transitions") is that boundary's own not-yet-built step, not re-scoped here.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Mapping
from typing import Any, ClassVar

from nanobar_api.state_machine import StateMachine


class NanobarAPIModel(ABC):
    #: field name -> the full set of allowed values for that field, not a per-state transition
    #: graph. `state_machine_for()` below treats this permissively (any declared state may
    #: transition to any other) since that's all this flat shape can express — a model needing
    #: real transition-order enforcement should build its own `StateMachine` directly (already a
    #: public, general-purpose class) rather than through this convenience default.
    monitored_state_fields: ClassVar[Mapping[str, tuple[str, ...]]] = {}
    #: fields whose values alone determine whether a candidate write is a retry of a previous one.
    idempotent_fields: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def state_machine_for(cls, field: str, initial: Any) -> StateMachine[Any]:
        if field not in cls.monitored_state_fields:
            raise KeyError(f"{field!r} is not declared in {cls.__name__}.monitored_state_fields")
        allowed_states = cls.monitored_state_fields[field]
        transitions = {state: frozenset(s for s in allowed_states if s != state) for state in allowed_states}
        return StateMachine(initial, transitions)

    @classmethod
    def is_idempotent_retry(cls, previous: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
        """True if every `idempotent_fields` value matches between `previous`/`candidate` —
        `candidate` is a retry of the same logical operation, not a new one. `False`, not
        vacuously `True`, when `idempotent_fields` is empty (the default): no declared fields
        means idempotency can't be determined here, not that every candidate is automatically
        a retry.
        """
        if not cls.idempotent_fields:
            return False
        return all(previous.get(field) == candidate.get(field) for field in cls.idempotent_fields)

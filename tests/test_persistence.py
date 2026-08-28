from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.nanobar.model import Nanobar, NanobarBrickBinding
from nanobar_api.persistence import build_session_factory
from nanobar_api.regression_brick.model import RegressionBrick


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


def _make_nanobar() -> Nanobar:
    return Nanobar(
        schema_version="1.0",
        system_name="test",
        system_version="0.0.0",
        nanobar_type="api-response",
        request_object_id="req-1",
        response_object_id="res-1",
        regression_weight=0.5,
        endpoint_scenario_frequency={"state": "unmeasured"},
        created_by="test",
    )


def _make_brick(content_hash: str = "sha256:abc") -> RegressionBrick:
    return RegressionBrick(
        schema_version="1.0",
        brick_version=1,
        source={"a": 1},
        request={"b": 2},
        response={"c": 3},
        content_hash=content_hash,
        created_by="test",
    )


def test_build_session_factory_creates_schema_for_both_entities_without_manual_imports(tmp_path: Path) -> None:
    """The caller only imports `nanobar_api.persistence` -- both entities' tables must still get
    registered against `Base.metadata` (see `_ensure_all_entity_models_registered`'s own
    docstring for the `NoReferencedTableError` this guards against)."""
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        session.add(_make_nanobar())
        session.add(_make_brick())
        session.commit()

        assert session.query(Nanobar).count() == 1
        assert session.query(RegressionBrick).count() == 1


def test_foreign_key_integrity_enforced_on_insert(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        session.add(
            NanobarBrickBinding(
                nanobar_id="nb-does-not-exist",
                regression_brick_id="rbrick-does-not-exist",
                match_method="exact",
                matcher_version="v1",
                matched_by="test",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_regression_bricks_are_immutable(tmp_path: Path) -> None:
    Session = build_session_factory(str(tmp_path / "test.db"), repository=_repository())

    with Session() as session:
        brick = _make_brick()
        session.add(brick)
        session.commit()

        brick.brick_version = 2
        with pytest.raises(IntegrityError):
            session.commit()


def test_schema_creation_is_idempotent(tmp_path: Path) -> None:
    """Calling `build_session_factory` twice against the same file (a second process/worker,
    a test re-running) must not fail -- `Base.metadata.create_all` and the trigger's own
    `CREATE TRIGGER IF NOT EXISTS` are both no-ops against an already-initialized database."""
    db_path = str(tmp_path / "test.db")
    build_session_factory(db_path, repository=_repository())
    Session = build_session_factory(db_path, repository=_repository())

    with Session() as session:
        session.add(_make_nanobar())
        session.commit()
        assert session.query(Nanobar).count() == 1

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from nanobar_api import Controller, Repository, Service


def test_controller_is_instantiable() -> None:
    assert isinstance(Controller(), Controller)


def test_service_is_instantiable() -> None:
    assert isinstance(Service(), Service)


def test_repository_holds_session() -> None:
    engine = create_engine("sqlite://")
    session = Session(engine)
    try:
        repository = Repository(session)
        assert repository.session is session
    finally:
        session.close()

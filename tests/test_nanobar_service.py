from __future__ import annotations

from pathlib import Path

import pytest

from nanobar_api.eventbus.queue_repository import ChannelConfig, EventQueueRepository
from nanobar_api.nanobar.model import Nanobar
from nanobar_api.nanobar.repository import NanobarRepository
from nanobar_api.nanobar.service import NanobarService, UpdateNanobarRequest
from nanobar_api.persistence import build_session_factory
from nanobar_api.taxonomy import ExpectedScenario, NanobarTypeEntry, NanobarTypeTaxonomy
from nanobar_api.telemetry import NanobarTelemetry


def _repository() -> EventQueueRepository:
    return EventQueueRepository([ChannelConfig(name="trace"), ChannelConfig(name="snapshot")])


def _nanobar_repository(tmp_path: Path) -> NanobarRepository:
    session = build_session_factory(str(tmp_path / "bricks.db"), repository=_repository())()
    return NanobarRepository(session)


def _make_nanobar(**overrides: object) -> Nanobar:
    defaults: dict[str, object] = {
        "schema_version": "1.0",
        "system_name": "test",
        "system_version": "0.0.0",
        "nanobar_type": "api-response",
        "request_object_id": "req-1",
        "response_object_id": "res-1",
        "regression_weight": 0.5,
        "endpoint_scenario_frequency": {"state": "unmeasured"},
        "created_by": "test",
    }
    defaults.update(overrides)
    return Nanobar(**defaults)


def _service(
    nanobar_repository: NanobarRepository, tmp_path: Path, *, static_taxonomy: NanobarTypeTaxonomy | None = None
) -> NanobarService:
    return NanobarService(
        NanobarTelemetry(_repository(), channel="trace"),
        nanobar_repository,
        static_taxonomy=static_taxonomy if static_taxonomy is not None else {},
        dynamic_taxonomy_db_path=str(tmp_path / "nanobar_type_system.db"),
    )


def test_update_not_found(tmp_path: Path) -> None:
    service = _service(_nanobar_repository(tmp_path), tmp_path)

    result = service(
        UpdateNanobarRequest(
            nanobar_id="does-not-exist",
            label=None,
            scenario_description=None,
            component_source_description=None,
            domain=None,
            app_box=None,
            criticality=None,
        )
    )

    assert result.status == "error"
    assert "does-not-exist" in result.result.msg_summary


def test_update_partial_update_keeps_omitted_fields(tmp_path: Path) -> None:
    nanobar_repository = _nanobar_repository(tmp_path)
    nanobar = nanobar_repository.create(_make_nanobar(label="original"))
    service = _service(nanobar_repository, tmp_path)

    result = service(
        UpdateNanobarRequest(
            nanobar_id=nanobar.nanobar_id,
            label=None,
            scenario_description="fetches an order",
            component_source_description=None,
            domain=None,
            app_box=None,
            criticality=None,
        )
    )

    assert result.status == "success"
    assert result.result.data["label"] == "original"
    assert result.result.data["scenario_description"] == "fetches an order"


def test_update_criticality_change_with_no_taxonomy_entry_at_all_does_not_crash(tmp_path: Path) -> None:
    """`nanobar_type` matches no static entry and no recognized dynamic prefix (`split is None`
    inside `_effective_taxonomy`) -- `compute_regression_weight` falls back to the placeholder
    weight, unchanged, per its own documented Open-Decision-1 behavior."""
    nanobar_repository = _nanobar_repository(tmp_path)
    nanobar = nanobar_repository.create(_make_nanobar(nanobar_type="totally-unrecognized-type"))
    service = _service(nanobar_repository, tmp_path)

    result = service(
        UpdateNanobarRequest(
            nanobar_id=nanobar.nanobar_id,
            label=None,
            scenario_description=None,
            component_source_description=None,
            domain=None,
            app_box=None,
            criticality=1.0,
        )
    )

    assert result.status == "success"
    assert result.result.data["regression_weight"] == 0.5  # unresolvable type -- unchanged placeholder


def test_update_criticality_change_for_a_dynamic_type_with_no_static_baseline(tmp_path: Path) -> None:
    """`nanobar_type` matches the `worker-*` dynamic prefix, but the static taxonomy has no
    `"worker"` baseline entry to seed a dynamic one from (`default_entry is None`) -- falls back
    to the static taxonomy unchanged, same as the fully-unrecognized case."""
    nanobar_repository = _nanobar_repository(tmp_path)
    nanobar = nanobar_repository.create(_make_nanobar(nanobar_type="worker-domain.appointments"))
    service = _service(nanobar_repository, tmp_path, static_taxonomy={})

    result = service(
        UpdateNanobarRequest(
            nanobar_id=nanobar.nanobar_id,
            label=None,
            scenario_description=None,
            component_source_description=None,
            domain=None,
            app_box=None,
            criticality=1.0,
        )
    )

    assert result.status == "success"
    assert result.result.data["regression_weight"] == 0.5


def test_update_criticality_change_for_a_recognized_dynamic_type_recomputes_weight(tmp_path: Path) -> None:
    nanobar_repository = _nanobar_repository(tmp_path)
    nanobar = nanobar_repository.create(_make_nanobar(nanobar_type="worker-domain.appointments"))
    static_taxonomy: NanobarTypeTaxonomy = {
        "worker": NanobarTypeEntry(
            expected_scenarios={"success": ExpectedScenario(weight=1.0, required=True, synthesizable=False)}
        )
    }
    service = _service(nanobar_repository, tmp_path, static_taxonomy=static_taxonomy)

    result = service(
        UpdateNanobarRequest(
            nanobar_id=nanobar.nanobar_id,
            label=None,
            scenario_description=None,
            component_source_description=None,
            domain=None,
            app_box=None,
            criticality=1.0,
        )
    )

    assert result.status == "success"
    # No bricks bound -- zero of the one required scenario covered.
    assert result.result.data["regression_weight"] == pytest.approx(0.0)


def test_update_without_criticality_change_skips_weight_recompute(tmp_path: Path) -> None:
    nanobar_repository = _nanobar_repository(tmp_path)
    nanobar = nanobar_repository.create(_make_nanobar(criticality=0.5))
    service = _service(nanobar_repository, tmp_path)

    result = service(
        UpdateNanobarRequest(
            nanobar_id=nanobar.nanobar_id,
            label="new label",
            scenario_description=None,
            component_source_description=None,
            domain=None,
            app_box=None,
            criticality=None,
        )
    )

    assert result.result.data["regression_weight"] == 0.5  # unchanged placeholder, never recomputed

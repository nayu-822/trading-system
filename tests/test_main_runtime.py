from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any

import pytest

import main as app_main
from domain.enums import (
    DataSourceMode,
    EventSource,
    EventType,
    OrderSide,
    OrderStatus,
    SignalType,
    TradingHaltReason,
    TradingMode,
)
from domain.events import (
    BaseEvent,
    EventFactory,
    MarketDataPayload,
    OrderStatusPayload,
    SignalPayload,
)
from domain.models import IndicatorValue, Order, Position, SystemConfig
from infrastructure.config_loader import load_config
from infrastructure.event_bus import EventBus


def test_runtime_initializes_api_paper_and_runs_end_to_end(
    monkeypatch,
) -> None:
    snapshot_dir = _test_dir("api_paper")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
        snapshot_enabled=True,
    )
    fake_external = _FakeExternalDataProcess(
        events=(
            _market_data_event(price=100.0),
            _market_data_event(price=101.0),
        )
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )

    runtime = app_main.build_application_runtime(config=config)

    runtime.start()
    runtime.run_external_data()
    runtime.flush()
    runtime.stop()

    state = runtime.trading_process.get_state("7203")

    assert fake_external.run_count == 1
    assert fake_external.sync_count == 1
    assert fake_external.stop_count == 1
    assert state.position is not None
    assert state.position.quantity == 100
    assert (snapshot_dir / "events.jsonl").exists()
    assert (snapshot_dir / "trading_snapshot.json").exists()


def test_runtime_keeps_csv_paper_flow(monkeypatch) -> None:
    snapshot_dir = _test_dir("csv_paper")
    config = _runtime_config(
        data_source_mode=DataSourceMode.CSV,
        snapshot_dir=snapshot_dir,
        snapshot_enabled=True,
    )
    fake_external = _FakeExternalDataProcess(
        events=(
            _market_data_event(price=100.0),
            _market_data_event(price=101.0),
        )
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )

    runtime = app_main.build_application_runtime(
        config=config,
        csv_path=Path("tests/fixtures/market_data.csv"),
    )

    runtime.start()
    runtime.run_external_data()
    runtime.flush()
    runtime.stop()

    state = runtime.trading_process.get_state("7203")

    assert fake_external.run_count == 1
    assert fake_external.sync_count == 0
    assert state.position is not None
    assert state.position.quantity == 100


def test_runtime_build_injects_risk_manager_in_csv_mode(monkeypatch) -> None:
    snapshot_dir = _test_dir("csv_risk_manager")
    config = _runtime_config(
        data_source_mode=DataSourceMode.CSV,
        snapshot_dir=snapshot_dir,
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(_FakeExternalDataProcess(events=())),
    )

    runtime = app_main.build_application_runtime(config=config)

    assert runtime.trading_process.risk_manager is not None


def test_runtime_halts_when_startup_order_resync_fails_in_api_paper(
    monkeypatch,
) -> None:
    snapshot_dir = _test_dir("api_paper_sync_failed")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    fake_external = _FakeExternalDataProcess(
        events=(),
        raise_on_sync=RuntimeError("sync failed"),
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )

    runtime = app_main.build_application_runtime(config=config)

    with pytest.raises(RuntimeError, match="sync failed"):
        runtime.start()

    assert runtime.trading_process.risk_manager is not None
    assert runtime.trading_process.risk_manager.state.kill_switch_active is True
    assert (
        runtime.trading_process.risk_manager.state.trading_halt_reason
        == TradingHaltReason.ORDER_SYNC_FAILED
    )
    assert runtime.trading_process.risk_manager.state.requires_manual_resume is True


def test_runtime_starts_without_snapshot_when_snapshot_is_missing(
    monkeypatch,
) -> None:
    snapshot_dir = _test_dir("missing_snapshot")
    config = _runtime_config(
        data_source_mode=DataSourceMode.CSV,
        snapshot_dir=snapshot_dir,
        snapshot_enabled=True,
        recovery_enabled=True,
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(_FakeExternalDataProcess(events=())),
    )
    runtime = app_main.build_application_runtime(config=config)

    runtime.start()
    runtime.stop()

    assert runtime.restored_snapshot is False


def test_runtime_restores_snapshot_and_continues_trading(monkeypatch) -> None:
    snapshot_dir = _test_dir("restore_snapshot")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
        snapshot_enabled=True,
        recovery_enabled=True,
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(_FakeExternalDataProcess(events=())),
    )
    original_runtime = app_main.build_application_runtime(
        config=replace(config, app=replace(config.app, recovery_enabled=False))
    )
    state = original_runtime.trading_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = 100
    state.position.average_price = 1000.0
    original_runtime.snapshot_process.save_snapshot(_timestamp())
    original_runtime.snapshot_process.start()
    original_runtime.snapshot_process.flush()
    original_runtime.snapshot_process.stop()

    restored_runtime = app_main.build_application_runtime(config=config)
    restored_runtime.start()
    restored_runtime.event_bus.publish(_market_data_event(price=1000.0))
    restored_runtime.event_bus.publish(_market_data_event(price=1001.0))
    restored_runtime.flush()
    restored_runtime.stop()

    restored_state = restored_runtime.trading_process.get_state("7203")

    assert restored_runtime.restored_snapshot is True
    assert restored_state.position is not None
    assert restored_state.position.quantity == 100
    assert len(restored_state.orders) == 0


def test_runtime_resyncs_order_status_after_restore(monkeypatch) -> None:
    snapshot_dir = _test_dir("restore_order_resync")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
        snapshot_enabled=True,
        recovery_enabled=True,
    )
    original_runtime = app_main.build_application_runtime(
        config=replace(config, app=replace(config.app, recovery_enabled=False))
    )
    state = original_runtime.trading_process.get_state("7203")
    state.orders.append(
        Order(
            order_id="local-order-1",
            external_order_id="api-order-1",
            symbol="7203",
            side=OrderSide.BUY,
            quantity=100,
            order_type="MARKET",
            status=OrderStatus.REQUESTED,
            remaining_quantity=100,
        )
    )
    original_runtime.snapshot_process.save_snapshot(_timestamp())
    original_runtime.snapshot_process.start()
    original_runtime.snapshot_process.flush()
    original_runtime.snapshot_process.stop()
    fake_external = _FakeExternalDataProcess(
        events=(),
        sync_events=(_order_status_event("api-order-1"),),
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )

    restored_runtime = app_main.build_application_runtime(config=config)
    restored_runtime.start()
    restored_runtime.flush()
    restored_runtime.stop()
    restored_state = restored_runtime.trading_process.get_state("7203")

    assert fake_external.sync_count == 1
    assert restored_state.position is not None
    assert restored_state.position.quantity == 100
    assert len(restored_state.trade_histories) == 1
    assert restored_state.trade_histories[0].filled_quantity == 100
    assert len(restored_state.orders) == 0


def test_runtime_restores_open_orders_from_api_on_startup(monkeypatch) -> None:
    snapshot_dir = _test_dir("restore_open_orders")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
        snapshot_enabled=True,
        recovery_enabled=True,
    )
    fake_external = _FakeExternalDataProcess(
        events=(),
        sync_events=(
            EventFactory(source=EventSource.EXTERNAL_DATA).create(
                event_type=EventType.ORDER_STATUS_UPDATED,
                timestamp=_timestamp(),
                symbol="7203",
                payload=OrderStatusPayload(
                    order_id="api-order-open-1",
                    status=OrderStatus.REQUESTED,
                    filled_quantity=0,
                    remaining_quantity=100,
                    avg_price=None,
                    side=OrderSide.BUY,
                    order_quantity=100,
                    is_exit=False,
                    external_order_id="api-order-open-1",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )

    runtime = app_main.build_application_runtime(config=config)
    runtime.start()
    runtime.flush()
    runtime.stop()
    state = runtime.trading_process.get_state("7203")

    assert fake_external.sync_count == 1
    assert len(state.orders) == 1
    assert state.orders[0].order_id == "api-order-open-1"
    assert state.orders[0].status == OrderStatus.REQUESTED


def test_runtime_reflects_filled_order_from_api_on_startup(monkeypatch) -> None:
    snapshot_dir = _test_dir("restore_filled_order")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
        snapshot_enabled=True,
        recovery_enabled=True,
    )
    fake_external = _FakeExternalDataProcess(
        events=(),
        sync_events=(
            EventFactory(source=EventSource.EXTERNAL_DATA).create(
                event_type=EventType.ORDER_STATUS_UPDATED,
                timestamp=_timestamp(),
                symbol="7203",
                payload=OrderStatusPayload(
                    order_id="api-order-filled-1",
                    status=OrderStatus.FILLED,
                    filled_quantity=100,
                    remaining_quantity=0,
                    avg_price=1000.0,
                    side=OrderSide.BUY,
                    order_quantity=100,
                    is_exit=False,
                    external_order_id="api-order-filled-1",
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )

    runtime = app_main.build_application_runtime(config=config)
    runtime.start()
    runtime.flush()
    runtime.stop()
    state = runtime.trading_process.get_state("7203")

    assert state.position is not None
    assert state.position.quantity == 100
    assert len(state.trade_histories) == 1
    assert state.trade_histories[0].order_id == "api-order-filled-1"
    assert len(state.orders) == 0


def test_runtime_start_stop_start_does_not_duplicate_subscriptions(
    monkeypatch,
) -> None:
    snapshot_dir = _test_dir("restart")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    fake_external = _FakeExternalDataProcess(
        events=(
            _market_data_event(price=100.0),
            _market_data_event(price=101.0),
        )
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )
    runtime = app_main.build_application_runtime(config=config)

    runtime.start()
    runtime.stop()
    runtime.start()
    runtime.run_external_data()
    runtime.flush()
    runtime.stop()

    lines = (snapshot_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()

    assert len([line for line in lines if "MarketDataUpdated" in line]) == 2
    assert len([line for line in lines if "SignalDetected" in line]) == 1


def test_api_runtime_waits_until_stop_event_is_set(monkeypatch) -> None:
    snapshot_dir = _test_dir("api_wait")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(_FakeExternalDataProcess(events=())),
    )
    runtime = app_main.build_application_runtime(config=config)
    stop_event = Event()

    stop_event.set()
    runtime.wait(stop_event=stop_event)

    assert stop_event.is_set()


def test_csv_runtime_wait_returns_without_stop_event(monkeypatch) -> None:
    snapshot_dir = _test_dir("csv_wait")
    config = _runtime_config(
        data_source_mode=DataSourceMode.CSV,
        snapshot_dir=snapshot_dir,
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(_FakeExternalDataProcess(events=())),
    )
    runtime = app_main.build_application_runtime(config=config)
    stop_event = Event()

    runtime.wait(stop_event=stop_event)

    assert not stop_event.is_set()


def test_runtime_stop_flushes_before_workers_stop(monkeypatch) -> None:
    snapshot_dir = _test_dir("stop_flush")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
        snapshot_enabled=True,
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(_FakeExternalDataProcess(events=())),
    )
    runtime = app_main.build_application_runtime(config=config)
    calls: list[str] = []
    runtime.persistence_process.flush = lambda: calls.append("persistence_flush")
    runtime.snapshot_process.flush = lambda: calls.append("snapshot_flush")
    runtime.persistence_process.stop = lambda: calls.append("persistence_stop")
    runtime.snapshot_process.stop = lambda: calls.append("snapshot_stop")

    runtime.stop()

    assert calls == [
        "persistence_flush",
        "snapshot_flush",
        "snapshot_stop",
        "persistence_stop",
    ]


def test_runtime_reconcile_positions_keeps_trading_when_positions_match(
    monkeypatch,
) -> None:
    snapshot_dir = _test_dir("position_match")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(_FakeExternalDataProcess(events=())),
    )
    runtime = app_main.build_application_runtime(config=config)
    runtime.position_reconciliation_service = _FakePositionReconciliationService()

    reconciled = runtime.reconcile_positions(symbols=("7203",))

    assert reconciled == 1
    assert runtime.trading_process.risk_manager is not None
    assert runtime.trading_process.risk_manager.state.kill_switch_active is False


def test_runtime_reconcile_positions_halts_trading_when_positions_mismatch(
    monkeypatch,
) -> None:
    snapshot_dir = _test_dir("position_mismatch")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(_FakeExternalDataProcess(events=())),
    )
    runtime = app_main.build_application_runtime(config=config)
    runtime.position_reconciliation_service = _FailingPositionReconciliationService()

    reconciled = runtime.reconcile_positions(symbols=("7203",))

    assert reconciled == 0
    assert runtime.trading_process.risk_manager is not None
    assert runtime.trading_process.risk_manager.state.kill_switch_active is True
    assert (
        runtime.trading_process.risk_manager.state.trading_halt_reason
        == app_main.TradingHaltReason.POSITION_MISMATCH
    )


def test_runtime_persists_and_restores_halt_state(monkeypatch) -> None:
    snapshot_dir = _test_dir("restore_halt_state")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
        snapshot_enabled=True,
        recovery_enabled=True,
    )
    original_runtime = app_main.build_application_runtime(
        config=replace(config, app=replace(config.app, recovery_enabled=False))
    )
    assert original_runtime.trading_process.risk_manager is not None
    original_runtime.trading_process.risk_manager.halt_trading(
        reason=TradingHaltReason.POSITION_MISMATCH,
        message="position mismatch detected",
    )
    original_runtime.snapshot_process.save_snapshot(_timestamp())
    original_runtime.snapshot_process.start()
    original_runtime.snapshot_process.flush()
    original_runtime.snapshot_process.stop()
    fake_external = _FakeExternalDataProcess(events=())
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )

    restored_runtime = app_main.build_application_runtime(config=config)
    restored_runtime.start()
    restored_runtime.event_bus.publish(_market_data_event(price=1000.0))
    restored_runtime.event_bus.publish(_signal_event(SignalType.BUY))
    restored_runtime.flush()
    restored_runtime.stop()

    assert restored_runtime.trading_process.risk_manager is not None
    assert (
        restored_runtime.trading_process.risk_manager.state.kill_switch_active is True
    )
    assert (
        restored_runtime.trading_process.risk_manager.state.trading_halt_reason
        == TradingHaltReason.POSITION_MISMATCH
    )
    assert len(restored_runtime.trading_process.get_state("7203").orders) == 0


def test_runtime_resume_trading_succeeds_after_checks(monkeypatch) -> None:
    snapshot_dir = _test_dir("resume_success")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    fake_external = _FakeExternalDataProcess(
        events=(),
        rest_poller=_FakeRestPoller(
            order_status_repository=_FakeOrderStatusRepository(open_orders=())
        ),
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )
    runtime = app_main.build_application_runtime(config=config)
    runtime.position_reconciliation_service = _FakePositionReconciliationService()
    runtime.trading_process.start()
    assert runtime.trading_process.risk_manager is not None
    runtime.trading_process.risk_manager.halt_trading(
        reason=TradingHaltReason.POSITION_MISMATCH,
        message="manual check required",
    )

    resumed = runtime.resume_trading()
    runtime.event_bus.publish(_market_data_event(price=1000.0))
    runtime.event_bus.publish(_signal_event(SignalType.BUY))

    assert resumed is True
    assert runtime.trading_process.risk_manager.state.kill_switch_active is False
    assert runtime.trading_process.get_state("7203").position is not None
    assert runtime.trading_process.get_state("7203").position.quantity == 100


def test_runtime_resume_trading_fails_when_positions_do_not_match(monkeypatch) -> None:
    snapshot_dir = _test_dir("resume_position_mismatch")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    fake_external = _FakeExternalDataProcess(
        events=(),
        rest_poller=_FakeRestPoller(
            order_status_repository=_FakeOrderStatusRepository(open_orders=())
        ),
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )
    runtime = app_main.build_application_runtime(config=config)
    runtime.position_reconciliation_service = _FailingPositionReconciliationService()
    assert runtime.trading_process.risk_manager is not None
    runtime.trading_process.risk_manager.halt_trading(
        reason=TradingHaltReason.POSITION_MISMATCH,
        message="position mismatch detected",
    )

    resumed = runtime.resume_trading()

    assert resumed is False
    assert runtime.trading_process.risk_manager.state.kill_switch_active is True
    assert (
        runtime.trading_process.risk_manager.state.trading_halt_reason
        == TradingHaltReason.POSITION_MISMATCH
    )


def test_runtime_resume_trading_fails_when_order_sync_fails(monkeypatch) -> None:
    snapshot_dir = _test_dir("resume_order_sync_failed")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    fake_external = _FakeExternalDataProcess(
        events=(),
        raise_on_sync=RuntimeError("sync failed"),
        rest_poller=_FakeRestPoller(
            order_status_repository=_FakeOrderStatusRepository(open_orders=())
        ),
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )
    runtime = app_main.build_application_runtime(config=config)
    runtime.position_reconciliation_service = _FakePositionReconciliationService()
    assert runtime.trading_process.risk_manager is not None
    runtime.trading_process.risk_manager.halt_trading(
        reason=TradingHaltReason.POSITION_MISMATCH,
        message="position mismatch detected",
    )

    resumed = runtime.resume_trading()

    assert resumed is False
    assert (
        runtime.trading_process.risk_manager.state.trading_halt_reason
        == TradingHaltReason.ORDER_SYNC_FAILED
    )


def test_open_orders_match_api_returns_false_when_api_raises(monkeypatch) -> None:
    snapshot_dir = _test_dir("open_orders_match_api_error")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    fake_external = _FakeExternalDataProcess(
        events=(),
        rest_poller=_FakeRestPoller(
            order_status_repository=_FakeOrderStatusRepository(
                open_orders=(),
                raise_on_get=RuntimeError("api failed"),
            )
        ),
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )
    runtime = app_main.build_application_runtime(config=config)

    assert runtime._open_orders_match_api() is False


def test_runtime_resume_trading_fails_when_open_orders_api_raises(monkeypatch) -> None:
    snapshot_dir = _test_dir("resume_open_orders_api_error")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    fake_external = _FakeExternalDataProcess(
        events=(),
        rest_poller=_FakeRestPoller(
            order_status_repository=_FakeOrderStatusRepository(
                open_orders=(),
                raise_on_get=RuntimeError("api failed"),
            )
        ),
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(fake_external),
    )
    runtime = app_main.build_application_runtime(config=config)
    runtime.position_reconciliation_service = _FakePositionReconciliationService()
    assert runtime.trading_process.risk_manager is not None
    runtime.trading_process.risk_manager.halt_trading(
        reason=TradingHaltReason.POSITION_MISMATCH,
        message="position mismatch detected",
    )

    resumed = runtime.resume_trading()

    assert resumed is False
    assert runtime.trading_process.risk_manager.state.kill_switch_active is True
    assert (
        runtime.trading_process.risk_manager.state.trading_halt_reason
        == TradingHaltReason.ORDER_SYNC_FAILED
    )


def test_runtime_reconcile_positions_skips_when_disabled(monkeypatch) -> None:
    snapshot_dir = _test_dir("position_reconcile_disabled")
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=snapshot_dir,
    )
    config = replace(
        config,
        app=replace(config.app, position_reconciliation_enabled=False),
    )
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(_FakeExternalDataProcess(events=())),
    )
    runtime = app_main.build_application_runtime(config=config)
    service = _FakePositionReconciliationService()
    runtime.position_reconciliation_service = service

    reconciled = runtime.reconcile_positions(symbols=("7203",))

    assert reconciled == 0
    assert service.call_count == 0


def test_initialize_application_flushes_when_keyboard_interrupt(monkeypatch) -> None:
    config = _runtime_config(
        data_source_mode=DataSourceMode.API,
        snapshot_dir=_test_dir("keyboard_interrupt"),
    )
    runtime = _FakeRuntimeForInterrupt(config=config)
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "validate_config", lambda _: None)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, csv_path=None: runtime,
    )

    try:
        app_main.initialize_application(block_api=True)
    except KeyboardInterrupt:
        pass

    assert runtime.calls == [
        "start",
        "run_external_data",
        "wait",
        "stop",
        "flush",
    ]


def test_runtime_api_order_dry_run_allows_paper_environment_only(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_paper",
        trading_mode=TradingMode.LIVE,
        kabu_api_environment=app_main.KabuApiEnvironment.PAPER,
        resource_options={"use_real_validator": True},
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert payload["ok"] is True
    assert payload["order_id"] == "api-order-1"
    assert _current_dry_run_resources().allow_api_paper_orders is True


def test_runtime_api_order_precheck_returns_ok_for_paper_environment(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_ok",
        trading_mode=TradingMode.LIVE,
        kabu_api_environment=app_main.KabuApiEnvironment.PAPER,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321",
        side=OrderSide.BUY,
        quantity=1,
    )
    resources = _current_dry_run_resources()

    assert payload["ok"] is True
    assert payload["executed_at"]
    assert payload["git_commit"]
    assert payload["config_summary"]["token_env_name"] == "KABU_API_PASSWORD_PAPER"
    assert (
        payload["record_hint"]
        == "docs/paper_api_test_record_template.md に結果を記録してください"
    )
    assert payload["next_action"] == ["api-order-dry-run を実行できます"]
    assert resources.gateway.place_order_calls == 0
    assert runtime.trading_process.get_state("1321").orders == []


def test_runtime_api_order_precheck_rejects_live_environment(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_live",
        trading_mode=TradingMode.LIVE,
        kabu_api_environment=app_main.KabuApiEnvironment.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321",
        side=OrderSide.BUY,
        quantity=1,
    )

    assert payload["ok"] is False
    assert any(
        check["name"] == "environment_is_paper" and not check["ok"]
        for check in payload["checks"]
    )
    assert (
        "config/app.yaml の kabu_api_environment を paper にしてください"
        in payload["next_action"]
    )


def test_runtime_api_order_precheck_rejects_live_port_base_url(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_live_port",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )
    runtime.config = replace(
        runtime.config,
        app=replace(
            runtime.config.app,
            kabu_api=replace(
                runtime.config.app.kabu_api, base_url="http://localhost:18080"
            ),
        ),
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321",
        side=OrderSide.BUY,
        quantity=1,
    )

    assert payload["ok"] is False
    assert any(
        check["name"] == "base_url_is_paper_port" and not check["ok"]
        for check in payload["checks"]
    )
    assert (
        "base_url が検証PORT 18081 を向いているか確認してください"
        in payload["next_action"]
    )


def test_runtime_api_order_precheck_rejects_symbol_outside_trade_symbols(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_symbol_ng",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="9999",
        side=OrderSide.BUY,
        quantity=1,
    )

    assert payload["ok"] is False
    assert any(
        check["name"] == "symbol_allowed" and not check["ok"]
        for check in payload["checks"]
    )
    assert (
        "config/app.yaml の trade_symbols を確認してください" in payload["next_action"]
    )


def test_runtime_api_order_precheck_rejects_zero_quantity(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_zero_quantity",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321",
        side=OrderSide.BUY,
        quantity=0,
    )

    assert payload["ok"] is False
    assert any(
        check["name"] == "quantity_valid" and not check["ok"]
        for check in payload["checks"]
    )
    assert (
        "数量が 1以上 max_order_quantity 以下か確認してください"
        in payload["next_action"]
    )


def test_runtime_api_order_precheck_rejects_quantity_over_max_order_quantity(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_over_quantity",
        max_order_quantity=1,
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321",
        side=OrderSide.BUY,
        quantity=2,
    )

    assert payload["ok"] is False
    assert any(
        check["name"] == "quantity_valid" and not check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_precheck_rejects_when_halted(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_halted",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )
    assert runtime.trading_process.risk_manager is not None
    runtime.trading_process.risk_manager.halt_trading(
        reason=TradingHaltReason.MANUAL,
        message="manual halt",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321",
        side=OrderSide.BUY,
        quantity=1,
    )

    assert payload["ok"] is False
    assert any(
        check["name"] == "not_halted" and not check["ok"] for check in payload["checks"]
    )
    assert (
        "halt-status で停止理由を確認し、必要なら原因調査後に resume してください"
        in payload["next_action"]
    )


def test_runtime_api_order_precheck_rejects_when_position_fetch_fails(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_position_fetch_ng",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
        resource_options={
            "position_fetch_error": RuntimeError("position fetch failed")
        },
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321",
        side=OrderSide.BUY,
        quantity=1,
    )
    resources = _current_dry_run_resources()

    assert payload["ok"] is False
    assert any(
        check["name"] == "position_fetch" and not check["ok"]
        for check in payload["checks"]
    )
    assert resources.gateway.place_order_calls == 0
    assert runtime.trading_process.get_state("1321").orders == []


def test_runtime_api_order_precheck_rejects_when_order_status_fetch_fails(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_order_status_ng",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
        resource_options={
            "order_status_fetch_error": RuntimeError("order status fetch failed")
        },
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321",
        side=OrderSide.BUY,
        quantity=1,
    )

    assert payload["ok"] is False
    assert any(
        check["name"] == "order_status_fetch" and not check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_precheck_returns_api_guidance_when_preflight_fails(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_preflight_ng",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
        resource_options={"open_orders_exist": True},
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321",
        side=OrderSide.BUY,
        quantity=1,
    )

    assert payload["ok"] is False
    assert any(
        check["name"] == "preflight_check" and not check["ok"]
        for check in payload["checks"]
    )
    assert (
        "kabuステーションを検証モードで起動し、API接続を確認してください"
        in payload["next_action"]
    )


def test_runtime_api_order_precheck_accepts_paper_token_env_name(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_token_name_ok",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert any(
        check["name"] == "token_env_name_is_paper" and check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_precheck_rejects_live_token_env_name(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_token_name_ng",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_LIVE",
        token_env_value="dummy-live-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert payload["ok"] is False
    assert any(
        check["name"] == "token_env_name_is_paper" and not check["ok"]
        for check in payload["checks"]
    )
    assert (
        "config/app.yaml の token_env_name を KABU_API_PASSWORD_PAPER にしてください"
        in payload["next_action"]
    )


def test_runtime_api_order_precheck_rejects_when_token_env_is_missing(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_token_env_missing",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value=None,
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert payload["ok"] is False
    assert any(
        check["name"] == "token_env_exists" and not check["ok"]
        for check in payload["checks"]
    )
    assert (
        'PowerShellで $env:KABU_API_PASSWORD_PAPER="検証用APIパスワード" を設定してください'
        in payload["next_action"]
    )


def test_runtime_api_order_precheck_accepts_when_token_env_is_set(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_token_env_ok",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert any(
        check["name"] == "token_env_exists" and check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_precheck_rejects_paper_trading_mode(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_trading_mode_ng",
        trading_mode=TradingMode.PAPER,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert any(
        check["name"] == "trading_mode_is_live" and not check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_precheck_rejects_when_live_enabled_is_false(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_live_enabled_ng",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=False,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert any(
        check["name"] == "live_enabled_is_true" and not check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_precheck_rejects_csv_data_source_mode(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_data_source_ng",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.CSV,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert any(
        check["name"] == "data_source_mode_is_api" and not check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_precheck_rejects_1306_quantity_not_matching_lot_unit(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_1306_lot_ng",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1306", side=OrderSide.BUY, quantity=1
    )

    assert any(
        check["name"] == "quantity_matches_lot_unit" and not check["ok"]
        for check in payload["checks"]
    )
    assert "指定銘柄の売買単位に合う数量を指定してください" in payload["next_action"]


def test_runtime_api_order_precheck_accepts_1306_quantity_matching_lot_unit(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_1306_lot_ok",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1306", side=OrderSide.BUY, quantity=10
    )

    assert any(
        check["name"] == "quantity_matches_lot_unit" and check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_precheck_accepts_safe_quantity_for_1321(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_1321_safe_ok",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert any(
        check["name"] == "quantity_is_safe_for_symbol" and check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_precheck_rejects_unsafe_quantity_for_1321(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_1321_safe_ng",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1321", side=OrderSide.BUY, quantity=10
    )

    assert any(
        check["name"] == "quantity_is_safe_for_symbol" and not check["ok"]
        for check in payload["checks"]
    )
    assert "検証用の推奨数量に変更してください" in payload["next_action"]


def test_runtime_api_order_precheck_accepts_safe_quantity_for_1570(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_1570_safe_ok",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1570", side=OrderSide.BUY, quantity=1
    )

    assert any(
        check["name"] == "quantity_is_safe_for_symbol" and check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_precheck_rejects_unsafe_quantity_for_1570(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_precheck_1570_safe_ng",
        trading_mode=TradingMode.LIVE,
        data_source_mode=DataSourceMode.API,
        live_enabled=True,
        token_env_name="KABU_API_PASSWORD_PAPER",
        token_env_value="dummy-paper-password",
    )

    payload = runtime.run_api_order_precheck(
        symbol="1570", side=OrderSide.BUY, quantity=10
    )

    assert any(
        check["name"] == "quantity_is_safe_for_symbol" and not check["ok"]
        for check in payload["checks"]
    )


def test_runtime_api_order_dry_run_does_not_fail_with_not_live_guard(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_not_live_guard",
        trading_mode=TradingMode.LIVE,
        kabu_api_environment=app_main.KabuApiEnvironment.PAPER,
        resource_options={"use_real_validator": True},
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert payload["ok"] is True
    assert "kabu_api_environment is not live" not in payload["errors"]


def test_runtime_api_order_dry_run_rejects_live_environment(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_live",
        trading_mode=TradingMode.LIVE,
        kabu_api_environment=app_main.KabuApiEnvironment.LIVE,
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert payload["ok"] is False
    assert "kabu_api_environment must be paper" in payload["errors"]


def test_runtime_api_order_dry_run_rejects_symbol_outside_trade_symbols(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_symbol",
    )

    payload = runtime.run_api_order_dry_run(
        symbol="9999", side=OrderSide.BUY, quantity=1
    )

    assert payload["ok"] is False
    assert "symbol is not in trade_symbols" in payload["errors"]


def test_runtime_api_order_dry_run_rejects_quantity_over_max_order_quantity(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_quantity",
        max_order_quantity=1,
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=2
    )

    assert payload["ok"] is False
    assert "quantity exceeds max_order_quantity" in payload["errors"]


def test_runtime_api_order_dry_run_rejects_when_halted(monkeypatch) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_halted",
    )
    assert runtime.trading_process.risk_manager is not None
    runtime.trading_process.risk_manager.halt_trading(
        reason=TradingHaltReason.MANUAL,
        message="manual halt",
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert payload["ok"] is False
    assert "trading is halted" in payload["errors"]


def test_runtime_api_order_dry_run_does_not_call_order_api_when_preflight_fails(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_preflight_ng",
        resource_options={"preflight_error": RuntimeError("preflight failed")},
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )
    resources = _current_dry_run_resources()

    assert payload["ok"] is False
    assert "preflight failed" in payload["errors"][0]
    assert resources.gateway.place_order_calls == 0


def test_runtime_api_order_dry_run_does_not_call_order_api_when_order_safety_fails(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_guard_ng",
        resource_options={"guard_reason": "incomplete order exists"},
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )
    resources = _current_dry_run_resources()

    assert payload["ok"] is False
    assert "incomplete order exists" in payload["errors"]
    assert resources.gateway.place_order_calls == 0


def test_runtime_api_order_dry_run_calls_order_sync_and_reconciliation_on_success(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_success",
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )
    resources = _current_dry_run_resources()

    assert payload["ok"] is True
    assert payload["executed_at"]
    assert payload["git_commit"]
    assert payload["config_summary"]["token_env_name"] == "KABU_API_PASSWORD"
    assert (
        payload["record_hint"]
        == "docs/paper_api_test_record_template.md に結果を記録してください"
    )
    assert resources.gateway.place_order_calls == 1
    assert resources.order_status_repository.get_order_status_calls == 1
    assert resources.position_reconciliation_service.call_count >= 2
    assert "時間を置いて注文状態同期を確認してください" in payload["next_action"]


def test_runtime_api_order_dry_run_does_not_add_order_before_api_send(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_no_pre_append",
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )
    resources = _current_dry_run_resources()

    assert payload["ok"] is True
    assert resources.gateway.orders_count_before_place_order == 0


def test_runtime_api_order_dry_run_gateway_internal_validate_also_passes(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_gateway_validate",
        resource_options={"use_real_validator": True},
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert payload["ok"] is True


def test_runtime_api_order_dry_run_rejects_when_existing_open_order_exists(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_existing_open_order",
        resource_options={"use_real_validator": True, "open_orders_exist": True},
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )
    resources = _current_dry_run_resources()

    assert payload["ok"] is False
    assert resources.gateway.place_order_calls == 0


def test_runtime_api_order_dry_run_returns_error_when_gateway_raises(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_gateway_error",
        resource_options={"gateway_error": RuntimeError("api failed")},
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert payload["ok"] is False
    assert payload["errors"] == ["api failed"]
    assert (
        "kabuステーションが検証モードで起動しているか確認してください"
        in payload["next_action"]
    )


def test_runtime_api_order_dry_run_returns_reconciliation_failure_next_action(
    monkeypatch,
) -> None:
    runtime = _build_api_order_dry_run_runtime(
        monkeypatch,
        snapshot_name="api_order_dry_run_reconcile_ng",
        resource_options={
            "post_order_reconciliation_error": RuntimeError(
                "position reconciliation failed"
            )
        },
    )

    payload = runtime.run_api_order_dry_run(
        symbol="1321", side=OrderSide.BUY, quantity=1
    )

    assert payload["ok"] is False
    assert payload["reconciliation_result"] == "NG"
    assert "取引停止状態を確認してください" in payload["next_action"]


class _FakeExternalDataProcess:
    def __init__(
        self,
        events: tuple[BaseEvent[Any], ...],
        sync_events: tuple[BaseEvent[Any], ...] = (),
        raise_on_sync: Exception | None = None,
        rest_poller=None,
    ) -> None:
        self.events = events
        self.sync_events = sync_events
        self.raise_on_sync = raise_on_sync
        self.rest_poller = rest_poller
        self.event_bus: EventBus | None = None
        self.run_count = 0
        self.sync_count = 0
        self.stop_count = 0

    def run(
        self,
        data_source_mode: DataSourceMode,
        csv_path: Path | None,
        symbol: str,
    ) -> None:
        if self.event_bus is None:
            raise AssertionError("event_bus is not configured")
        self.run_count += 1
        for event in self.events:
            self.event_bus.publish(event)

    def sync_orders_once(self, force_refresh: bool = False) -> int:
        if self.event_bus is None:
            raise AssertionError("event_bus is not configured")
        if self.raise_on_sync is not None:
            raise self.raise_on_sync
        self.sync_count += 1
        for event in self.sync_events:
            self.event_bus.publish(event)
        return len(self.sync_events)

    def stop(self) -> None:
        self.stop_count += 1


class _FakeRuntimeForInterrupt:
    def __init__(self, config: SystemConfig) -> None:
        self.config = config
        self.event_bus = EventBus()
        self.logger = _FakeLogger()
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")

    def run_external_data(self) -> None:
        self.calls.append("run_external_data")

    def wait(self) -> None:
        self.calls.append("wait")
        raise KeyboardInterrupt

    def flush(self) -> None:
        self.calls.append("flush")

    def stop(self) -> None:
        self.calls.append("stop")
        self.flush()


_API_ORDER_DRY_RUN_RESOURCES: "_FakeApiOrderDryRunResources | None" = None


def _build_api_order_dry_run_runtime(
    monkeypatch,
    snapshot_name: str,
    trading_mode: TradingMode = TradingMode.LIVE,
    kabu_api_environment=app_main.KabuApiEnvironment.PAPER,
    max_order_quantity: int = 10,
    data_source_mode: DataSourceMode = DataSourceMode.CSV,
    live_enabled: bool = False,
    token_env_name: str = "KABU_API_PASSWORD",
    token_env_value: str | None = None,
    resource_options: dict[str, Any] | None = None,
):
    snapshot_dir = _test_dir(snapshot_name)
    config = _runtime_config(
        data_source_mode=data_source_mode,
        snapshot_dir=snapshot_dir,
    )
    config = replace(
        config,
        app=replace(
            config.app,
            trading_mode=trading_mode,
            live_enabled=live_enabled,
            kabu_api=replace(
                config.app.kabu_api,
                environment=kabu_api_environment,
                token_env_name=token_env_name,
            ),
            max_order_quantity=max_order_quantity,
            trade_symbols=("1306", "1321", "1570"),
        ),
    )
    if token_env_value is None:
        monkeypatch.delenv(token_env_name, raising=False)
    else:
        monkeypatch.setenv(token_env_name, token_env_value)
    monkeypatch.setattr(
        app_main,
        "_build_external_data_process",
        _fake_external_builder(_FakeExternalDataProcess(events=())),
    )
    monkeypatch.setattr(
        app_main,
        "_build_position_reconciliation_service",
        lambda config, logger: None,
    )
    monkeypatch.setattr(
        app_main,
        "_build_api_order_dry_run_resources",
        lambda config, logger, trading_process: _set_current_dry_run_resources(
            _FakeApiOrderDryRunResources(
                event_factory=trading_process.event_factory,
                trading_process=trading_process,
                **(resource_options or {}),
            )
        ),
    )
    return app_main.build_application_runtime(
        config=config,
        allow_real_order_disabled=True,
    )


def _set_current_dry_run_resources(resources: "_FakeApiOrderDryRunResources"):
    global _API_ORDER_DRY_RUN_RESOURCES
    _API_ORDER_DRY_RUN_RESOURCES = resources
    return resources


def _current_dry_run_resources() -> "_FakeApiOrderDryRunResources":
    if _API_ORDER_DRY_RUN_RESOURCES is None:
        raise AssertionError("api-order-dry-run resources are not initialized")
    return _API_ORDER_DRY_RUN_RESOURCES


class _FakeApiOrderDryRunResources:
    def __init__(
        self,
        event_factory: EventFactory,
        trading_process,
        preflight_error: Exception | None = None,
        order_status_fetch_error: Exception | None = None,
        position_fetch_error: Exception | None = None,
        guard_reason: str | None = None,
        gateway_error: Exception | None = None,
        use_real_validator: bool = False,
        open_orders_exist: bool = False,
        post_order_reconciliation_error: Exception | None = None,
    ) -> None:
        self.order_status_repository = _FakeDryRunOrderStatusRepository(
            preflight_error=preflight_error,
            order_status_fetch_error=order_status_fetch_error,
            open_orders_exist=open_orders_exist,
        )
        self.position_repository = _FakeDryRunPositionRepository(
            error=position_fetch_error
        )
        self.position_reconciliation_service = _FakeDryRunPositionReconciliationService(
            position_repository=self.position_repository,
            preflight_error=preflight_error,
            post_order_error=post_order_reconciliation_error,
        )
        if use_real_validator:
            self.order_safety_validator = app_main.OrderSafetyValidator(
                trading_mode=TradingMode.LIVE,
                kabu_api_environment=app_main.KabuApiEnvironment.PAPER,
                max_order_quantity=10,
                trade_symbols=("1306", "1321", "1570"),
                enabled_symbols=("7203", "1306", "1321", "1570"),
                allow_api_paper_orders=True,
                state_provider=app_main._build_order_safety_state_provider(
                    position_repository=self.position_repository,  # type: ignore[arg-type]
                    order_status_repository=self.order_status_repository,  # type: ignore[arg-type]
                    risk_manager=trading_process.risk_manager,
                ),
            )
        else:
            self.order_safety_validator = _FakeDryRunOrderSafetyValidator(
                reason=guard_reason
            )
        self.allow_api_paper_orders = getattr(
            self.order_safety_validator,
            "allow_api_paper_orders",
            False,
        )
        self.gateway = _FakeDryRunGateway(
            event_factory=event_factory,
            trading_process=trading_process,
            validator=self.order_safety_validator,
            error=gateway_error,
        )
        self.order_gateway = self.gateway


class _FakeDryRunOrderStatusRepository:
    def __init__(
        self,
        preflight_error: Exception | None = None,
        order_status_fetch_error: Exception | None = None,
        open_orders_exist: bool = False,
    ) -> None:
        self.preflight_error = preflight_error
        self.order_status_fetch_error = order_status_fetch_error
        self.open_orders_exist = open_orders_exist
        self.list_orders_calls = 0
        self.get_order_status_calls = 0

    def list_orders(
        self,
        symbol: str | None = None,
        force_refresh: bool = False,
    ) -> tuple[Order, ...]:
        self.list_orders_calls += 1
        if symbol is not None and self.order_status_fetch_error is not None:
            raise self.order_status_fetch_error
        if self.preflight_error is not None:
            raise self.preflight_error
        return ()

    def get_open_orders(
        self,
        symbol: str | None = None,
        force_refresh: bool = False,
    ) -> list[Order]:
        if self.preflight_error is not None:
            raise self.preflight_error
        if self.open_orders_exist:
            return [
                Order(
                    order_id="existing-open-order",
                    external_order_id="existing-open-order",
                    symbol=symbol or "1321",
                    side=OrderSide.BUY,
                    quantity=1,
                    order_type="MARKET",
                    status=OrderStatus.REQUESTED,
                    remaining_quantity=1,
                )
            ]
        return []

    def get_order_status(
        self,
        order_id: str,
        force_refresh: bool = False,
    ) -> Order | None:
        self.get_order_status_calls += 1
        return Order(
            order_id="api-order-1",
            external_order_id="api-order-1",
            symbol="1321",
            side=OrderSide.BUY,
            quantity=1,
            order_type="MARKET",
            status=OrderStatus.REQUESTED,
            filled_quantity=0,
            remaining_quantity=1,
        )


class _FakeDryRunPositionRepository:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.get_position_calls = 0

    def get_position(self, symbol: str, force_refresh: bool = False) -> Position:
        self.get_position_calls += 1
        if self.error is not None:
            raise self.error
        return Position(symbol=symbol, quantity=0, average_price=0.0)


class _FakeDryRunPositionReconciliationService:
    def __init__(
        self,
        position_repository: _FakeDryRunPositionRepository,
        preflight_error: Exception | None = None,
        post_order_error: Exception | None = None,
    ) -> None:
        self.position_repository = position_repository
        self.preflight_error = preflight_error
        self.post_order_error = post_order_error
        self.call_count = 0

    def reconcile(self, symbol: str, internal_position) -> None:
        self.call_count += 1
        if self.preflight_error is not None:
            raise self.preflight_error
        if self.call_count > 1 and self.post_order_error is not None:
            raise self.post_order_error
        self.position_repository.get_position(symbol, force_refresh=True)


class _FakeDryRunOrderSafetyValidator:
    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason

    def evaluate_order(self, order: Order):
        return type(
            "_Decision",
            (),
            {"allowed": self.reason is None, "reason": self.reason},
        )()


class _FakeDryRunGateway:
    def __init__(
        self,
        event_factory: EventFactory,
        trading_process,
        validator,
        error: Exception | None = None,
    ) -> None:
        self.event_factory = event_factory
        self.trading_process = trading_process
        self.validator = validator
        self.error = error
        self.place_order_calls = 0
        self.orders_count_before_place_order: int | None = None

    def place_order(self, order: Order, timestamp: datetime):
        self.place_order_calls += 1
        self.orders_count_before_place_order = len(
            self.trading_process.get_state(order.symbol).orders
        )
        if self.error is not None:
            raise self.error
        if hasattr(self.validator, "validate_order"):
            self.validator.validate_order(order)
        return (
            self.event_factory.create(
                event_type=EventType.ORDER_STATUS_UPDATED,
                timestamp=timestamp,
                symbol=order.symbol,
                payload=OrderStatusPayload(
                    order_id=order.order_id,
                    status=OrderStatus.REQUESTED,
                    filled_quantity=0,
                    remaining_quantity=order.quantity,
                    avg_price=None,
                    side=order.side,
                    order_quantity=order.quantity,
                    is_exit=False,
                    external_order_id="api-order-1",
                ),
            ),
        )


class _FakeLogger:
    def info(self, *args: Any, **kwargs: Any) -> None:
        pass

    def exception(self, *args: Any, **kwargs: Any) -> None:
        pass


def _fake_external_builder(
    fake_external: _FakeExternalDataProcess,
) -> Callable[..., _FakeExternalDataProcess]:
    def build(**kwargs: Any) -> _FakeExternalDataProcess:
        fake_external.event_bus = kwargs["event_bus"]
        return fake_external

    return build


class _FakePositionReconciliationService:
    def __init__(self) -> None:
        self.call_count = 0

    def reconcile(self, symbol: str, internal_position) -> None:
        self.call_count += 1


class _FailingPositionReconciliationService:
    def reconcile(self, symbol: str, internal_position) -> None:
        raise app_main.PositionReconciliationError(f"mismatch: {symbol}")


class _FakeOrderStatusRepository:
    def __init__(
        self,
        open_orders: tuple[Order, ...],
        raise_on_get: Exception | None = None,
    ) -> None:
        self.open_orders = open_orders
        self.raise_on_get = raise_on_get

    def get_open_orders(
        self,
        symbol: str | None = None,
        force_refresh: bool = False,
    ) -> list[Order]:
        if self.raise_on_get is not None:
            raise self.raise_on_get
        if symbol is None:
            return list(self.open_orders)
        return [order for order in self.open_orders if order.symbol == symbol]


class _FakeRestPoller:
    def __init__(self, order_status_repository: _FakeOrderStatusRepository) -> None:
        self.order_status_repository = order_status_repository
        self.on_cycle_completed = None


def _runtime_config(
    data_source_mode: DataSourceMode,
    snapshot_dir: Path,
    snapshot_enabled: bool = False,
    recovery_enabled: bool = False,
) -> SystemConfig:
    config = load_config(Path("config"))
    app = replace(
        config.app,
        data_source_mode=data_source_mode,
        trading_mode=TradingMode.PAPER,
        trade_symbols=("7203",),
        max_order_quantity=100,
        snapshot_enabled=snapshot_enabled,
        snapshot_dir=str(snapshot_dir),
        recovery_enabled=recovery_enabled,
    )
    strategy = replace(
        config.strategy,
        trend=replace(config.strategy.trend, short_window=1, long_window=2),
    )
    risk = replace(
        config.risk,
        trading_start_time="00:00",
        trading_end_time="23:59",
    )
    return replace(config, app=app, strategy=strategy, risk=risk)


def _market_data_event(price: float) -> BaseEvent[Any]:
    timestamp = _timestamp()
    return EventFactory(source=EventSource.EXTERNAL_DATA).create(
        event_type=EventType.MARKET_DATA_UPDATED,
        timestamp=timestamp,
        symbol="7203",
        payload=MarketDataPayload(
            price=price,
            bid=None,
            ask=None,
            volume=None,
            timestamp=timestamp,
        ),
    )


def _order_status_event(order_id: str) -> BaseEvent[Any]:
    timestamp = _timestamp()
    return EventFactory(source=EventSource.EXTERNAL_DATA).create(
        event_type=EventType.ORDER_STATUS_UPDATED,
        timestamp=timestamp,
        symbol="7203",
        payload=OrderStatusPayload(
            order_id=order_id,
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1000.0,
            is_exit=False,
        ),
    )


def _signal_event(signal_type: SignalType) -> BaseEvent[Any]:
    return EventFactory(source=EventSource.SIGNAL).create(
        event_type=EventType.SIGNAL_DETECTED,
        timestamp=_timestamp(),
        symbol="7203",
        payload=SignalPayload(
            signal_type=signal_type,
            strategy_type=app_main.StrategyType.TREND,
            indicators=(IndicatorValue(name="current_price", value=1000.0),),
        ),
    )


def _timestamp() -> datetime:
    return datetime(2026, 4, 18, tzinfo=timezone.utc)


def _test_dir(name: str) -> Path:
    directory = Path("tests/.tmp_main_runtime") / name
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.iterdir():
        if path.is_file():
            path.unlink()
    return directory

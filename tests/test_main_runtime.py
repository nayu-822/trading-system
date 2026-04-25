from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any

import main as app_main
from domain.enums import (
    DataSourceMode,
    EventSource,
    EventType,
    OrderSide,
    OrderStatus,
    TradingMode,
)
from domain.events import (
    BaseEvent,
    EventFactory,
    MarketDataPayload,
    OrderStatusPayload,
)
from domain.models import Order, SystemConfig
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


def test_runtime_reconcile_positions_keeps_trading_when_positions_match(monkeypatch) -> None:
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


class _FakeExternalDataProcess:
    def __init__(
        self,
        events: tuple[BaseEvent[Any], ...],
        sync_events: tuple[BaseEvent[Any], ...] = (),
    ) -> None:
        self.events = events
        self.sync_events = sync_events
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
        snapshot_enabled=snapshot_enabled,
        snapshot_dir=str(snapshot_dir),
        recovery_enabled=recovery_enabled,
    )
    return replace(config, app=app)


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


def _timestamp() -> datetime:
    return datetime(2026, 4, 18, tzinfo=timezone.utc)


def _test_dir(name: str) -> Path:
    directory = Path("tests/.tmp_main_runtime") / name
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.iterdir():
        if path.is_file():
            path.unlink()
    return directory

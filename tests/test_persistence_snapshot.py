from datetime import datetime, timezone
from pathlib import Path

from domain.enums import EventSource, EventType, OrderSide, SignalType, StrategyType
from domain.events import (
    BaseEvent,
    ErrorOccurred,
    ErrorPayload,
    EventFactory,
    MarketDataPayload,
    OrderRequested,
    OrderRequestedPayload,
    PositionPayload,
    SignalPayload,
)
from domain.models import Position, TradingSymbolState
from infrastructure.event_bus import EventBus
from infrastructure.file_storage import FileStorage
from processes.persistence_process import PersistenceProcess
from processes.snapshot_process import SnapshotProcess
from processes.trading_process import TradingProcess


def test_persistence_process_saves_event_to_json_lines() -> None:
    event_bus = EventBus()
    storage = FileStorage()
    event_log_path = _test_file_path("events.jsonl")
    _remove_file(event_log_path)
    persistence_process = PersistenceProcess(
        event_bus=event_bus,
        storage=storage,
        event_log_path=event_log_path,
    )

    persistence_process.start()
    event_bus.publish(_create_market_data_event(price=1000.0))
    persistence_process.flush()
    persistence_process.stop()

    lines = event_log_path.read_text(encoding="utf-8").splitlines()
    _remove_file(event_log_path)

    assert len(lines) == 1
    assert "MarketDataUpdated" in lines[0]
    assert "1000.0" in lines[0]


def test_persistence_process_saves_state_and_error_events() -> None:
    event_bus = EventBus()
    event_log_path = _test_file_path("state_error_events.jsonl")
    _remove_file(event_log_path)
    persistence_process = PersistenceProcess(
        event_bus=event_bus,
        storage=FileStorage(),
        event_log_path=event_log_path,
    )

    persistence_process.start()
    event_bus.publish(_create_position_updated_event())
    event_bus.publish(_create_error_event())
    persistence_process.flush()
    persistence_process.stop()

    lines = event_log_path.read_text(encoding="utf-8").splitlines()
    _remove_file(event_log_path)

    assert len(lines) == 2
    assert "PositionUpdated" in lines[0]
    assert "ErrorOccurred" in lines[1]


def test_persistence_process_does_not_duplicate_after_restart() -> None:
    event_bus = EventBus()
    event_log_path = _test_file_path("restart_events.jsonl")
    _remove_file(event_log_path)
    persistence_process = PersistenceProcess(
        event_bus=event_bus,
        storage=FileStorage(),
        event_log_path=event_log_path,
    )

    persistence_process.start()
    persistence_process.stop()
    persistence_process.start()
    event_bus.publish(_create_market_data_event(price=1000.0))
    persistence_process.flush()
    persistence_process.stop()

    lines = event_log_path.read_text(encoding="utf-8").splitlines()
    _remove_file(event_log_path)

    assert len(lines) == 1


def test_persistence_process_start_stop_start_keeps_single_subscription() -> None:
    event_bus = EventBus()
    event_log_path = _test_file_path("restart_subscription_events.jsonl")
    _remove_file(event_log_path)
    persistence_process = PersistenceProcess(
        event_bus=event_bus,
        storage=FileStorage(),
        event_log_path=event_log_path,
    )

    persistence_process.start()
    persistence_process.stop()
    persistence_process.start()

    assert len(event_bus._subscribers[EventType.MARKET_DATA_UPDATED]) == 1

    persistence_process.stop()


def test_snapshot_process_saves_trading_snapshot() -> None:
    event_bus = EventBus()
    snapshot_path = _test_file_path("trading_snapshot_save.json")
    _remove_file(snapshot_path)
    trading_process = TradingProcess(event_bus=event_bus)
    state = trading_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = 100
    state.position.average_price = 1000.0
    snapshot_process = SnapshotProcess(
        event_bus=event_bus,
        trading_process=trading_process,
        storage=FileStorage(),
        snapshot_path=snapshot_path,
        event_threshold=1,
    )

    snapshot_process.start()
    event_bus.publish(_create_order_requested_event())
    snapshot_process.flush()
    snapshot_process.stop()

    saved_data = FileStorage().read_json(snapshot_path)
    _remove_file(snapshot_path)

    assert saved_data is not None
    assert saved_data["version"] == 1
    assert saved_data["symbols"][0]["symbol"] == "7203"
    assert saved_data["symbols"][0]["position"]["quantity"] == 100


def test_snapshot_process_does_not_duplicate_after_restart() -> None:
    event_bus = EventBus()
    snapshot_path = _test_file_path("trading_snapshot_restart.json")
    _remove_file(snapshot_path)
    trading_process = TradingProcess(event_bus=event_bus)
    snapshot_process = SnapshotProcess(
        event_bus=event_bus,
        trading_process=trading_process,
        storage=FileStorage(),
        snapshot_path=snapshot_path,
        event_threshold=2,
    )

    snapshot_process.start()
    snapshot_process.stop()
    snapshot_process.start()
    event_bus.publish(_create_order_requested_event())

    assert snapshot_process._event_count == 1
    snapshot_process.stop()
    _remove_file(snapshot_path)


def test_snapshot_process_start_stop_start_keeps_single_subscription() -> None:
    event_bus = EventBus()
    snapshot_path = _test_file_path("trading_snapshot_restart_subscription.json")
    _remove_file(snapshot_path)
    trading_process = TradingProcess(event_bus=event_bus)
    snapshot_process = SnapshotProcess(
        event_bus=event_bus,
        trading_process=trading_process,
        storage=FileStorage(),
        snapshot_path=snapshot_path,
        event_threshold=2,
    )

    snapshot_process.start()
    snapshot_process.stop()
    snapshot_process.start()

    assert len(event_bus._subscribers[EventType.ORDER_REQUESTED]) == 1
    assert len(event_bus._subscribers[EventType.ORDER_STATUS_UPDATED]) == 1
    assert len(event_bus._subscribers[EventType.POSITION_UPDATED]) == 1

    snapshot_process.stop()
    _remove_file(snapshot_path)


def test_trading_process_restores_snapshot() -> None:
    storage = FileStorage()
    snapshot_path = _test_file_path("trading_snapshot_restore.json")
    _remove_file(snapshot_path)
    original_process = TradingProcess(event_bus=EventBus())
    state = original_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = 100
    state.position.average_price = 1000.0
    snapshot = original_process.get_snapshot(_timestamp())
    storage.overwrite_json(snapshot_path, snapshot.to_dict())

    restored_process = TradingProcess(event_bus=EventBus())
    snapshot_process = SnapshotProcess(
        event_bus=EventBus(),
        trading_process=restored_process,
        storage=storage,
        snapshot_path=snapshot_path,
    )

    assert snapshot_process.restore() is True
    restored_state = restored_process.get_state("7203")

    assert restored_state.position is not None
    assert restored_state.position.quantity == 100
    assert restored_state.position.average_price == 1000.0
    _remove_file(snapshot_path)


def test_trading_process_continues_after_snapshot_restore() -> None:
    storage = FileStorage()
    snapshot_path = _test_file_path("trading_snapshot_continue.json")
    _remove_file(snapshot_path)
    original_process = TradingProcess(event_bus=EventBus())
    original_process.states.append(
        TradingSymbolState(
            symbol="7203",
            lot_size=100,
            position=Position(
                symbol="7203",
                quantity=100,
                average_price=1000.0,
            ),
        )
    )
    storage.overwrite_json(
        snapshot_path, original_process.get_snapshot(_timestamp()).to_dict()
    )
    event_bus = EventBus()
    restored_process = TradingProcess(event_bus=event_bus)
    snapshot_process = SnapshotProcess(
        event_bus=event_bus,
        trading_process=restored_process,
        storage=storage,
        snapshot_path=snapshot_path,
    )
    received_orders: list[BaseEvent] = []

    assert snapshot_process.restore() is True
    restored_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_orders.append)
    event_bus.publish(_create_signal_event(SignalType.EXIT))

    assert len(received_orders) == 1
    assert isinstance(received_orders[0], OrderRequested)
    assert received_orders[0].payload.side == OrderSide.SELL
    assert received_orders[0].payload.quantity == 100
    _remove_file(snapshot_path)


def _create_market_data_event(price: float) -> BaseEvent:
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


def _create_order_requested_event() -> BaseEvent:
    return EventFactory(source=EventSource.TRADING).create(
        event_type=EventType.ORDER_REQUESTED,
        timestamp=_timestamp(),
        symbol="7203",
        payload=OrderRequestedPayload(
            symbol="7203",
            side=OrderSide.BUY,
            quantity=100,
            order_type="MARKET",
            price=None,
        ),
    )


def _create_position_updated_event() -> BaseEvent:
    return EventFactory(source=EventSource.TRADING).create(
        event_type=EventType.POSITION_UPDATED,
        timestamp=_timestamp(),
        symbol="7203",
        payload=PositionPayload(
            symbol="7203",
            quantity=100,
            avg_price=1000.0,
            realized_pnl=None,
            unrealized_pnl=None,
        ),
    )


def _create_error_event() -> BaseEvent:
    return ErrorOccurred(
        timestamp=_timestamp(),
        source=EventSource.TRADING,
        symbol=None,
        payload=ErrorPayload(
            error_type="TEST",
            message="test error",
        ),
        sequence_no=1,
    )


def _create_signal_event(signal_type: SignalType) -> BaseEvent:
    return EventFactory(source=EventSource.SIGNAL).create(
        event_type=EventType.SIGNAL_DETECTED,
        timestamp=_timestamp(),
        symbol="7203",
        payload=SignalPayload(
            signal_type=signal_type,
            strategy_type=StrategyType.TREND,
        ),
    )


def _timestamp() -> datetime:
    return datetime(2026, 4, 18, tzinfo=timezone.utc)


def _test_file_path(file_name: str) -> Path:
    directory = Path("tests/.tmp_persistence_snapshot")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / file_name


def _remove_file(path: Path) -> None:
    if path.exists():
        path.unlink()

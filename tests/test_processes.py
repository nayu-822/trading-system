from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_source.csv_loader import CsvMarketDataLoader
from domain.enums import EventSource, EventType, OrderSide, SignalType, StrategyType
from domain.events import (
    BaseEvent,
    EventFactory,
    MarketDataPayload,
    OrderRequested,
    SignalDetected,
    SignalPayload,
)
from infrastructure.event_bus import EventBus
from processes.external_data_process import ExternalDataProcess
from processes.signal_process import SignalProcess
from processes.trading_process import TradingProcess

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _create_market_data_event(price: float) -> BaseEvent[Any]:
    timestamp = datetime(2026, 4, 18, tzinfo=timezone.utc)
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


def test_signal_process_publishes_buy_when_price_rises() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(event_bus=event_bus)
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    event_bus.publish(_create_market_data_event(price=100.0))
    event_bus.publish(_create_market_data_event(price=101.0))

    assert len(received_events) == 1
    assert isinstance(received_events[0], SignalDetected)
    assert received_events[0].payload.signal_type == SignalType.BUY
    assert received_events[0].payload.indicators[0].value == 1.0


def test_signal_process_publishes_sell_when_price_falls() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(event_bus=event_bus)
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    event_bus.publish(_create_market_data_event(price=101.0))
    event_bus.publish(_create_market_data_event(price=100.0))

    assert len(received_events) == 1
    assert isinstance(received_events[0], SignalDetected)
    assert received_events[0].payload.signal_type == SignalType.SELL
    assert received_events[0].payload.indicators[0].value == -1.0


def test_trading_process_publishes_order_requested_from_signal() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    timestamp = datetime(2026, 4, 18, tzinfo=timezone.utc)
    signal_event = EventFactory(source=EventSource.SIGNAL).create(
        event_type=EventType.SIGNAL_DETECTED,
        timestamp=timestamp,
        symbol="7203",
        payload=SignalPayload(
            signal_type=SignalType.BUY,
            strategy_type=StrategyType.AUTO,
        ),
    )
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(signal_event)

    assert len(received_events) == 1
    assert isinstance(received_events[0], OrderRequested)
    assert received_events[0].payload.symbol == "7203"
    assert received_events[0].payload.side == OrderSide.BUY
    assert received_events[0].payload.quantity == 100


def test_csv_event_flow_publishes_order_requested_end_to_end() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(event_bus=event_bus)
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    external_data_process = ExternalDataProcess(
        event_bus=event_bus,
        csv_loader=CsvMarketDataLoader(),
    )
    received_orders: list[BaseEvent[Any]] = []

    signal_process.start()
    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_orders.append)
    external_data_process.run_csv(
        csv_path=FIXTURE_DIR / "market_data.csv",
        symbol="7203",
    )

    assert len(received_orders) == 1
    assert isinstance(received_orders[0], OrderRequested)
    assert received_orders[0].payload.side == OrderSide.BUY

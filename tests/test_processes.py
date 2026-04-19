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
from domain.models import SignalStrategyConfig
from infrastructure.event_bus import EventBus
from processes.external_data_process import ExternalDataProcess
from processes.signal_process import SignalProcess, SignalStrategySlot
from processes.trading_process import TradingProcess
from strategy.trend_strategy import TrendStrategy

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _create_market_data_event(price: float, symbol: str = "7203") -> BaseEvent[Any]:
    timestamp = datetime(2026, 4, 18, tzinfo=timezone.utc)
    return EventFactory(source=EventSource.EXTERNAL_DATA).create(
        event_type=EventType.MARKET_DATA_UPDATED,
        timestamp=timestamp,
        symbol=symbol,
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


def test_signal_process_keeps_last_price_by_symbol() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(event_bus=event_bus)
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    event_bus.publish(_create_market_data_event(price=100.0, symbol="7203"))
    event_bus.publish(_create_market_data_event(price=500.0, symbol="6758"))
    event_bus.publish(_create_market_data_event(price=101.0, symbol="7203"))
    event_bus.publish(_create_market_data_event(price=499.0, symbol="6758"))

    assert len(received_events) == 2
    assert received_events[0].symbol == "7203"
    assert received_events[0].payload.signal_type == SignalType.BUY
    assert received_events[0].payload.indicators[0].value == 1.0
    assert received_events[1].symbol == "6758"
    assert received_events[1].payload.signal_type == SignalType.SELL
    assert received_events[1].payload.indicators[0].value == -1.0
    toyota_slot = _find_strategy_slot(signal_process, symbol="7203")
    sony_slot = _find_strategy_slot(signal_process, symbol="6758")

    assert isinstance(toyota_slot.strategy, TrendStrategy)
    assert isinstance(sony_slot.strategy, TrendStrategy)
    assert toyota_slot.strategy.state.last_price == 101.0
    assert sony_slot.strategy.state.last_price == 499.0


def test_signal_process_switches_strategy_by_symbol_config() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        strategy_configs=(
            SignalStrategyConfig(
                symbol="7203",
                strategy_type=StrategyType.TREND,
            ),
            SignalStrategyConfig(
                symbol="6758",
                strategy_type=StrategyType.RANGE,
            ),
        ),
        range_window=2,
    )
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    event_bus.publish(_create_market_data_event(price=100.0, symbol="7203"))
    event_bus.publish(_create_market_data_event(price=101.0, symbol="7203"))
    event_bus.publish(_create_market_data_event(price=100.0, symbol="6758"))
    event_bus.publish(_create_market_data_event(price=102.0, symbol="6758"))
    event_bus.publish(_create_market_data_event(price=99.0, symbol="6758"))

    assert len(received_events) == 2
    assert received_events[0].symbol == "7203"
    assert received_events[0].payload.strategy_type == StrategyType.TREND
    assert received_events[0].payload.signal_type == SignalType.BUY
    assert received_events[1].symbol == "6758"
    assert received_events[1].payload.strategy_type == StrategyType.RANGE
    assert received_events[1].payload.signal_type == SignalType.BUY


def test_signal_process_falls_back_to_trend_when_auto_is_configured(caplog) -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        strategy_configs=(
            SignalStrategyConfig(
                symbol="7203",
                strategy_type=StrategyType.AUTO,
            ),
        ),
    )
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    with caplog.at_level("WARNING"):
        event_bus.publish(_create_market_data_event(price=100.0, symbol="7203"))
        event_bus.publish(_create_market_data_event(price=101.0, symbol="7203"))

    slot = _find_strategy_slot(signal_process, symbol="7203")

    assert "auto strategy is not implemented" in caplog.text
    assert slot.requested_strategy_type == StrategyType.AUTO
    assert slot.active_strategy_type == StrategyType.TREND
    assert isinstance(slot.strategy, TrendStrategy)
    assert len(received_events) == 1
    assert received_events[0].payload.signal_type == SignalType.BUY
    assert received_events[0].payload.strategy_type == StrategyType.TREND
    assert received_events[0].payload.indicators[0].name == "price_delta"
    assert received_events[0].payload.indicators[0].value == 1.0


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


def test_trading_process_ignores_exit_signal() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    timestamp = datetime(2026, 4, 18, tzinfo=timezone.utc)
    signal_event = EventFactory(source=EventSource.SIGNAL).create(
        event_type=EventType.SIGNAL_DETECTED,
        timestamp=timestamp,
        symbol="7203",
        payload=SignalPayload(
            signal_type=SignalType.EXIT,
            strategy_type=StrategyType.AUTO,
        ),
    )
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(signal_event)

    assert received_events == []


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


def _find_strategy_slot(
    signal_process: SignalProcess,
    symbol: str,
) -> SignalStrategySlot:
    for slot in signal_process.slots:
        if slot.symbol == symbol:
            return slot
    raise AssertionError(f"strategy slot was not found: {symbol}")

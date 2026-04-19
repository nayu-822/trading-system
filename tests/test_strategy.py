from datetime import datetime, timezone
from typing import Any

from domain.enums import EventSource, EventType, SignalType, StrategyType
from domain.events import BaseEvent, EventFactory, MarketDataPayload
from strategy.range_strategy import RangeStrategy, RangeStrategyState
from strategy.trend_strategy import TrendStrategy, TrendStrategyState


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


def test_trend_strategy_generates_buy_and_suppresses_same_direction() -> None:
    strategy = TrendStrategy(state=TrendStrategyState(symbol="7203"))

    first_payload = strategy.on_market_data(_create_market_data_event(price=100.0))
    buy_payload = strategy.on_market_data(_create_market_data_event(price=101.0))
    suppressed_payload = strategy.on_market_data(_create_market_data_event(price=102.0))

    assert first_payload is None
    assert buy_payload is not None
    assert buy_payload.signal_type == SignalType.BUY
    assert buy_payload.strategy_type == StrategyType.TREND
    assert buy_payload.indicators[0].value == 1.0
    assert suppressed_payload is None


def test_trend_strategy_generates_sell_when_direction_changes() -> None:
    strategy = TrendStrategy(state=TrendStrategyState(symbol="7203"))

    strategy.on_market_data(_create_market_data_event(price=100.0))
    strategy.on_market_data(_create_market_data_event(price=101.0))
    sell_payload = strategy.on_market_data(_create_market_data_event(price=99.0))

    assert sell_payload is not None
    assert sell_payload.signal_type == SignalType.SELL
    assert sell_payload.strategy_type == StrategyType.TREND
    assert sell_payload.indicators[0].value == -2.0


def test_range_strategy_does_not_signal_until_buffer_is_full() -> None:
    strategy = RangeStrategy(state=RangeStrategyState(symbol="7203"), window=3)

    assert strategy.on_market_data(_create_market_data_event(price=100.0)) is None
    assert strategy.on_market_data(_create_market_data_event(price=102.0)) is None
    assert strategy.on_market_data(_create_market_data_event(price=101.0)) is None


def test_range_strategy_generates_buy_and_sell_from_recent_range() -> None:
    strategy = RangeStrategy(state=RangeStrategyState(symbol="7203"), window=2)

    strategy.on_market_data(_create_market_data_event(price=100.0))
    strategy.on_market_data(_create_market_data_event(price=102.0))
    buy_payload = strategy.on_market_data(_create_market_data_event(price=99.0))
    sell_payload = strategy.on_market_data(_create_market_data_event(price=103.0))

    assert buy_payload is not None
    assert buy_payload.signal_type == SignalType.BUY
    assert buy_payload.strategy_type == StrategyType.RANGE
    assert buy_payload.indicators[0].name == "range_low"
    assert buy_payload.indicators[0].value == 100.0
    assert sell_payload is not None
    assert sell_payload.signal_type == SignalType.SELL
    assert sell_payload.strategy_type == StrategyType.RANGE
    assert sell_payload.indicators[1].name == "range_high"
    assert sell_payload.indicators[1].value == 102.0

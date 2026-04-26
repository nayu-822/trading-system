from datetime import datetime, timezone
from typing import Any

import pytest

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


def test_trend_strategy_does_not_signal_until_long_window_is_full() -> None:
    strategy = TrendStrategy(
        state=TrendStrategyState(symbol="7203"),
        short_window=2,
        long_window=3,
    )

    assert strategy.on_market_data(_create_market_data_event(price=100.0)) is None
    assert strategy.on_market_data(_create_market_data_event(price=101.0)) is None


def test_trend_strategy_generates_buy_when_short_ma_exceeds_long_ma() -> None:
    strategy = TrendStrategy(
        state=TrendStrategyState(symbol="7203"),
        short_window=2,
        long_window=3,
    )

    strategy.on_market_data(_create_market_data_event(price=100.0))
    strategy.on_market_data(_create_market_data_event(price=101.0))
    buy_payload = strategy.on_market_data(_create_market_data_event(price=102.0))

    assert buy_payload is not None
    assert buy_payload.signal_type == SignalType.BUY
    assert buy_payload.strategy_type == StrategyType.TREND
    assert buy_payload.indicators[0].name == "short_ma"
    assert buy_payload.indicators[0].value == 101.5
    assert buy_payload.indicators[1].name == "long_ma"
    assert buy_payload.indicators[1].value == 101.0
    assert buy_payload.indicators[2].name == "ma_diff"
    assert buy_payload.indicators[2].value == 0.5
    assert buy_payload.indicators[3].name == "current_price"
    assert buy_payload.indicators[3].value == 102.0


def test_trend_strategy_generates_sell_when_short_ma_is_below_long_ma() -> None:
    strategy = TrendStrategy(
        state=TrendStrategyState(symbol="7203"),
        short_window=2,
        long_window=3,
    )

    strategy.on_market_data(_create_market_data_event(price=102.0))
    strategy.on_market_data(_create_market_data_event(price=101.0))
    sell_payload = strategy.on_market_data(_create_market_data_event(price=100.0))

    assert sell_payload is not None
    assert sell_payload.signal_type == SignalType.SELL
    assert sell_payload.strategy_type == StrategyType.TREND
    assert sell_payload.indicators[0].value == 100.5
    assert sell_payload.indicators[1].value == 101.0
    assert sell_payload.indicators[2].value == -0.5


def test_trend_strategy_changes_signal_when_windows_change() -> None:
    default_strategy = TrendStrategy(
        state=TrendStrategyState(symbol="7203"),
        short_window=2,
        long_window=4,
    )
    override_strategy = TrendStrategy(
        state=TrendStrategyState(symbol="7203"),
        short_window=3,
        long_window=4,
    )

    prices = (120.0, 60.0, 80.0, 110.0)
    default_payload = None
    override_payload = None
    for price in prices:
        default_payload = default_strategy.on_market_data(_create_market_data_event(price))
        override_payload = override_strategy.on_market_data(
            _create_market_data_event(price)
        )

    assert default_payload is not None
    assert override_payload is not None
    assert default_payload.signal_type == SignalType.BUY
    assert override_payload.signal_type == SignalType.SELL


def test_trend_strategy_suppresses_same_signal_consecutively() -> None:
    strategy = TrendStrategy(
        state=TrendStrategyState(symbol="7203"),
        short_window=2,
        long_window=3,
    )

    strategy.on_market_data(_create_market_data_event(price=100.0))
    strategy.on_market_data(_create_market_data_event(price=101.0))
    buy_payload = strategy.on_market_data(_create_market_data_event(price=102.0))
    suppressed_payload = strategy.on_market_data(_create_market_data_event(price=103.0))

    assert buy_payload is not None
    assert buy_payload.signal_type == SignalType.BUY
    assert suppressed_payload is None


def test_trend_strategy_reset_clears_price_history() -> None:
    strategy = TrendStrategy(
        state=TrendStrategyState(symbol="7203"),
        short_window=2,
        long_window=3,
    )

    strategy.on_market_data(_create_market_data_event(price=100.0))
    strategy.on_market_data(_create_market_data_event(price=101.0))

    strategy.reset("7203")

    assert strategy.state.prices == []
    assert strategy.state.last_price is None
    assert strategy.state.last_signal_type is None


@pytest.mark.parametrize(
    ("short_window", "long_window"),
    [
        (0, 3),
        (2, 0),
        (3, 3),
        (4, 3),
    ],
)
def test_trend_strategy_rejects_invalid_windows(
    short_window: int,
    long_window: int,
) -> None:
    with pytest.raises(ValueError):
        TrendStrategy(
            state=TrendStrategyState(symbol="7203"),
            short_window=short_window,
            long_window=long_window,
        )


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

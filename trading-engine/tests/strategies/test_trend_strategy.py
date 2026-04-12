from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.strategies.signal import SignalType
from app.strategies.trend.trend_strategy import TrendStrategy


def test_generate_signal_returns_buy_on_golden_cross() -> None:
    market_data = pd.DataFrame(
        {"close": [10, 9, 8, 7, 6, 12]}
    )
    strategy = TrendStrategy(short_window=2, long_window=5)

    signal = strategy.generate_signal(market_data=market_data)

    assert signal.signal == SignalType.BUY


def test_generate_signal_returns_sell_on_dead_cross() -> None:
    market_data = pd.DataFrame(
        {"close": [6, 7, 8, 9, 10, 4]}
    )
    strategy = TrendStrategy(short_window=2, long_window=5)

    signal = strategy.generate_signal(market_data=market_data)

    assert signal.signal == SignalType.SELL


def test_generate_signal_returns_hold_when_cross_does_not_happen() -> None:
    market_data = pd.DataFrame(
        {"close": [1, 2, 3, 4, 5, 6]}
    )
    strategy = TrendStrategy(short_window=2, long_window=5)

    signal = strategy.generate_signal(market_data=market_data)

    assert signal.signal == SignalType.HOLD


def test_generate_signal_raises_when_market_data_is_too_short() -> None:
    market_data = pd.DataFrame({"close": [1, 2, 3, 4, 5]})
    strategy = TrendStrategy(short_window=2, long_window=5)

    with pytest.raises(ValueError):
        strategy.generate_signal(market_data=market_data)

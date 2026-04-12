from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.strategies.range.range_strategy import RangeStrategy, SignalType


def test_generate_signal_returns_buy_when_rsi_is_oversold() -> None:
    market_data = pd.DataFrame(
        {"close": [10, 9, 8, 7, 6, 5]}
    )
    strategy = RangeStrategy(rsi_period=5, oversold_threshold=30.0, overbought_threshold=70.0)

    signal = strategy.generate_signal(market_data=market_data)

    assert signal.signal == SignalType.BUY


def test_generate_signal_returns_sell_when_rsi_is_overbought() -> None:
    market_data = pd.DataFrame(
        {"close": [5, 6, 7, 8, 9, 10]}
    )
    strategy = RangeStrategy(rsi_period=5, oversold_threshold=30.0, overbought_threshold=70.0)

    signal = strategy.generate_signal(market_data=market_data)

    assert signal.signal == SignalType.SELL


def test_generate_signal_returns_hold_when_rsi_is_between_thresholds() -> None:
    market_data = pd.DataFrame(
        {"close": [10, 9, 10, 9, 10, 9]}
    )
    strategy = RangeStrategy(rsi_period=5, oversold_threshold=30.0, overbought_threshold=70.0)

    signal = strategy.generate_signal(market_data=market_data)

    assert signal.signal == SignalType.HOLD


def test_generate_signal_raises_when_market_data_is_too_short() -> None:
    market_data = pd.DataFrame({"close": [1, 2, 3, 4, 5]})
    strategy = RangeStrategy(rsi_period=5)

    with pytest.raises(ValueError):
        strategy.generate_signal(market_data=market_data)

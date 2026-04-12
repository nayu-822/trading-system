from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sys
from pathlib import Path

import pandas as pd


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.engine import Engine, EngineConfig
from app.execution.paper_executor import PaperExecutionResult, PaperOrder
from app.strategies.signal import BaseSignal, SignalType


@dataclass(frozen=True)
class DummySignal(BaseSignal):
    pass


class DummyStrategy:
    def __init__(self, signal_type: SignalType) -> None:
        self.signal_type = signal_type

    def generate_signal(self, market_data: pd.DataFrame) -> DummySignal:
        return DummySignal(signal=self.signal_type)


class DummyExecutor:
    def __init__(self) -> None:
        self.called = False
        self.received_order: PaperOrder | None = None

    def execute(self, order: PaperOrder) -> PaperExecutionResult:
        self.called = True
        self.received_order = order
        return PaperExecutionResult(
            order_id="paper-test-order",
            symbol=order.symbol,
            strategy=order.strategy,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            status="filled",
            executed_at="2026-04-12T09:00:00+00:00",
        )


class DummyDataProvider:
    def fetch(self, symbol: str) -> pd.DataFrame:
        return pd.DataFrame({"symbol": [symbol] * 3, "close": [100.0, 101.0, 102.0]})


class ClosedMarketTimeChecker:
    def is_open(self, current_datetime: datetime | None = None) -> bool:
        return False


class OpenMarketTimeChecker:
    def is_open(self, current_datetime: datetime | None = None) -> bool:
        return True


def test_run_once_does_not_process_when_market_is_closed() -> None:
    executor = DummyExecutor()
    engine = Engine(
        config=EngineConfig(symbol="7203", strategy_name="trend", quantity=100),
        strategy=DummyStrategy(signal_type=SignalType.BUY),
        executor=executor,
        data_provider=DummyDataProvider(),
        market_time_checker=ClosedMarketTimeChecker(),
    )

    result = engine.run_once()

    assert result is None
    assert executor.called is False


def test_run_once_does_not_call_executor_when_signal_is_hold() -> None:
    executor = DummyExecutor()
    engine = Engine(
        config=EngineConfig(symbol="7203", strategy_name="trend", quantity=100),
        strategy=DummyStrategy(signal_type=SignalType.HOLD),
        executor=executor,
        data_provider=DummyDataProvider(),
        market_time_checker=OpenMarketTimeChecker(),
    )

    result = engine.run_once()

    assert result is None
    assert executor.called is False


def test_run_once_calls_executor_when_signal_is_buy() -> None:
    executor = DummyExecutor()
    engine = Engine(
        config=EngineConfig(symbol="7203", strategy_name="trend", quantity=100),
        strategy=DummyStrategy(signal_type=SignalType.BUY),
        executor=executor,
        data_provider=DummyDataProvider(),
        market_time_checker=OpenMarketTimeChecker(),
    )

    result = engine.run_once()

    assert result is not None
    assert executor.called is True
    assert executor.received_order is not None
    assert executor.received_order.side == "buy"


def test_run_once_calls_executor_when_signal_is_sell() -> None:
    executor = DummyExecutor()
    engine = Engine(
        config=EngineConfig(symbol="7203", strategy_name="trend", quantity=100),
        strategy=DummyStrategy(signal_type=SignalType.SELL),
        executor=executor,
        data_provider=DummyDataProvider(),
        market_time_checker=OpenMarketTimeChecker(),
    )

    result = engine.run_once()

    assert result is not None
    assert executor.called is True
    assert executor.received_order is not None
    assert executor.received_order.side == "sell"

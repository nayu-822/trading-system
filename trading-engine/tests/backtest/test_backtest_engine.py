from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import pandas as pd
import pytest


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.backtest.backtest_engine import BacktestConfig, BacktestEngine
from app.domain.models import ExecutionResult, Order
from app.logging.trade_logger import TradeLog
from app.strategies.signal import BaseSignal, SignalType


@dataclass(frozen=True)
class DummySignal(BaseSignal):
    pass


class SequenceStrategy:
    def __init__(self, signals: list[SignalType]) -> None:
        self.signals = signals
        self.calls: list[int] = []

    def generate_signal(self, market_data: pd.DataFrame) -> DummySignal:
        """
        呼び出し順に応じて固定シグナルを返す。
        引数:
            market_data: その時点までの市場データ

        戻り値:
            DummySignal: 固定シグナル
        """
        self.calls.append(len(market_data))
        signal_index = len(self.calls) - 1
        return DummySignal(signal=self.signals[signal_index])


class DummyExecutor:
    def __init__(self) -> None:
        self.called_orders: list[Order] = []

    def execute(self, order: Order) -> ExecutionResult:
        """
        注文情報を記録し、固定の実行結果を返す。
        引数:
            order: 実行対象の注文情報

        戻り値:
            ExecutionResult: 固定の実行結果
        """
        self.called_orders.append(order)
        return ExecutionResult(
            success=True,
            executed_price=float(order.price or 0.0),
            quantity=order.quantity,
            message="backtest execution simulated",
        )


class DummyTradeLogger:
    def __init__(self) -> None:
        self.logs: list[TradeLog] = []

    def log(self, log: TradeLog) -> None:
        """
        売買ログを記録する。
        引数:
            log: 保存対象の売買ログ

        戻り値:
            なし
        """
        self.logs.append(log)


def test_backtest_engine_processes_market_data_sequentially() -> None:
    strategy = SequenceStrategy(
        signals=[
            SignalType.HOLD,
            SignalType.HOLD,
            SignalType.BUY,
            SignalType.SELL,
        ]
    )
    executor = DummyExecutor()
    trade_logger = DummyTradeLogger()
    engine = BacktestEngine(
        config=BacktestConfig(symbol="7203.T", strategy_name="trend", quantity=100),
        strategy=strategy,
        executor=executor,
        trade_logger=trade_logger,
    )
    market_data = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-01-05 09:00:00",
                    "2026-01-06 09:00:00",
                    "2026-01-07 09:00:00",
                    "2026-01-08 09:00:00",
                ]
            ),
            "close": [100.0, 101.0, 102.0, 103.0],
        }
    )

    result = engine.run(market_data=market_data)

    assert strategy.calls == [1, 2, 3, 4]
    assert len(executor.called_orders) == 2
    assert executor.called_orders[0].side == "buy"
    assert executor.called_orders[1].side == "sell"
    assert len(trade_logger.logs) == 2
    assert result.total_trades == 2
    assert result.buy_count == 1
    assert result.sell_count == 1
    assert result.total_pnl == 1.0
    assert result.average_pnl == 1.0
    assert result.win_rate == 1.0
    assert result.max_win_streak == 1
    assert result.max_loss_streak == 0


def test_backtest_engine_does_not_call_executor_when_signal_is_hold() -> None:
    strategy = SequenceStrategy(
        signals=[
            SignalType.HOLD,
            SignalType.HOLD,
        ]
    )
    executor = DummyExecutor()
    trade_logger = DummyTradeLogger()
    engine = BacktestEngine(
        config=BacktestConfig(symbol="6758.T", strategy_name="trend", quantity=100),
        strategy=strategy,
        executor=executor,
        trade_logger=trade_logger,
    )
    market_data = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-05 09:00:00", "2026-01-06 09:00:00"]),
            "close": [100.0, 101.0],
        }
    )

    result = engine.run(market_data=market_data)

    assert result.total_trades == 0
    assert executor.called_orders == []
    assert trade_logger.logs == []
    assert result.total_pnl == 0.0
    assert result.average_pnl == 0.0
    assert result.win_rate == 0.0
    assert result.max_win_streak == 0
    assert result.max_loss_streak == 0


def test_backtest_engine_trade_logger_receives_expected_trade_log() -> None:
    strategy = SequenceStrategy(signals=[SignalType.BUY])
    executor = DummyExecutor()
    trade_logger = DummyTradeLogger()
    engine = BacktestEngine(
        config=BacktestConfig(symbol="8306.T", strategy_name="trend", quantity=50),
        strategy=strategy,
        executor=executor,
        trade_logger=trade_logger,
    )
    market_data = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-05 09:00:00"]),
            "close": [1800.5],
        }
    )

    result = engine.run(market_data=market_data)

    assert result.total_trades == 1
    assert trade_logger.logs[0].symbol == "8306.T"
    assert trade_logger.logs[0].side == "buy"
    assert trade_logger.logs[0].price == 1800.5
    assert trade_logger.logs[0].quantity == 50.0
    assert trade_logger.logs[0].strategy == "trend"


def test_backtest_engine_skips_strategy_warmup_errors() -> None:
    class WarmupStrategy:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def generate_signal(self, market_data: pd.DataFrame) -> DummySignal:
            self.calls.append(len(market_data))
            if len(market_data) < 3:
                raise ValueError("market_data must contain at least 3 rows to evaluate a moving average crossover")
            if len(market_data) == 3:
                return DummySignal(signal=SignalType.BUY)
            return DummySignal(signal=SignalType.SELL)

    strategy = WarmupStrategy()
    executor = DummyExecutor()
    engine = BacktestEngine(
        config=BacktestConfig(symbol="7203.T", strategy_name="trend", quantity=100),
        strategy=strategy,
        executor=executor,
    )
    market_data = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-01-05 09:00:00",
                    "2026-01-06 09:00:00",
                    "2026-01-07 09:00:00",
                    "2026-01-08 09:00:00",
                ]
            ),
            "close": [100.0, 101.0, 102.0, 103.0],
        }
    )

    result = engine.run(market_data=market_data)

    assert strategy.calls == [1, 2, 3, 4]
    assert len(executor.called_orders) == 2
    assert result.total_trades == 2


def test_backtest_engine_calculates_loss_and_loss_streaks() -> None:
    strategy = SequenceStrategy(
        signals=[
            SignalType.BUY,
            SignalType.SELL,
            SignalType.BUY,
            SignalType.SELL,
        ]
    )
    executor = DummyExecutor()
    engine = BacktestEngine(
        config=BacktestConfig(symbol="7203.T", strategy_name="trend", quantity=100),
        strategy=strategy,
        executor=executor,
    )
    market_data = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-01-05 09:00:00",
                    "2026-01-06 09:00:00",
                    "2026-01-07 09:00:00",
                    "2026-01-08 09:00:00",
                ]
            ),
            "close": [100.0, 99.0, 98.0, 97.0],
        }
    )

    result = engine.run(market_data=market_data)

    assert result.total_trades == 4
    assert result.total_pnl == -2.0
    assert result.average_pnl == -1.0
    assert result.win_rate == 0.0
    assert result.max_win_streak == 0
    assert result.max_loss_streak == 2


def test_backtest_engine_ignores_open_position_at_the_end() -> None:
    strategy = SequenceStrategy(
        signals=[
            SignalType.BUY,
            SignalType.HOLD,
            SignalType.HOLD,
        ]
    )
    executor = DummyExecutor()
    engine = BacktestEngine(
        config=BacktestConfig(symbol="9984.T", strategy_name="trend", quantity=100),
        strategy=strategy,
        executor=executor,
    )
    market_data = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2026-01-05 09:00:00",
                    "2026-01-06 09:00:00",
                    "2026-01-07 09:00:00",
                ]
            ),
            "close": [100.0, 101.0, 102.0],
        }
    )

    result = engine.run(market_data=market_data)

    assert result.total_trades == 1
    assert result.buy_count == 1
    assert result.sell_count == 0
    assert result.total_pnl == 0.0
    assert result.average_pnl == 0.0
    assert result.win_rate == 0.0
    assert result.max_win_streak == 0
    assert result.max_loss_streak == 0


def test_backtest_engine_raises_when_market_data_is_empty() -> None:
    engine = BacktestEngine(
        config=BacktestConfig(symbol="1306.T", strategy_name="trend", quantity=100),
        strategy=SequenceStrategy(signals=[]),
        executor=DummyExecutor(),
    )

    with pytest.raises(ValueError, match="market_data must not be empty"):
        engine.run(market_data=pd.DataFrame(columns=["timestamp", "close"]))

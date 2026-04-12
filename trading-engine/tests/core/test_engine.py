from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import sys

import pandas as pd
import pytest


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.core.engine import Engine, EngineConfig
from app.domain.models import ExecutionResult, Order
from app.logging.trade_logger import TradeLog
from app.execution.paper_executor import PaperExecutor
from app.strategies.signal import BaseSignal, SignalType


@dataclass(frozen=True)
class DummySignal(BaseSignal):
    pass


class DummyStrategy:
    def __init__(self, signal_type: SignalType) -> None:
        self.signal_type = signal_type

    def generate_signal(self, market_data: pd.DataFrame) -> DummySignal:
        """
        テスト用に固定のシグナルを返す。
        引数:
            market_data: Engine から渡される市場データ

        戻り値:
            DummySignal: 固定のシグナル
        """
        return DummySignal(signal=self.signal_type)


class DummyExecutor:
    def __init__(self) -> None:
        self.called = False
        self.received_order: Order | None = None

    def execute(self, order: Order) -> ExecutionResult:
        """
        注文呼び出しの有無を確認するためのダミー executor。
        引数:
            order: Engine が生成した注文情報

        戻り値:
            ExecutionResult: 固定の実行結果
        """
        self.called = True
        self.received_order = order
        return ExecutionResult(
            success=True,
            executed_price=float(order.price or 0.0),
            quantity=order.quantity,
            message="dummy execution",
        )


class DummyDataProvider:
    def fetch(self, symbol: str) -> pd.DataFrame:
        """
        テスト用の市場データを返す。
        引数:
            symbol: 取得対象の銘柄コード

        戻り値:
            pd.DataFrame: close 列を含む市場データ
        """
        return pd.DataFrame({"symbol": [symbol] * 3, "close": [100.0, 101.0, 102.0]})


class ClosedMarketTimeChecker:
    def is_open(self, current_datetime: datetime | None = None) -> bool:
        """
        常に市場時間外として判定する。
        引数:
            current_datetime: 判定対象の日時

        戻り値:
            bool: 常に False
        """
        return False


class OpenMarketTimeChecker:
    def is_open(self, current_datetime: datetime | None = None) -> bool:
        """
        常に市場時間内として判定する。
        引数:
            current_datetime: 判定対象の日時

        戻り値:
            bool: 常に True
        """
        return True


class FailingDataProvider:
    def fetch(self, symbol: str) -> pd.DataFrame:
        """
        例外ログ確認用に常に失敗するデータ取得処理。
        引数:
            symbol: 取得対象の銘柄コード

        戻り値:
            なし
        """
        raise ValueError("symbol not found: 7203")


class DummyTradeLogger:
    def __init__(self) -> None:
        self.called = False
        self.received_log: TradeLog | None = None

    def log(self, log: TradeLog) -> None:
        """
        Engine からの売買ログ連携を確認するためのダミー logger。
        引数:
            log: Engine が生成した売買ログ

        戻り値:
            なし
        """
        self.called = True
        self.received_log = log


def test_run_once_does_not_process_when_market_is_closed() -> None:
    executor = DummyExecutor()
    engine = Engine(
        config=EngineConfig(symbol="7203", strategy_name="trend", quantity=100),
        executor=executor,
        strategy=DummyStrategy(signal_type=SignalType.BUY),
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
        executor=executor,
        strategy=DummyStrategy(signal_type=SignalType.HOLD),
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
        executor=executor,
        strategy=DummyStrategy(signal_type=SignalType.BUY),
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
        executor=executor,
        strategy=DummyStrategy(signal_type=SignalType.SELL),
        data_provider=DummyDataProvider(),
        market_time_checker=OpenMarketTimeChecker(),
    )

    result = engine.run_once()

    assert result is not None
    assert executor.called is True
    assert executor.received_order is not None
    assert executor.received_order.side == "sell"


def test_run_once_uses_domain_order_with_paper_executor() -> None:
    engine = Engine(
        config=EngineConfig(symbol="7203", strategy_name="trend", quantity=100),
        executor=PaperExecutor(),
        strategy=DummyStrategy(signal_type=SignalType.BUY),
        data_provider=DummyDataProvider(),
        market_time_checker=OpenMarketTimeChecker(),
    )

    result = engine.run_once()

    assert result is not None
    assert result.success is True
    assert result.executed_price == 102.0


def test_run_once_calls_trade_logger_after_execution() -> None:
    trade_logger = DummyTradeLogger()
    engine = Engine(
        config=EngineConfig(symbol="7203", strategy_name="trend", quantity=100),
        executor=PaperExecutor(),
        strategy=DummyStrategy(signal_type=SignalType.BUY),
        data_provider=DummyDataProvider(),
        market_time_checker=OpenMarketTimeChecker(),
        trade_logger=trade_logger,
    )

    result = engine.run_once(current_datetime=datetime(2026, 4, 12, 9, 0, 0))

    assert result is not None
    assert trade_logger.called is True
    assert trade_logger.received_log is not None
    assert trade_logger.received_log.symbol == "7203"
    assert trade_logger.received_log.side == "buy"
    assert trade_logger.received_log.price == 102.0
    assert trade_logger.received_log.quantity == 100.0
    assert trade_logger.received_log.strategy == "trend"


def test_run_once_does_not_call_trade_logger_when_signal_is_hold() -> None:
    trade_logger = DummyTradeLogger()
    engine = Engine(
        config=EngineConfig(symbol="7203", strategy_name="trend", quantity=100),
        executor=DummyExecutor(),
        strategy=DummyStrategy(signal_type=SignalType.HOLD),
        data_provider=DummyDataProvider(),
        market_time_checker=OpenMarketTimeChecker(),
        trade_logger=trade_logger,
    )

    result = engine.run_once()

    assert result is None
    assert trade_logger.called is False


def test_run_once_logs_exception_when_processing_fails(caplog: pytest.LogCaptureFixture) -> None:
    engine = Engine(
        config=EngineConfig(symbol="7203", strategy_name="trend", quantity=100),
        executor=DummyExecutor(),
        strategy=DummyStrategy(signal_type=SignalType.BUY),
        data_provider=FailingDataProvider(),
        market_time_checker=OpenMarketTimeChecker(),
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(ValueError, match="symbol not found: 7203"):
            engine.run_once()

    assert "engine failed: symbol=7203 action=fetch_market_data reason=symbol not found: 7203" in caplog.text

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import logging
from typing import Protocol

import pandas as pd

from app.data.market_data import DummyMarketDataProvider
from app.domain.models import ExecutionResult, Order
from app.execution.executor import Executor
from app.logging.trade_logger import TradeLog
from app.strategies.signal import BaseSignal, SignalType
from app.strategies.trend.trend_strategy import TrendStrategy


logger = logging.getLogger(__name__)


class MarketDataProvider(Protocol):
    """
    市場データ取得処理の共通インターフェース。
    """

    def fetch(self, symbol: str) -> pd.DataFrame:
        """
        指定した銘柄の市場データを取得する。
        引数:
            symbol: 取得対象の銘柄コード

        戻り値:
            pd.DataFrame: 戦略へ渡す市場データ
        """
        ...


class Strategy(Protocol):
    """
    戦略実行処理の共通インターフェース。
    """

    def generate_signal(self, market_data: pd.DataFrame) -> BaseSignal:
        """
        市場データから売買シグナルを生成する。
        引数:
            market_data: 戦略判定に使用する市場データ

        戻り値:
            BaseSignal: 売買シグナル
        """
        ...


class MarketTimeChecker(Protocol):
    """
    市場時間判定処理の共通インターフェース。
    """

    def is_open(self, current_datetime: datetime | None = None) -> bool:
        """
        指定時刻が市場時間内かどうかを判定する。
        引数:
            current_datetime: 判定対象の日時。未指定時は現在日時を使用する

        戻り値:
            bool: 市場時間内の場合は True
        """
        ...


class TradeLogWriter(Protocol):
    """
    売買ログ永続化処理の共通インターフェース。
    """

    def log(self, log: TradeLog) -> None:
        """
        売買ログを永続化する。
        引数:
            log: 永続化対象の売買ログ

        戻り値:
            なし
        """
        ...


@dataclass(frozen=True)
class EngineConfig:
    symbol: str
    strategy_name: str
    quantity: int
    price_column: str = "close"


@dataclass(frozen=True)
class DefaultMarketTimeChecker:
    market_open_time: time = time(hour=9, minute=0)
    market_close_time: time = time(hour=15, minute=30)

    def is_open(self, current_datetime: datetime | None = None) -> bool:
        """
        指定日時が平日の市場時間内かどうかを判定する。
        引数:
            current_datetime: 判定対象の日時。未指定時は現在日時を使用する

        戻り値:
            bool: 市場時間内の場合は True
        """
        target_datetime = current_datetime or datetime.now()
        if target_datetime.weekday() >= 5:
            return False

        current_time = target_datetime.time()
        return self.market_open_time <= current_time <= self.market_close_time


class Engine:
    """
    市場データ取得から戦略判定、注文実行までの最小売買フローを管理する。
    """

    def __init__(
        self,
        config: EngineConfig,
        executor: Executor,
        strategy: Strategy | None = None,
        data_provider: MarketDataProvider | None = None,
        market_time_checker: MarketTimeChecker | None = None,
        trade_logger: TradeLogWriter | None = None,
        system_logger: logging.Logger | None = None,
    ) -> None:
        """
        売買フローに必要な依存関係を受け取って初期化する。
        引数:
            config: 売買フロー全体で使用する設定
            executor: 注文実行を担当する executor
            strategy: シグナル生成を担当する戦略
            data_provider: 市場データ取得処理
            market_time_checker: 市場時間の判定処理
            trade_logger: 売買ログ永続化処理
            system_logger: システムログ出力に使用する logger

        戻り値:
            なし
        """
        self.config = config
        self.executor = executor
        self.strategy = strategy or TrendStrategy()
        self.data_provider = data_provider or DummyMarketDataProvider()
        self.market_time_checker = market_time_checker or DefaultMarketTimeChecker()
        self.trade_logger = trade_logger
        self.system_logger = system_logger or logger

    def run_once(self, current_datetime: datetime | None = None) -> ExecutionResult | None:
        """
        市場時間判定から注文実行までのフローを 1 回実行する。
        引数:
            current_datetime: 市場時間判定に使用する日時

        戻り値:
            ExecutionResult | None: 注文実行時は実行結果、スキップ時は None
        """
        action = "initialize"

        try:
            if not self.market_time_checker.is_open(current_datetime=current_datetime):
                self.system_logger.info(
                    "engine skipped: symbol=%s action=skip reason=market_closed",
                    self.config.symbol,
                )
                return None

            action = "fetch_market_data"
            market_data = self.data_provider.fetch(symbol=self.config.symbol)

            action = "generate_signal"
            signal = self.strategy.generate_signal(market_data=market_data)
            self.system_logger.info(
                "engine signal: symbol=%s action=%s signal=%s",
                self.config.symbol,
                "signal_generated",
                signal.signal.value,
            )

            if signal.signal == SignalType.HOLD:
                self.system_logger.info(
                    "engine skipped: symbol=%s action=skip reason=hold",
                    self.config.symbol,
                )
                return None

            action = signal.signal.value
            order = self._build_order(market_data=market_data, signal=signal)
            execution_result = self.executor.execute(order=order)
            if self.trade_logger is not None:
                self.trade_logger.log(
                    log=self._build_trade_log(
                        order=order,
                        execution_result=execution_result,
                        current_datetime=current_datetime,
                    )
                )
            self.system_logger.info(
                "engine executed: symbol=%s action=%s quantity=%s executed_price=%s success=%s reason=%s",
                order.symbol,
                order.side,
                execution_result.quantity,
                execution_result.executed_price,
                execution_result.success,
                execution_result.message,
            )
            return execution_result
        except Exception as exc:
            self.system_logger.exception(
                "engine failed: symbol=%s action=%s reason=%s",
                self.config.symbol,
                action,
                str(exc),
            )
            raise

    def _build_order(self, market_data: pd.DataFrame, signal: BaseSignal) -> Order:
        """
        市場データとシグナルから executor 用の注文情報を組み立てる。
        引数:
            market_data: 最新価格を含む市場データ
            signal: 戦略が返した売買シグナル

        戻り値:
            Order: executor へ渡す注文情報
        """
        latest_price = float(market_data[self.config.price_column].astype(float).iloc[-1])

        if signal.signal == SignalType.BUY:
            side = "buy"
        elif signal.signal == SignalType.SELL:
            side = "sell"
        else:
            raise ValueError("hold signal cannot be converted to order")

        return Order(
            symbol=self.config.symbol,
            side=side,
            quantity=float(self.config.quantity),
            price=latest_price,
        )

    def _build_trade_log(
        self,
        order: Order,
        execution_result: ExecutionResult,
        current_datetime: datetime | None,
    ) -> TradeLog:
        """
        注文情報と実行結果から永続化用の売買ログを組み立てる。
        引数:
            order: 実行した注文情報
            execution_result: executor が返した実行結果
            current_datetime: 実行時刻として使用する日時

        戻り値:
            TradeLog: 永続化用の売買ログ
        """
        return TradeLog(
            timestamp=current_datetime or datetime.now(),
            symbol=order.symbol,
            side=order.side,
            price=execution_result.executed_price,
            quantity=execution_result.quantity,
            success=execution_result.success,
            message=execution_result.message,
            strategy=self.config.strategy_name,
        )

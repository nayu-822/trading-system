from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import pandas as pd

from app.domain.models import ExecutionResult, Order
from app.execution.executor import Executor
from app.logging.trade_logger import TradeLog
from app.strategies.signal import BaseSignal, SignalType


class Strategy(Protocol):
    """
    バックテストで利用する戦略の共通インターフェース。
    """

    def generate_signal(self, market_data: pd.DataFrame) -> BaseSignal:
        """
        市場データから売買シグナルを生成する。
        引数:
            market_data: その時点までの市場データ

        戻り値:
            BaseSignal: 売買シグナル
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
class BacktestConfig:
    symbol: str
    strategy_name: str
    quantity: float
    price_column: str = "close"


@dataclass(frozen=True)
class BacktestResult:
    trades: list[TradeLog]
    total_trades: int
    buy_count: int
    sell_count: int


@dataclass
class BacktestEngine:
    """
    過去データを時系列に沿って逐次処理する簡易バックテストエンジン。
    """

    config: BacktestConfig
    strategy: Strategy
    executor: Executor
    trade_logger: TradeLogWriter | None = None

    def run(self, market_data: pd.DataFrame) -> BacktestResult:
        """
        過去データを 1 行ずつ処理してバックテストを実行する。
        引数:
            market_data: 時系列順に処理する市場データ

        戻り値:
            BacktestResult: 売買件数を集計したバックテスト結果
        """
        self._validate_market_data(market_data=market_data)

        sorted_market_data = market_data.sort_values("timestamp").reset_index(drop=True)
        trades: list[TradeLog] = []
        buy_count = 0
        sell_count = 0

        for current_index in range(1, len(sorted_market_data) + 1):
            current_market_data = sorted_market_data.iloc[:current_index].copy()
            signal = self.strategy.generate_signal(market_data=current_market_data)

            if signal.signal == SignalType.HOLD:
                continue

            order = self._build_order(
                market_data=current_market_data,
                signal=signal,
            )
            execution_result = self.executor.execute(order=order)
            trade_log = self._build_trade_log(
                market_data=current_market_data,
                order=order,
                execution_result=execution_result,
            )
            trades.append(trade_log)

            if self.trade_logger is not None:
                self.trade_logger.log(log=trade_log)

            if order.side == "buy":
                buy_count += 1
            else:
                sell_count += 1

        return BacktestResult(
            trades=trades,
            total_trades=len(trades),
            buy_count=buy_count,
            sell_count=sell_count,
        )

    def _validate_market_data(self, market_data: pd.DataFrame) -> None:
        """
        バックテストに必要なカラムがそろっているかを検証する。
        引数:
            market_data: 検証対象の市場データ

        戻り値:
            なし
        """
        required_columns = {"timestamp", self.config.price_column}
        missing_columns = [column for column in required_columns if column not in market_data.columns]
        if missing_columns:
            raise ValueError(f"required columns are missing: {', '.join(sorted(missing_columns))}")

    def _build_order(self, market_data: pd.DataFrame, signal: BaseSignal) -> Order:
        """
        現在時点の市場データとシグナルから注文情報を組み立てる。
        引数:
            market_data: 現在時点までの市場データ
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
        market_data: pd.DataFrame,
        order: Order,
        execution_result: ExecutionResult,
    ) -> TradeLog:
        """
        実行結果を永続化用の売買ログへ変換する。
        引数:
            market_data: 現在時点までの市場データ
            order: 実行した注文情報
            execution_result: executor の実行結果

        戻り値:
            TradeLog: 売買ログ
        """
        latest_timestamp = market_data["timestamp"].iloc[-1]
        if isinstance(latest_timestamp, pd.Timestamp):
            timestamp = latest_timestamp.to_pydatetime()
        elif isinstance(latest_timestamp, datetime):
            timestamp = latest_timestamp
        else:
            timestamp = pd.to_datetime(latest_timestamp).to_pydatetime()

        return TradeLog(
            timestamp=timestamp,
            symbol=order.symbol,
            side=order.side,
            price=execution_result.executed_price,
            quantity=execution_result.quantity,
            success=execution_result.success,
            message=execution_result.message,
            strategy=self.config.strategy_name,
        )

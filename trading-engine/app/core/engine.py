from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import logging
from typing import Protocol

import pandas as pd

from app.execution.paper_executor import PaperExecutionResult, PaperExecutor, PaperOrder
from app.strategies.signal import BaseSignal, SignalType
from app.strategies.trend.trend_strategy import TrendStrategy


logger = logging.getLogger(__name__)


class MarketDataProvider(Protocol):
    """
    市場データ取得処理の共通インターフェースを表す。
    """

    def fetch(self, symbol: str) -> pd.DataFrame:
        """
        指定銘柄の市場データを取得する。

        引数:
            symbol: データ取得対象の銘柄コード

        戻り値:
            pd.DataFrame: 戦略へ渡す市場データ
        """
        ...


class Strategy(Protocol):
    """
    戦略実行処理の共通インターフェースを表す。
    """

    def generate_signal(self, market_data: pd.DataFrame) -> BaseSignal:
        """
        市場データから売買シグナルを生成する。

        引数:
            market_data: 戦略判定に利用する市場データ

        戻り値:
            BaseSignal: 売買シグナル
        """
        ...


class Executor(Protocol):
    """
    発注実行処理の共通インターフェースを表す。
    """

    def execute(self, order: PaperOrder) -> PaperExecutionResult:
        """
        注文情報を受け取り、実行結果を返す。

        引数:
            order: 実行対象の注文情報

        戻り値:
            PaperExecutionResult: 実行結果
        """
        ...


class MarketTimeChecker(Protocol):
    """
    市場時間判定処理の共通インターフェースを表す。
    """

    def is_open(self, current_datetime: datetime | None = None) -> bool:
        """
        指定時刻が市場時間内かどうかを判定する。

        引数:
            current_datetime: 判定対象時刻

        戻り値:
            bool: 市場時間内なら True
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
        現在時刻が平日の市場時間内かどうかを判定する。

        引数:
            current_datetime: 判定対象時刻。未指定時は現在時刻を使用する

        戻り値:
            bool: 市場時間内なら True
        """
        target_datetime = current_datetime or datetime.now()
        if target_datetime.weekday() >= 5:
            return False

        current_time = target_datetime.time()
        return self.market_open_time <= current_time <= self.market_close_time


class StubMarketDataProvider:
    """
    最小売買フロー確認用のダミー市場データを返すスタブ実装。
    """

    def fetch(self, symbol: str) -> pd.DataFrame:
        """
        ダミーの終値データを返す。

        引数:
            symbol: データ取得対象の銘柄コード

        戻り値:
            pd.DataFrame: 終値列を持つダミー市場データ
        """
        return pd.DataFrame(
            {
                "symbol": [symbol] * 26,
                "close": [
                    100.0,
                    101.0,
                    102.0,
                    103.0,
                    104.0,
                    105.0,
                    106.0,
                    107.0,
                    108.0,
                    109.0,
                    110.0,
                    111.0,
                    112.0,
                    113.0,
                    114.0,
                    115.0,
                    116.0,
                    117.0,
                    118.0,
                    119.0,
                    120.0,
                    121.0,
                    122.0,
                    123.0,
                    124.0,
                    130.0,
                ],
            }
        )


class Engine:
    """
    市場データ取得から戦略実行、executor 呼び出しまでの最小売買フローを管理する。
    """

    def __init__(
        self,
        config: EngineConfig,
        strategy: Strategy | None = None,
        executor: Executor | None = None,
        data_provider: MarketDataProvider | None = None,
        market_time_checker: MarketTimeChecker | None = None,
        system_logger: logging.Logger | None = None,
    ) -> None:
        """
        売買エンジンを初期化する。

        引数:
            config: 売買実行に必要な設定
            strategy: シグナル生成戦略
            executor: 発注実行処理
            data_provider: 市場データ取得処理
            market_time_checker: 市場時間判定処理
            system_logger: システムログ出力先

        戻り値:
            なし
        """
        self.config = config
        self.strategy = strategy or TrendStrategy()
        self.executor = executor or PaperExecutor()
        self.data_provider = data_provider or StubMarketDataProvider()
        self.market_time_checker = market_time_checker or DefaultMarketTimeChecker()
        self.system_logger = system_logger or logger

    def run_once(self, current_datetime: datetime | None = None) -> PaperExecutionResult | None:
        """
        市場時間チェックからシグナル判定、擬似約定までの最小売買フローを1回実行する。

        引数:
            current_datetime: 市場時間判定に使用する時刻

        戻り値:
            PaperExecutionResult | None: 約定した場合は実行結果、処理スキップ時は None
        """
        if not self.market_time_checker.is_open(current_datetime=current_datetime):
            self.system_logger.info("engine skipped: market is closed")
            return None

        market_data = self.data_provider.fetch(symbol=self.config.symbol)
        signal = self.strategy.generate_signal(market_data=market_data)
        self.system_logger.info("engine signal: symbol=%s signal=%s", self.config.symbol, signal.signal.value)

        if signal.signal == SignalType.HOLD:
            self.system_logger.info("engine skipped: signal is hold")
            return None

        order = self._build_order(market_data=market_data, signal=signal)
        execution_result = self.executor.execute(order=order)
        self.system_logger.info(
            "engine executed: symbol=%s side=%s quantity=%s price=%s order_id=%s",
            execution_result.symbol,
            execution_result.side,
            execution_result.quantity,
            execution_result.price,
            execution_result.order_id,
        )
        return execution_result

    def _build_order(self, market_data: pd.DataFrame, signal: BaseSignal) -> PaperOrder:
        """
        市場データとシグナルから executor 用の注文情報を組み立てる。

        引数:
            market_data: 最新価格を含む市場データ
            signal: 戦略から返された売買シグナル

        戻り値:
            PaperOrder: executor へ渡す注文情報
        """
        latest_price = float(market_data[self.config.price_column].astype(float).iloc[-1])
        return PaperOrder(
            symbol=self.config.symbol,
            strategy=self.config.strategy_name,
            side=signal.signal.value,
            quantity=self.config.quantity,
            price=latest_price,
        )

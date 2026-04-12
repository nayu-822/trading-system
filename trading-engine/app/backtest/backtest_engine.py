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
    """
    単一ポジション前提のバックテスト結果を表すモデル。

    引数:
        trades: 実行された売買ログ一覧
        total_trades: 総取引件数
        buy_count: 買い取引件数
        sell_count: 売り取引件数
        total_pnl: 実現損益の合計
        average_pnl: 1 決済あたりの平均損益
        win_rate: 勝率
        max_win_streak: 最大連勝数
        max_loss_streak: 最大連敗数
        max_drawdown: 累積損益ベースの最大ドローダウン額

    戻り値:
        なし
    """

    trades: list[TradeLog]
    total_trades: int
    buy_count: int
    sell_count: int
    total_pnl: float
    average_pnl: float
    win_rate: float
    max_win_streak: int
    max_loss_streak: int
    max_drawdown: float


@dataclass
class BacktestEngine:
    """
    過去データを時系列に沿って逐次処理する簡易バックテストエンジン。

    単一ポジションのみを扱い、同時に複数ポジションは保有しない。
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
            BacktestResult: 売買件数と評価指標を集計したバックテスト結果
        """
        self._validate_market_data(market_data=market_data)

        sorted_market_data = market_data.sort_values("timestamp").reset_index(drop=True)
        trades: list[TradeLog] = []
        buy_count = 0
        sell_count = 0
        current_position: str | None = None
        entry_price: float | None = None
        realized_pnls: list[float] = []
        current_win_streak = 0
        current_loss_streak = 0
        max_win_streak = 0
        max_loss_streak = 0

        for current_index in range(1, len(sorted_market_data) + 1):
            current_market_data = sorted_market_data.iloc[:current_index].copy()
            try:
                signal = self.strategy.generate_signal(market_data=current_market_data)
            except ValueError as exc:
                if self._is_strategy_warmup_error(message=str(exc)):
                    continue
                raise

            if signal.signal == SignalType.HOLD:
                continue

            if signal.signal == SignalType.BUY and current_position is not None:
                continue

            if signal.signal == SignalType.SELL and current_position is None:
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
                current_position = "buy"
                entry_price = execution_result.executed_price
            else:
                sell_count += 1
                pnl = self._calculate_pnl(
                    entry_price=entry_price,
                    exit_price=execution_result.executed_price,
                    quantity=execution_result.quantity,
                )
                realized_pnls.append(pnl)
                (
                    current_win_streak,
                    current_loss_streak,
                    max_win_streak,
                    max_loss_streak,
                ) = self._update_streaks(
                    pnl=pnl,
                    current_win_streak=current_win_streak,
                    current_loss_streak=current_loss_streak,
                    max_win_streak=max_win_streak,
                    max_loss_streak=max_loss_streak,
                )
                current_position = None
                entry_price = None

        return BacktestResult(
            trades=trades,
            total_trades=len(trades),
            buy_count=buy_count,
            sell_count=sell_count,
            total_pnl=sum(realized_pnls),
            average_pnl=(sum(realized_pnls) / len(realized_pnls)) if realized_pnls else 0.0,
            win_rate=(self._count_wins(realized_pnls) / len(realized_pnls)) if realized_pnls else 0.0,
            max_win_streak=max_win_streak,
            max_loss_streak=max_loss_streak,
            max_drawdown=self._calculate_max_drawdown(realized_pnls=realized_pnls),
        )

    def _validate_market_data(self, market_data: pd.DataFrame) -> None:
        """
        バックテストに必要なカラムがそろっているかを検証する。
        引数:
            market_data: 検証対象の市場データ

        戻り値:
            なし
        """
        if market_data.empty:
            raise ValueError("market_data must not be empty")

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

    def _calculate_pnl(self, entry_price: float | None, exit_price: float, quantity: float) -> float:
        """
        エントリー価格と決済価格から実現損益を計算する。
        引数:
            entry_price: エントリー価格
            exit_price: 決済価格
            quantity: 売買数量

        戻り値:
            float: 実現損益
        """
        if entry_price is None:
            raise ValueError("entry_price is required to calculate pnl")

        return (exit_price - entry_price) * quantity

    def _update_streaks(
        self,
        pnl: float,
        current_win_streak: int,
        current_loss_streak: int,
        max_win_streak: int,
        max_loss_streak: int,
    ) -> tuple[int, int, int, int]:
        """
        損益に応じて連勝・連敗回数を更新する。
        引数:
            pnl: 今回トレードの損益
            current_win_streak: 現在の連勝数
            current_loss_streak: 現在の連敗数
            max_win_streak: 最大連勝数
            max_loss_streak: 最大連敗数

        戻り値:
            tuple[int, int, int, int]: 更新後の連勝・連敗状態
        """
        if pnl > 0:
            current_win_streak += 1
            current_loss_streak = 0
            max_win_streak = max(max_win_streak, current_win_streak)
        elif pnl < 0:
            current_loss_streak += 1
            current_win_streak = 0
            max_loss_streak = max(max_loss_streak, current_loss_streak)
        else:
            current_win_streak = 0
            current_loss_streak = 0

        return current_win_streak, current_loss_streak, max_win_streak, max_loss_streak

    def _count_wins(self, realized_pnls: list[float]) -> int:
        """
        実現損益のうち勝ちトレード数を数える。
        引数:
            realized_pnls: 実現損益の一覧

        戻り値:
            int: 勝ちトレード数
        """
        return sum(1 for pnl in realized_pnls if pnl > 0)

    def _calculate_max_drawdown(self, realized_pnls: list[float]) -> float:
        """
        実現損益の累積推移から最大ドローダウン額を計算する。
        引数:
            realized_pnls: 実現損益の一覧

        戻り値:
            float: 累積損益ベースの最大ドローダウン額
        """
        cumulative_pnl = 0.0
        peak_pnl = 0.0
        max_drawdown = 0.0

        for pnl in realized_pnls:
            cumulative_pnl += pnl
            peak_pnl = max(peak_pnl, cumulative_pnl)
            max_drawdown = max(max_drawdown, peak_pnl - cumulative_pnl)

        return max_drawdown

    def _is_strategy_warmup_error(self, message: str) -> bool:
        """
        戦略のウォームアップ不足による例外かどうかを判定する。
        引数:
            message: 例外メッセージ

        戻り値:
            bool: ウォームアップ不足由来なら True
        """
        return message.startswith("market_data must contain at least ")

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Protocol

import pandas as pd

from app.backtest.backtest_engine import BacktestConfig, BacktestEngine, BacktestResult, Strategy
from app.execution.executor import Executor


class StrategyFactory(Protocol):
    """
    最適化時に戦略を生成する呼び出しインターフェース。
    """

    def __call__(self, strategy_name: str, **parameters: int | float) -> Strategy:
        """
        指定したパラメータで戦略を生成する。

        Args:
            strategy_name: 生成する戦略名。
            parameters: 戦略生成時に使用するパラメータ。

        Returns:
            Strategy: 生成した戦略オブジェクト。
        """
        ...


class ExecutorFactory(Protocol):
    """
    最適化時に executor を生成する呼び出しインターフェース。
    """

    def __call__(self) -> Executor:
        """
        executor を生成する。

        Args:
            なし。

        Returns:
            Executor: 生成した executor。
        """
        ...


@dataclass(frozen=True)
class OptimizationConfig:
    """
    パラメータ最適化の実行設定を表す。

    Args:
        symbol: 最適化対象の銘柄コード。
        strategy_name: 最適化対象の戦略名。
        quantity: バックテストで使用する数量。
        top_n: ランキングとして保持する件数。
        price_column: バックテストで使用する価格列名。

    Returns:
        なし。
    """

    symbol: str
    strategy_name: str
    quantity: float
    top_n: int = 3
    price_column: str = "close"


@dataclass(frozen=True)
class RankedOptimizationResult:
    """
    1 つのパラメータ組み合わせに対応する最適化結果を表す。

    Args:
        parameters: 使用したパラメータ。
        result: 対応するバックテスト結果。

    Returns:
        なし。
    """

    parameters: dict[str, int | float]
    result: BacktestResult


@dataclass(frozen=True)
class OptimizationResult:
    """
    パラメータ最適化の集計結果を表す。

    Args:
        best_parameters: 最良だったパラメータ。
        best_total_pnl: 最良結果の総損益。
        best_win_rate: 最良結果の勝率。
        best_max_drawdown: 最良結果の最大ドローダウン。
        ranking: 上位ランキング。

    Returns:
        なし。
    """

    best_parameters: dict[str, int | float]
    best_total_pnl: float
    best_win_rate: float
    best_max_drawdown: float
    ranking: list[RankedOptimizationResult]


@dataclass
class ParameterOptimizer:
    """
    グリッドサーチで戦略パラメータを探索する最適化クラス。
    """

    config: OptimizationConfig
    create_strategy: StrategyFactory
    create_executor: ExecutorFactory

    def optimize(
        self,
        *,
        market_data: pd.DataFrame,
        parameter_grid: dict[str, list[int | float]],
    ) -> OptimizationResult:
        """
        指定したパラメータ範囲を総当たりで探索し、最良結果を返す。

        Args:
            market_data: 最適化に使用する市場データ。
            parameter_grid: 探索するパラメータ範囲。

        Returns:
            OptimizationResult: 最適化結果。
        """
        ranked_results: list[RankedOptimizationResult] = []

        for parameters in self._generate_parameter_combinations(parameter_grid=parameter_grid):
            strategy = self.create_strategy(self.config.strategy_name, **parameters)
            executor = self.create_executor()
            engine = BacktestEngine(
                config=BacktestConfig(
                    symbol=self.config.symbol,
                    strategy_name=self.config.strategy_name,
                    quantity=self.config.quantity,
                    price_column=self.config.price_column,
                ),
                strategy=strategy,
                executor=executor,
                trade_logger=None,
            )
            result = engine.run(market_data=market_data.copy())
            ranked_results.append(
                RankedOptimizationResult(
                    parameters=parameters,
                    result=result,
                )
            )

        if not ranked_results:
            raise ValueError("parameter_grid must contain at least one parameter combination")

        sorted_results = sorted(
            ranked_results,
            key=lambda item: (
                item.result.total_pnl,
                item.result.win_rate,
                -item.result.max_drawdown,
            ),
            reverse=True,
        )
        best_result = sorted_results[0]

        return OptimizationResult(
            best_parameters=best_result.parameters,
            best_total_pnl=best_result.result.total_pnl,
            best_win_rate=best_result.result.win_rate,
            best_max_drawdown=best_result.result.max_drawdown,
            ranking=sorted_results[: self.config.top_n],
        )

    def _generate_parameter_combinations(
        self,
        *,
        parameter_grid: dict[str, list[int | float]],
    ) -> list[dict[str, int | float]]:
        """
        グリッドサーチ用のパラメータ組み合わせを生成する。

        Args:
            parameter_grid: パラメータ候補の一覧。

        Returns:
            list[dict[str, int | float]]: 生成した全組み合わせ。
        """
        if not parameter_grid:
            return [{}]

        parameter_names = list(parameter_grid.keys())
        parameter_values = [parameter_grid[name] for name in parameter_names]

        if any(len(values) == 0 for values in parameter_values):
            raise ValueError("parameter_grid values must not be empty")

        combinations: list[dict[str, int | float]] = []
        for values in product(*parameter_values):
            combinations.append(dict(zip(parameter_names, values)))

        return combinations

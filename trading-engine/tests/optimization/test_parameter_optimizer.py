from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.backtest.backtest_engine import BacktestResult
from app.optimization.parameter_optimizer import OptimizationConfig, ParameterOptimizer


class DummyStrategy:
    def __init__(self, parameters: dict[str, int | float]) -> None:
        self.parameters = parameters

    def generate_signal(self, market_data: pd.DataFrame) -> object:
        """
        テスト用のダミーシグナル生成を表す。
        引数:
            market_data: 市場データ

        戻り値:
            object: ダミーオブジェクト
        """
        return object()


class DummyExecutor:
    pass


class DummyBacktestEngine:
    def __init__(self, *, config, strategy, executor, trade_logger=None) -> None:
        self.config = config
        self.strategy = strategy
        self.executor = executor
        self.trade_logger = trade_logger

    def run(self, market_data: pd.DataFrame) -> BacktestResult:
        """
        パラメータに応じて固定のバックテスト結果を返す。
        引数:
            market_data: 市場データ

        戻り値:
            BacktestResult: 固定の結果
        """
        parameters = self.strategy.parameters
        total_pnl = float(parameters.get("rsi_period", 0)) + float(parameters.get("rsi_lower", 0))
        return BacktestResult(
            trades=[],
            total_trades=1,
            buy_count=1,
            sell_count=0,
            total_pnl=total_pnl,
            average_pnl=total_pnl,
            win_rate=1.0 if total_pnl > 0 else 0.0,
            max_win_streak=1,
            max_loss_streak=0,
            max_drawdown=0.0,
        )


def test_generate_parameter_combinations_returns_all_grid_patterns(monkeypatch) -> None:
    optimizer = ParameterOptimizer(
        config=OptimizationConfig(symbol="1306.T", strategy_name="range", quantity=100),
        create_strategy=lambda strategy_name, **parameters: DummyStrategy(parameters),
        create_executor=lambda: DummyExecutor(),
    )

    combinations = optimizer._generate_parameter_combinations(
        parameter_grid={
            "rsi_period": [10, 14],
            "rsi_lower": [25.0, 30.0],
        }
    )

    assert len(combinations) == 4
    assert {"rsi_period": 10, "rsi_lower": 25.0} in combinations
    assert {"rsi_period": 14, "rsi_lower": 30.0} in combinations


def test_optimize_runs_backtest_for_each_parameter_combination(monkeypatch) -> None:
    created_parameters: list[dict[str, int | float]] = []

    def create_dummy_strategy(strategy_name: str, **parameters: int | float) -> DummyStrategy:
        created_parameters.append(parameters)
        return DummyStrategy(parameters)

    monkeypatch.setattr(
        "app.optimization.parameter_optimizer.BacktestEngine",
        DummyBacktestEngine,
    )
    optimizer = ParameterOptimizer(
        config=OptimizationConfig(symbol="1306.T", strategy_name="range", quantity=100),
        create_strategy=create_dummy_strategy,
        create_executor=lambda: DummyExecutor(),
    )

    result = optimizer.optimize(
        market_data=pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-05"]), "close": [100.0]}),
        parameter_grid={
            "rsi_period": [10, 14],
            "rsi_lower": [25.0, 30.0],
        },
    )

    assert len(created_parameters) == 4
    assert result.best_parameters == {"rsi_period": 14, "rsi_lower": 30.0}


def test_optimize_selects_best_parameter_by_total_pnl(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.optimization.parameter_optimizer.BacktestEngine",
        DummyBacktestEngine,
    )
    optimizer = ParameterOptimizer(
        config=OptimizationConfig(symbol="1306.T", strategy_name="range", quantity=100, top_n=2),
        create_strategy=lambda strategy_name, **parameters: DummyStrategy(parameters),
        create_executor=lambda: DummyExecutor(),
    )

    result = optimizer.optimize(
        market_data=pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-05"]), "close": [100.0]}),
        parameter_grid={
            "rsi_period": [10, 20],
            "rsi_lower": [25.0],
        },
    )

    assert result.best_total_pnl == 45.0
    assert result.best_parameters == {"rsi_period": 20, "rsi_lower": 25.0}
    assert len(result.ranking) == 2

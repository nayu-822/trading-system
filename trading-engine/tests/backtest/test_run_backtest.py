from __future__ import annotations

from pathlib import Path
import shutil
import sys
from uuid import uuid4

import pandas as pd
import pytest


sys.path.append(str(Path(__file__).resolve().parents[2]))

import app.backtest.run_backtest as run_backtest_module
from app.backtest.backtest_engine import BacktestResult
from app.optimization.parameter_optimizer import OptimizationResult, RankedOptimizationResult
from app.strategies.range.range_strategy import RangeStrategy


class DummyProvider:
    def __init__(self) -> None:
        self.called_with: tuple[str, str, str] | None = None

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        """
        テスト用の市場データを返す。
        引数:
            symbol: 銘柄コード
            period: 取得期間
            interval: 足種別

        戻り値:
            pd.DataFrame: バックテスト用の市場データ
        """
        self.called_with = (symbol, period, interval)
        return pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-01-05 09:00:00"]),
                "close": [100.0],
            }
        )


class DummyStrategy:
    def generate_signal(self, market_data: pd.DataFrame) -> object:
        """
        テスト用 strategy のダミー実装。
        引数:
            market_data: 市場データ

        戻り値:
            object: ダミーオブジェクト
        """
        return object()


class DummyExecutor:
    pass


class DummyTradeLogger:
    def __init__(self, log_file_path: Path) -> None:
        self.log_file_path = log_file_path


class DummyBacktestEngine:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def run(self, market_data: pd.DataFrame) -> BacktestResult:
        """
        バックテスト結果を固定で返す。
        引数:
            market_data: 実行対象の市場データ

        戻り値:
            BacktestResult: 固定の結果
        """
        return BacktestResult(
            trades=[],
            total_trades=1,
            buy_count=1,
            sell_count=0,
            total_pnl=0.0,
            average_pnl=0.0,
            win_rate=0.0,
            max_win_streak=0,
            max_loss_streak=0,
            max_drawdown=0.0,
        )


def _create_workspace_temp_dir() -> Path:
    """
    ワークスペース配下にテスト用の一時ディレクトリを作成する。
    引数:
        なし

    戻り値:
        Path: 作成した一時ディレクトリ
    """
    temp_dir = Path(".tmp") / f"run-backtest-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    return temp_dir


def test_run_backtest_builds_dependencies_and_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        provider = DummyProvider()
        monkeypatch.setattr(run_backtest_module, "YFinanceDataProvider", lambda: provider)
        captured_strategy_arguments: dict[str, object] = {}

        def create_dummy_strategy(strategy_name: str, **kwargs: object) -> DummyStrategy:
            captured_strategy_arguments["strategy_name"] = strategy_name
            captured_strategy_arguments.update(kwargs)
            return DummyStrategy()

        monkeypatch.setattr(run_backtest_module, "create_strategy", create_dummy_strategy)
        monkeypatch.setattr(run_backtest_module, "create_executor", lambda: DummyExecutor())
        monkeypatch.setattr(run_backtest_module, "TradeLogger", DummyTradeLogger)

        captured_engine_arguments: dict[str, object] = {}

        def create_dummy_backtest_engine(**kwargs) -> DummyBacktestEngine:
            captured_engine_arguments.update(kwargs)
            return DummyBacktestEngine(**kwargs)

        monkeypatch.setattr(run_backtest_module, "BacktestEngine", create_dummy_backtest_engine)

        result = run_backtest_module.run_backtest(
            symbol="7203.T",
            period="6mo",
            interval="1d",
            strategy_name="trend",
            quantity=250,
            trade_log_path=temp_dir / "trade_log.csv",
        )

        assert provider.called_with == ("7203.T", "6mo", "1d")
        assert result.total_trades == 1
        assert captured_engine_arguments["config"].symbol == "7203.T"
        assert captured_engine_arguments["config"].strategy_name == "trend"
        assert captured_engine_arguments["config"].quantity == 250
        assert captured_engine_arguments["trade_logger"].log_file_path == temp_dir / "trade_log.csv"
        assert captured_strategy_arguments["strategy_name"] == "trend"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_run_backtest_raises_for_unsupported_strategy() -> None:
    with pytest.raises(ValueError, match="unsupported strategy: invalid"):
        run_backtest_module.create_strategy("invalid")


def test_create_strategy_returns_range_strategy_with_rsi_parameters() -> None:
    strategy = run_backtest_module.create_strategy(
        "range",
        rsi_period=10,
        rsi_lower=25.0,
        rsi_upper=75.0,
    )

    assert isinstance(strategy, RangeStrategy)
    assert strategy.rsi_period == 10
    assert strategy.lower_threshold == 25.0
    assert strategy.upper_threshold == 75.0


def test_run_backtest_passes_range_parameters_to_create_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = DummyProvider()
    captured_strategy_arguments: dict[str, object] = {}

    def create_dummy_strategy(strategy_name: str, **kwargs: object) -> DummyStrategy:
        captured_strategy_arguments["strategy_name"] = strategy_name
        captured_strategy_arguments.update(kwargs)
        return DummyStrategy()

    monkeypatch.setattr(run_backtest_module, "YFinanceDataProvider", lambda: provider)
    monkeypatch.setattr(run_backtest_module, "create_strategy", create_dummy_strategy)
    monkeypatch.setattr(run_backtest_module, "create_executor", lambda: DummyExecutor())
    monkeypatch.setattr(run_backtest_module, "TradeLogger", DummyTradeLogger)
    monkeypatch.setattr(run_backtest_module, "BacktestEngine", lambda **kwargs: DummyBacktestEngine(**kwargs))

    result = run_backtest_module.run_backtest(
        symbol="1306.T",
        period="1y",
        interval="1d",
        strategy_name="range",
        quantity=100,
        rsi_period=10,
        rsi_lower=25.0,
        rsi_upper=75.0,
        trade_log_path=Path(".tmp/range-test.csv"),
    )

    assert result.total_trades == 1
    assert captured_strategy_arguments["strategy_name"] == "range"
    assert captured_strategy_arguments["rsi_period"] == 10
    assert captured_strategy_arguments["rsi_lower"] == 25.0
    assert captured_strategy_arguments["rsi_upper"] == 75.0


def test_run_backtest_propagates_data_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingProvider:
        def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
            raise ValueError("yfinance data is empty: 1306.T")

    monkeypatch.setattr(run_backtest_module, "YFinanceDataProvider", lambda: FailingProvider())

    with pytest.raises(ValueError, match="yfinance data is empty: 1306.T"):
        run_backtest_module.run_backtest(
            symbol="1306.T",
            period="1y",
            interval="1d",
            strategy_name="trend",
        )


def test_main_returns_error_code_when_backtest_fails(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(run_backtest_module, "run_backtest", lambda **kwargs: (_ for _ in ()).throw(ValueError("unsupported strategy: range")))

    result = run_backtest_module.main(["--strategy", "range"])

    captured = capsys.readouterr()
    assert result == 1
    assert "backtest failed: unsupported strategy: range" in captured.err


def test_main_writes_result_to_stdout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        run_backtest_module,
        "run_backtest",
        lambda **kwargs: BacktestResult(
            trades=[],
            total_trades=3,
            buy_count=2,
            sell_count=1,
            total_pnl=10.0,
            average_pnl=5.0,
            win_rate=0.5,
            max_win_streak=1,
            max_loss_streak=1,
            max_drawdown=2.0,
        ),
    )

    result = run_backtest_module.main(["--symbol", "1306.T", "--period", "1y", "--interval", "1d"])

    captured = capsys.readouterr()
    assert result == 0
    assert "symbol: 1306.T" in captured.out
    assert "total_trades: 3" in captured.out
    assert "total_pnl: 10.0" in captured.out
    assert "average_pnl: 5.0" in captured.out
    assert "win_rate: 0.5" in captured.out
    assert "max_win_streak: 1" in captured.out
    assert "max_loss_streak: 1" in captured.out
    assert "max_drawdown: 2.0" in captured.out


def test_main_runs_successfully_with_range_strategy(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        run_backtest_module,
        "run_backtest",
        lambda **kwargs: BacktestResult(
            trades=[],
            total_trades=2,
            buy_count=1,
            sell_count=1,
            total_pnl=250.0,
            average_pnl=250.0,
            win_rate=1.0,
            max_win_streak=1,
            max_loss_streak=0,
            max_drawdown=0.0,
        ),
    )

    result = run_backtest_module.main(
        ["--symbol", "1306.T", "--period", "30d", "--interval", "5m", "--strategy", "range"]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "symbol: 1306.T" in captured.out
    assert "total_trades: 2" in captured.out
    assert "total_pnl: 250.0" in captured.out


def test_parse_args_accepts_quantity() -> None:
    args = run_backtest_module.parse_args(["--quantity", "250"])

    assert args.quantity == 250.0


def test_parse_args_accepts_range_parameters() -> None:
    args = run_backtest_module.parse_args(
        ["--strategy", "range", "--rsi-period", "10", "--rsi-lower", "25", "--rsi-upper", "75"]
    )

    assert args.strategy == "range"
    assert args.rsi_period == 10
    assert args.rsi_lower == 25.0
    assert args.rsi_upper == 75.0


def test_parse_args_accepts_compare_flag() -> None:
    args = run_backtest_module.parse_args(["--compare"])

    assert args.compare is True


def test_parse_args_accepts_optimize_flag() -> None:
    args = run_backtest_module.parse_args(["--optimize"])

    assert args.optimize is True


def test_run_comparison_backtest_executes_both_strategies_with_same_conditions(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = DummyProvider()
    created_strategies: list[str] = []
    captured_engine_arguments: list[dict[str, object]] = []

    def create_dummy_strategy(strategy_name: str, **kwargs: object) -> DummyStrategy:
        created_strategies.append(strategy_name)
        return DummyStrategy()

    def create_dummy_backtest_engine(**kwargs) -> DummyBacktestEngine:
        captured_engine_arguments.append(kwargs)
        return DummyBacktestEngine(**kwargs)

    monkeypatch.setattr(run_backtest_module, "YFinanceDataProvider", lambda: provider)
    monkeypatch.setattr(run_backtest_module, "create_strategy", create_dummy_strategy)
    monkeypatch.setattr(run_backtest_module, "create_executor", lambda: DummyExecutor())
    monkeypatch.setattr(run_backtest_module, "TradeLogger", DummyTradeLogger)
    monkeypatch.setattr(run_backtest_module, "BacktestEngine", create_dummy_backtest_engine)

    results = run_backtest_module.run_comparison_backtest(
        symbol="1306.T",
        period="1y",
        interval="1d",
        quantity=250,
        trade_log_path=Path(".tmp/comparison-test.csv"),
        rsi_period=10,
        rsi_lower=25.0,
        rsi_upper=75.0,
    )

    assert provider.called_with == ("1306.T", "1y", "1d")
    assert created_strategies == ["trend", "range"]
    assert set(results.keys()) == {"trend", "range"}
    assert captured_engine_arguments[0]["config"].quantity == 250
    assert captured_engine_arguments[1]["config"].quantity == 250
    assert captured_engine_arguments[0]["config"].symbol == "1306.T"
    assert captured_engine_arguments[1]["config"].symbol == "1306.T"


def test_format_comparison_result_contains_both_strategy_results() -> None:
    result = BacktestResult(
        trades=[],
        total_trades=3,
        buy_count=2,
        sell_count=1,
        total_pnl=10.0,
        average_pnl=5.0,
        win_rate=0.5,
        max_win_streak=1,
        max_loss_streak=1,
        max_drawdown=2.0,
    )

    formatted = run_backtest_module.format_comparison_result(
        symbol="1306.T",
        period="1y",
        interval="1d",
        results={"trend": result, "range": result},
    )

    assert "Strategy Comparison:" in formatted
    assert "trend:" in formatted
    assert "range:" in formatted
    assert "total_pnl: 10.0" in formatted
    assert "max_drawdown: 2.0" in formatted


def test_main_writes_comparison_result_to_stdout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    comparison_result = BacktestResult(
        trades=[],
        total_trades=3,
        buy_count=2,
        sell_count=1,
        total_pnl=10.0,
        average_pnl=5.0,
        win_rate=0.5,
        max_win_streak=1,
        max_loss_streak=1,
        max_drawdown=2.0,
    )
    monkeypatch.setattr(
        run_backtest_module,
        "run_comparison_backtest",
        lambda **kwargs: {"trend": comparison_result, "range": comparison_result},
    )

    result = run_backtest_module.main(["--symbol", "1306.T", "--period", "1y", "--interval", "1d", "--compare"])

    captured = capsys.readouterr()
    assert result == 0
    assert "Strategy Comparison:" in captured.out
    assert "trend:" in captured.out
    assert "range:" in captured.out


def test_main_writes_optimization_result_to_stdout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ranked_result = RankedOptimizationResult(
        parameters={"rsi_period": 14, "rsi_lower": 30.0, "rsi_upper": 70.0},
        result=BacktestResult(
            trades=[],
            total_trades=3,
            buy_count=2,
            sell_count=1,
            total_pnl=10.0,
            average_pnl=5.0,
            win_rate=0.5,
            max_win_streak=1,
            max_loss_streak=1,
            max_drawdown=2.0,
        ),
    )
    monkeypatch.setattr(
        run_backtest_module,
        "run_optimization_backtest",
        lambda **kwargs: OptimizationResult(
            best_parameters={"rsi_period": 14, "rsi_lower": 30.0, "rsi_upper": 70.0},
            best_total_pnl=10.0,
            best_win_rate=0.5,
            best_max_drawdown=2.0,
            ranking=[ranked_result],
        ),
    )

    result = run_backtest_module.main(["--symbol", "1306.T", "--period", "1y", "--interval", "1d", "--optimize"])

    captured = capsys.readouterr()
    assert result == 0
    assert "Optimization Result:" in captured.out
    assert "best_total_pnl: 10.0" in captured.out
    assert "best_max_drawdown: 2.0" in captured.out


def test_main_returns_error_when_compare_and_optimize_are_used_together(capsys: pytest.CaptureFixture[str]) -> None:
    result = run_backtest_module.main(["--compare", "--optimize"])

    captured = capsys.readouterr()
    assert result == 1
    assert "compare and optimize cannot be used together" in captured.err

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from pathlib import Path
import sys

from app.backtest.backtest_engine import BacktestConfig, BacktestEngine, BacktestResult, Strategy
from app.data.yfinance_data_provider import YFinanceDataProvider
from app.execution.executor import Executor
from app.execution.paper_executor import PaperExecutor
from app.logging.trade_logger import TradeLogger
from app.optimization.parameter_optimizer import OptimizationConfig, OptimizationResult, ParameterOptimizer
from app.strategies.range.range_strategy import RangeStrategy
from app.strategies.trend.trend_strategy import TrendStrategy


DEFAULT_SYMBOL = "7203.T"
DEFAULT_PERIOD = "6mo"
DEFAULT_INTERVAL = "1d"
DEFAULT_STRATEGY = "trend"
DEFAULT_QUANTITY = 100.0
DEFAULT_TRADE_LOG_PATH = Path("logs/trades/backtest_trade_log.csv")
DEFAULT_RSI_PERIOD = 14
DEFAULT_RSI_LOWER = 30.0
DEFAULT_RSI_UPPER = 70.0
DEFAULT_RSI_PERIOD_GRID = "10,14,20"
DEFAULT_RSI_LOWER_GRID = "25,30,35"
DEFAULT_RSI_UPPER_GRID = "65,70,75"


def create_strategy(
    strategy_name: str,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    rsi_lower: float = DEFAULT_RSI_LOWER,
    rsi_upper: float = DEFAULT_RSI_UPPER,
) -> Strategy:
    """
    戦略名に応じたバックテスト用 strategy を生成する。
    引数:
        strategy_name: 使用する戦略名
        rsi_period: range 戦略で利用する RSI 期間
        rsi_lower: range 戦略で利用する RSI 下限
        rsi_upper: range 戦略で利用する RSI 上限

    戻り値:
        Strategy: 戦略オブジェクト
    """
    if strategy_name == "trend":
        return TrendStrategy()

    if strategy_name == "range":
        return RangeStrategy(
            rsi_period=rsi_period,
            lower_threshold=rsi_lower,
            upper_threshold=rsi_upper,
        )

    raise ValueError(f"unsupported strategy: {strategy_name}")


def create_executor() -> Executor:
    """
    バックテストで利用する executor を生成する。
    引数:
        なし

    戻り値:
        Executor: バックテスト用 executor
    """
    return PaperExecutor()


def run_backtest(
    *,
    symbol: str,
    period: str,
    interval: str,
    strategy_name: str,
    quantity: float = DEFAULT_QUANTITY,
    trade_log_path: Path | None = None,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    rsi_lower: float = DEFAULT_RSI_LOWER,
    rsi_upper: float = DEFAULT_RSI_UPPER,
) -> BacktestResult:
    """
    指定条件でバックテストを実行し、結果を返す。
    引数:
        symbol: バックテスト対象の銘柄コード
        period: 取得期間
        interval: 足種別
        strategy_name: 使用する戦略名
        quantity: 売買数量
        trade_log_path: 売買ログ保存先
        rsi_period: range 戦略で利用する RSI 期間
        rsi_lower: range 戦略で利用する RSI 下限
        rsi_upper: range 戦略で利用する RSI 上限

    戻り値:
        BacktestResult: バックテスト結果
    """
    data_provider = YFinanceDataProvider()
    market_data = data_provider.fetch(
        symbol=symbol,
        period=period,
        interval=interval,
    )
    strategy = create_strategy(
        strategy_name=strategy_name,
        rsi_period=rsi_period,
        rsi_lower=rsi_lower,
        rsi_upper=rsi_upper,
    )
    executor = create_executor()
    trade_logger = TradeLogger(log_file_path=trade_log_path or DEFAULT_TRADE_LOG_PATH)
    engine = BacktestEngine(
        config=BacktestConfig(
            symbol=symbol,
            strategy_name=strategy_name,
            quantity=quantity,
        ),
        strategy=strategy,
        executor=executor,
        trade_logger=trade_logger,
    )
    return engine.run(market_data=market_data)


def run_comparison_backtest(
    *,
    symbol: str,
    period: str,
    interval: str,
    quantity: float = DEFAULT_QUANTITY,
    trade_log_path: Path | None = None,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    rsi_lower: float = DEFAULT_RSI_LOWER,
    rsi_upper: float = DEFAULT_RSI_UPPER,
) -> dict[str, BacktestResult]:
    """
    同一条件で trend と range のバックテストを実行し、結果を返す。
    引数:
        symbol: バックテスト対象の銘柄コード
        period: 取得期間
        interval: 足種別
        quantity: 売買数量
        trade_log_path: 売買ログ保存先
        rsi_period: range 戦略で利用する RSI 期間
        rsi_lower: range 戦略で利用する RSI 下限
        rsi_upper: range 戦略で利用する RSI 上限

    戻り値:
        dict[str, BacktestResult]: 戦略名ごとのバックテスト結果
    """
    data_provider = YFinanceDataProvider()
    market_data = data_provider.fetch(
        symbol=symbol,
        period=period,
        interval=interval,
    )
    executor = create_executor()

    results: dict[str, BacktestResult] = {}
    for strategy_name in ("trend", "range"):
        strategy = create_strategy(
            strategy_name=strategy_name,
            rsi_period=rsi_period,
            rsi_lower=rsi_lower,
            rsi_upper=rsi_upper,
        )
        trade_logger = TradeLogger(log_file_path=trade_log_path or DEFAULT_TRADE_LOG_PATH)
        engine = BacktestEngine(
            config=BacktestConfig(
                symbol=symbol,
                strategy_name=strategy_name,
                quantity=quantity,
            ),
            strategy=strategy,
            executor=executor,
            trade_logger=trade_logger,
        )
        results[strategy_name] = engine.run(market_data=market_data.copy())

    return results


def run_optimization_backtest(
    *,
    symbol: str,
    period: str,
    interval: str,
    strategy_name: str,
    quantity: float = DEFAULT_QUANTITY,
    rsi_period_grid: list[int] | None = None,
    rsi_lower_grid: list[float] | None = None,
    rsi_upper_grid: list[float] | None = None,
) -> OptimizationResult:
    """
    指定した戦略のパラメータ最適化を実行し、最良結果を返す。
    引数:
        symbol: 最適化対象の銘柄コード
        period: 取得期間
        interval: 足種別
        strategy_name: 最適化対象の戦略名
        quantity: 売買数量
        rsi_period_grid: RSI 期間候補
        rsi_lower_grid: RSI 下限候補
        rsi_upper_grid: RSI 上限候補

    戻り値:
        OptimizationResult: 最適化結果
    """
    data_provider = YFinanceDataProvider()
    market_data = data_provider.fetch(
        symbol=symbol,
        period=period,
        interval=interval,
    )

    parameter_grid: dict[str, list[int | float]]
    if strategy_name == "range":
        parameter_grid = {
            "rsi_period": list(rsi_period_grid or [10, 14, 20]),
            "rsi_lower": list(rsi_lower_grid or [25.0, 30.0, 35.0]),
            "rsi_upper": list(rsi_upper_grid or [65.0, 70.0, 75.0]),
        }
    elif strategy_name == "trend":
        parameter_grid = {}
    else:
        raise ValueError(f"unsupported strategy: {strategy_name}")

    optimizer = ParameterOptimizer(
        config=OptimizationConfig(
            symbol=symbol,
            strategy_name=strategy_name,
            quantity=quantity,
        ),
        create_strategy=create_strategy,
        create_executor=create_executor,
    )
    return optimizer.optimize(market_data=market_data, parameter_grid=parameter_grid)


def parse_args(argv: list[str] | None = None) -> Namespace:
    """
    CLI 引数を解析する。
    引数:
        argv: 解析対象の引数一覧

    戻り値:
        Namespace: 解析済み引数
    """
    parser = ArgumentParser(description="Run trading-engine backtest with yfinance data.")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="銘柄コード。日本株は 1306.T のように .T を付けます。")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help="取得期間。例: 6mo, 1y")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, help="時間足。例: 1d, 1h, 1m")
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY, help="戦略名。現在は trend, range をサポートします。")
    parser.add_argument("--quantity", type=float, default=DEFAULT_QUANTITY, help="売買数量。デフォルトは 100 です。")
    parser.add_argument("--compare", action="store_true", help="trend と range を同一条件で比較実行します。")
    parser.add_argument("--optimize", action="store_true", help="指定戦略のパラメータ最適化を実行します。")
    parser.add_argument("--rsi-period", type=int, default=DEFAULT_RSI_PERIOD, help="range 戦略で利用する RSI 期間。")
    parser.add_argument("--rsi-lower", type=float, default=DEFAULT_RSI_LOWER, help="range 戦略で利用する RSI 下限。")
    parser.add_argument("--rsi-upper", type=float, default=DEFAULT_RSI_UPPER, help="range 戦略で利用する RSI 上限。")
    parser.add_argument(
        "--rsi-period-grid",
        default=DEFAULT_RSI_PERIOD_GRID,
        help="最適化時に利用する RSI 期間候補。カンマ区切りで指定します。",
    )
    parser.add_argument(
        "--rsi-lower-grid",
        default=DEFAULT_RSI_LOWER_GRID,
        help="最適化時に利用する RSI 下限候補。カンマ区切りで指定します。",
    )
    parser.add_argument(
        "--rsi-upper-grid",
        default=DEFAULT_RSI_UPPER_GRID,
        help="最適化時に利用する RSI 上限候補。カンマ区切りで指定します。",
    )
    parser.add_argument(
        "--trade-log-path",
        default=str(DEFAULT_TRADE_LOG_PATH),
        help="バックテスト取引ログの保存先 CSV パス。",
    )
    return parser.parse_args(argv)


def format_backtest_result(
    *,
    symbol: str,
    period: str,
    interval: str,
    result: BacktestResult,
) -> str:
    """
    バックテスト結果を人が読みやすい文字列へ整形する。
    引数:
        symbol: バックテスト対象の銘柄コード
        period: 取得期間
        interval: 足種別
        result: バックテスト結果

    戻り値:
        str: 表示用の結果文字列
    """
    lines = [
        f"symbol: {symbol}",
        f"period: {period}",
        f"interval: {interval}",
        f"total_trades: {result.total_trades}",
        f"buy_count: {result.buy_count}",
        f"sell_count: {result.sell_count}",
        f"total_pnl: {result.total_pnl}",
        f"average_pnl: {result.average_pnl}",
        f"win_rate: {result.win_rate}",
        f"max_win_streak: {result.max_win_streak}",
        f"max_loss_streak: {result.max_loss_streak}",
        f"max_drawdown: {result.max_drawdown}",
    ]
    return "\n".join(lines) + "\n"


def format_comparison_result(
    *,
    symbol: str,
    period: str,
    interval: str,
    results: dict[str, BacktestResult],
) -> str:
    """
    比較バックテスト結果を人が読みやすい文字列へ整形する。
    引数:
        symbol: バックテスト対象の銘柄コード
        period: 取得期間
        interval: 足種別
        results: 戦略名ごとのバックテスト結果

    戻り値:
        str: 表示用の比較結果文字列
    """
    lines = [
        "Strategy Comparison:",
        f"symbol: {symbol}",
        f"period: {period}",
        f"interval: {interval}",
        "",
    ]

    for strategy_name in ("trend", "range"):
        result = results[strategy_name]
        lines.extend(
            [
                f"{strategy_name}:",
                f"  total_trades: {result.total_trades}",
                f"  total_pnl: {result.total_pnl}",
                f"  average_pnl: {result.average_pnl}",
                f"  win_rate: {result.win_rate}",
                f"  max_win_streak: {result.max_win_streak}",
                f"  max_loss_streak: {result.max_loss_streak}",
                f"  max_drawdown: {result.max_drawdown}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def format_optimization_result(
    *,
    symbol: str,
    period: str,
    interval: str,
    strategy_name: str,
    result: OptimizationResult,
) -> str:
    """
    最適化結果を人が読みやすい文字列へ整形する。
    引数:
        symbol: 最適化対象の銘柄コード
        period: 取得期間
        interval: 足種別
        strategy_name: 最適化対象の戦略名
        result: 最適化結果

    戻り値:
        str: 表示用の最適化結果文字列
    """
    lines = [
        "Optimization Result:",
        f"symbol: {symbol}",
        f"period: {period}",
        f"interval: {interval}",
        f"strategy: {strategy_name}",
        f"best_parameters: {result.best_parameters}",
        f"best_total_pnl: {result.best_total_pnl}",
        f"best_win_rate: {result.best_win_rate}",
        f"best_max_drawdown: {result.best_max_drawdown}",
        "",
        "Ranking:",
    ]

    for index, ranked_result in enumerate(result.ranking, start=1):
        lines.extend(
            [
                f"{index}. parameters: {ranked_result.parameters}",
                f"   total_pnl: {ranked_result.result.total_pnl}",
                f"   win_rate: {ranked_result.result.win_rate}",
                f"   max_drawdown: {ranked_result.result.max_drawdown}",
            ]
        )

    return "\n".join(lines) + "\n"


def _parse_int_grid(raw_value: str) -> list[int]:
    """
    カンマ区切り文字列を整数グリッドへ変換する。
    引数:
        raw_value: カンマ区切り文字列

    戻り値:
        list[int]: 整数グリッド
    """
    return [int(value.strip()) for value in raw_value.split(",") if value.strip()]


def _parse_float_grid(raw_value: str) -> list[float]:
    """
    カンマ区切り文字列を浮動小数点グリッドへ変換する。
    引数:
        raw_value: カンマ区切り文字列

    戻り値:
        list[float]: 浮動小数点グリッド
    """
    return [float(value.strip()) for value in raw_value.split(",") if value.strip()]


def main(argv: list[str] | None = None) -> int:
    """
    CLI からバックテストを実行する。
    引数:
        argv: CLI 引数一覧

    戻り値:
        int: 正常終了時は 0、異常終了時は 1
    """
    args = parse_args(argv)

    if args.compare and args.optimize:
        sys.stderr.write("backtest failed: compare and optimize cannot be used together\n")
        return 1

    try:
        if args.optimize:
            optimization_result = run_optimization_backtest(
                symbol=args.symbol,
                period=args.period,
                interval=args.interval,
                strategy_name=args.strategy,
                quantity=args.quantity,
                rsi_period_grid=_parse_int_grid(args.rsi_period_grid),
                rsi_lower_grid=_parse_float_grid(args.rsi_lower_grid),
                rsi_upper_grid=_parse_float_grid(args.rsi_upper_grid),
            )
        elif args.compare:
            results = run_comparison_backtest(
                symbol=args.symbol,
                period=args.period,
                interval=args.interval,
                quantity=args.quantity,
                trade_log_path=Path(args.trade_log_path),
                rsi_period=args.rsi_period,
                rsi_lower=args.rsi_lower,
                rsi_upper=args.rsi_upper,
            )
        else:
            result = run_backtest(
                symbol=args.symbol,
                period=args.period,
                interval=args.interval,
                strategy_name=args.strategy,
                quantity=args.quantity,
                trade_log_path=Path(args.trade_log_path),
                rsi_period=args.rsi_period,
                rsi_lower=args.rsi_lower,
                rsi_upper=args.rsi_upper,
            )
    except Exception as exc:
        sys.stderr.write(f"backtest failed: {exc}\n")
        return 1

    if args.optimize:
        sys.stdout.write(
            format_optimization_result(
                symbol=args.symbol,
                period=args.period,
                interval=args.interval,
                strategy_name=args.strategy,
                result=optimization_result,
            )
        )
        return 0

    if args.compare:
        sys.stdout.write(
            format_comparison_result(
                symbol=args.symbol,
                period=args.period,
                interval=args.interval,
                results=results,
            )
        )
        return 0

    sys.stdout.write(
        format_backtest_result(
            symbol=args.symbol,
            period=args.period,
            interval=args.interval,
            result=result,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

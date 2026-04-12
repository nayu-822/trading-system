from __future__ import annotations

from argparse import ArgumentParser, Namespace
from pathlib import Path
import sys

from app.backtest.backtest_engine import BacktestConfig, BacktestEngine, BacktestResult, Strategy
from app.data.yfinance_data_provider import YFinanceDataProvider
from app.execution.executor import Executor
from app.execution.paper_executor import PaperExecutor
from app.logging.trade_logger import TradeLogger
from app.strategies.trend.trend_strategy import TrendStrategy


DEFAULT_SYMBOL = "7203.T"
DEFAULT_PERIOD = "6mo"
DEFAULT_INTERVAL = "1d"
DEFAULT_STRATEGY = "trend"
DEFAULT_QUANTITY = 100.0
DEFAULT_TRADE_LOG_PATH = Path("logs/trades/backtest_trade_log.csv")


def create_strategy(strategy_name: str) -> Strategy:
    """
    戦略名に応じたバックテスト用 strategy を生成する。
    引数:
        strategy_name: 使用する戦略名

    戻り値:
        Strategy: 戦略オブジェクト
    """
    if strategy_name == "trend":
        return TrendStrategy()

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

    戻り値:
        BacktestResult: バックテスト結果
    """
    data_provider = YFinanceDataProvider()
    market_data = data_provider.fetch(
        symbol=symbol,
        period=period,
        interval=interval,
    )
    strategy = create_strategy(strategy_name=strategy_name)
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
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY, help="戦略名。現在は trend をサポートします。")
    parser.add_argument("--quantity", type=float, default=DEFAULT_QUANTITY, help="売買数量。デフォルトは 100 です。")
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


def main(argv: list[str] | None = None) -> int:
    """
    CLI からバックテストを実行する。
    引数:
        argv: CLI 引数一覧

    戻り値:
        int: 正常終了時は 0、異常終了時は 1
    """
    args = parse_args(argv)

    try:
        result = run_backtest(
            symbol=args.symbol,
            period=args.period,
            interval=args.interval,
            strategy_name=args.strategy,
            quantity=args.quantity,
            trade_log_path=Path(args.trade_log_path),
        )
    except Exception as exc:
        sys.stderr.write(f"backtest failed: {exc}\n")
        return 1

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

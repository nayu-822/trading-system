from __future__ import annotations

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from datetime import datetime
import logging
import os
from pathlib import Path
from typing import Any

from app.core.engine import DefaultMarketTimeChecker, Engine, EngineConfig, MarketTimeChecker, Strategy
from app.data.market_data import CsvMarketDataProvider, DummyMarketDataProvider, YFinanceMarketDataProvider
from app.domain.models import ExecutionResult
from app.execution.executor import Executor
from app.execution.live_executor import (
    KabuLiveOrderSender,
    LiveExecutionConfig,
    LiveExecutor,
    LiveOrderSender,
)
from app.execution.paper_executor import PaperExecutor
from app.logging.trade_logger import TradeLogger
from app.strategies.range.range_strategy import RangeStrategy
from app.strategies.trend.trend_strategy import TrendStrategy


DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent / "config" / "settings.yaml"
DEFAULT_SYSTEM_LOG_PATH = Path("logs/system/trading_runner.log")
DEFAULT_TRADE_LOG_PATH = Path("logs/trades/trade_log.csv")
DEFAULT_MODE = "paper"
DEFAULT_STRATEGY = "trend"
DEFAULT_DATA_SOURCE = "dummy"
DEFAULT_YFINANCE_PERIOD = "5d"
DEFAULT_YFINANCE_INTERVAL = "1m"
SUPPORTED_MODES: tuple[str, ...] = ("paper", "live")
SUPPORTED_STRATEGIES: tuple[str, ...] = ("trend", "range")
SUPPORTED_DATA_SOURCES: tuple[str, ...] = ("dummy", "csv", "yfinance")


@dataclass(frozen=True)
class ApiSettings:
    host: str = "localhost"
    port: int = 18080
    timeout_seconds: float = 10.0
    exchange: int | None = None
    api_password: str | None = None
    api_token: str | None = None


@dataclass(frozen=True)
class TradingSettings:
    symbol: str = "1306"
    mode: str = DEFAULT_MODE
    strategy: str = DEFAULT_STRATEGY
    quantity: int = 100
    data_source: str = DEFAULT_DATA_SOURCE
    csv_path: str | None = None
    yfinance_period: str = DEFAULT_YFINANCE_PERIOD
    yfinance_interval: str = DEFAULT_YFINANCE_INTERVAL
    trend_short_window: int = 5
    trend_long_window: int = 25
    rsi_period: int = 14
    rsi_lower: float = 30.0
    rsi_upper: float = 70.0
    log_path: str | None = None
    system_log_path: str | None = None
    api: ApiSettings = ApiSettings()


@dataclass(frozen=True)
class RunnerResult:
    status: str
    reason: str | None = None
    execution_result: ExecutionResult | None = None


def load_settings(
    settings_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> TradingSettings:
    """
    設定ファイルと環境変数から通常売買設定を読み込む。
    """
    resolved_path = settings_path or DEFAULT_SETTINGS_PATH
    if not resolved_path.exists():
        raise FileNotFoundError(f"settings file not found: {resolved_path}")

    raw_settings = _load_yaml_like_settings(settings_path=resolved_path)
    env = environ or dict(os.environ)

    return TradingSettings(
        symbol=_read_string_setting(raw_settings, env, "symbol", "TRADING_SYMBOL", "1306"),
        mode=_read_string_setting(raw_settings, env, "mode", "TRADING_MODE", DEFAULT_MODE),
        strategy=_read_string_setting(raw_settings, env, "strategy", "TRADING_STRATEGY", DEFAULT_STRATEGY),
        quantity=_read_int_setting(raw_settings, env, "quantity", "TRADING_QUANTITY", 100),
        data_source=_read_string_setting(
            raw_settings,
            env,
            "data_source",
            "TRADING_DATA_SOURCE",
            DEFAULT_DATA_SOURCE,
        ),
        csv_path=_read_optional_string_setting(raw_settings, env, "csv_path", "TRADING_CSV_PATH"),
        yfinance_period=_read_string_setting(
            raw_settings,
            env,
            "yfinance_period",
            "TRADING_YFINANCE_PERIOD",
            DEFAULT_YFINANCE_PERIOD,
        ),
        yfinance_interval=_read_string_setting(
            raw_settings,
            env,
            "yfinance_interval",
            "TRADING_YFINANCE_INTERVAL",
            DEFAULT_YFINANCE_INTERVAL,
        ),
        trend_short_window=_read_int_setting(
            raw_settings,
            env,
            "trend_short_window",
            "TREND_SHORT_WINDOW",
            5,
        ),
        trend_long_window=_read_int_setting(
            raw_settings,
            env,
            "trend_long_window",
            "TREND_LONG_WINDOW",
            25,
        ),
        rsi_period=_read_int_setting(
            raw_settings,
            env,
            "rsi_period",
            "RANGE_RSI_PERIOD",
            14,
        ),
        rsi_lower=_read_float_setting(
            raw_settings,
            env,
            "rsi_lower",
            "RANGE_RSI_LOWER",
            30.0,
        ),
        rsi_upper=_read_float_setting(
            raw_settings,
            env,
            "rsi_upper",
            "RANGE_RSI_UPPER",
            70.0,
        ),
        log_path=_read_optional_string_setting(raw_settings, env, "log_path", "TRADING_TRADE_LOG_PATH"),
        system_log_path=_read_optional_string_setting(
            raw_settings,
            env,
            "system_log_path",
            "TRADING_SYSTEM_LOG_PATH",
        ),
        api=ApiSettings(
            host=_read_string_setting(raw_settings, env, "api_host", "KABU_API_HOST", ApiSettings.host),
            port=_read_int_setting(raw_settings, env, "api_port", "KABU_API_PORT", ApiSettings.port),
            timeout_seconds=_read_float_setting(
                raw_settings,
                env,
                "api_timeout_seconds",
                "KABU_API_TIMEOUT_SECONDS",
                ApiSettings.timeout_seconds,
            ),
            exchange=_read_optional_int_setting(raw_settings, env, "api_exchange", "KABU_API_EXCHANGE"),
            api_password=_read_optional_string_setting(
                raw_settings,
                env,
                "api_password",
                "KABU_API_PASSWORD",
            ),
            api_token=_read_optional_string_setting(
                raw_settings,
                env,
                "api_token",
                "KABU_API_TOKEN",
            ),
        ),
    )


def create_system_logger(log_path: str | Path | None = None) -> logging.Logger:
    """
    system log 用 logger を生成する。
    """
    system_logger = logging.getLogger("trading_engine.runner")
    system_logger.setLevel(logging.INFO)
    system_logger.propagate = False

    if not any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in system_logger.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        system_logger.addHandler(stream_handler)

    resolved_log_path = Path(log_path) if log_path else DEFAULT_SYSTEM_LOG_PATH
    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
    if not _has_file_handler(system_logger, resolved_log_path):
        file_handler = logging.FileHandler(resolved_log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        system_logger.addHandler(file_handler)

    return system_logger


def create_strategy(
    settings: TradingSettings | str,
    *,
    trend_short_window: int = 5,
    trend_long_window: int = 25,
    rsi_period: int = 14,
    rsi_lower: float = 30.0,
    rsi_upper: float = 70.0,
) -> Strategy:
    """
    設定に応じた strategy を生成する。
    """
    resolved_settings = settings
    if isinstance(settings, str):
        resolved_settings = TradingSettings(
            strategy=settings,
            trend_short_window=trend_short_window,
            trend_long_window=trend_long_window,
            rsi_period=rsi_period,
            rsi_lower=rsi_lower,
            rsi_upper=rsi_upper,
        )

    if resolved_settings.strategy == "trend":
        return TrendStrategy(
            short_window=resolved_settings.trend_short_window,
            long_window=resolved_settings.trend_long_window,
        )

    if resolved_settings.strategy == "range":
        return RangeStrategy(
            rsi_period=resolved_settings.rsi_period,
            lower_threshold=resolved_settings.rsi_lower,
            upper_threshold=resolved_settings.rsi_upper,
        )

    raise ValueError(f"unsupported strategy: {resolved_settings.strategy}")


def create_data_provider(settings: TradingSettings) -> Any:
    """
    設定に応じたデータ provider を生成する。
    """
    if settings.data_source == "dummy":
        return DummyMarketDataProvider()

    if settings.data_source == "csv":
        if settings.csv_path is None:
            raise ValueError("csv_path is required when data_source is csv")
        return CsvMarketDataProvider(csv_path=Path(settings.csv_path))

    if settings.data_source == "yfinance":
        return YFinanceMarketDataProvider(
            period=settings.yfinance_period,
            interval=settings.yfinance_interval,
        )

    raise ValueError(f"unsupported data_source: {settings.data_source}")


def create_executor(
    settings: TradingSettings,
    *,
    live_order_sender: LiveOrderSender | None = None,
) -> Executor:
    """
    設定に応じた executor を生成する。
    """
    if settings.mode == "paper":
        return PaperExecutor()

    if settings.mode == "live":
        sender = live_order_sender or KabuLiveOrderSender(
            config=LiveExecutionConfig(
                host=settings.api.host,
                port=settings.api.port,
                timeout_seconds=settings.api.timeout_seconds,
                exchange=settings.api.exchange,
                api_password=settings.api.api_password,
                api_token=settings.api.api_token,
            )
        )
        return LiveExecutor(order_sender=sender)

    raise ValueError(f"unsupported mode: {settings.mode}")


def validate_settings(
    settings: TradingSettings,
    *,
    market_time_checker: MarketTimeChecker | None = None,
    current_datetime: datetime | None = None,
) -> None:
    """
    通常売買 runner の起動前安全チェックを行う。
    """
    if settings.mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported mode: {settings.mode}")

    if settings.strategy not in SUPPORTED_STRATEGIES:
        raise ValueError(f"unsupported strategy: {settings.strategy}")

    if settings.data_source not in SUPPORTED_DATA_SOURCES:
        raise ValueError(f"unsupported data_source: {settings.data_source}")

    if not settings.symbol.strip():
        raise ValueError("symbol is required")

    if settings.quantity <= 0:
        raise ValueError("quantity must be greater than zero")

    if settings.log_path is not None and not settings.log_path.strip():
        raise ValueError("log_path must not be empty")

    if settings.system_log_path is not None and not settings.system_log_path.strip():
        raise ValueError("system_log_path must not be empty")

    create_strategy(settings=settings)
    create_data_provider(settings=settings)

    if settings.mode != "live":
        return

    if not settings.api.host.strip():
        raise ValueError("api_host is required for live mode")

    if settings.api.port <= 0:
        raise ValueError("api_port must be greater than zero")

    if settings.api.exchange is None:
        raise ValueError("api_exchange is required for live mode")

    if not settings.api.api_password and not settings.api.api_token:
        raise ValueError("api_password or api_token is required for live mode")

    checker = market_time_checker or DefaultMarketTimeChecker()
    if not checker.is_open(current_datetime=current_datetime):
        raise ValueError("live mode requires market to be open")

    raise ValueError("live mode is not supported yet")


def build_engine(
    settings: TradingSettings,
    *,
    system_logger: logging.Logger | None = None,
    market_time_checker: MarketTimeChecker | None = None,
    data_provider: Any | None = None,
    live_order_sender: LiveOrderSender | None = None,
) -> Engine:
    """
    通常売買用 Engine を構築する。
    """
    validate_settings(
        settings=settings,
        market_time_checker=market_time_checker,
    )

    logger_instance = system_logger or create_system_logger(settings.system_log_path)
    trade_log_path = Path(settings.log_path) if settings.log_path else DEFAULT_TRADE_LOG_PATH
    logger_instance.info("strategy: %s", settings.strategy)
    logger_instance.info("data_source: %s", settings.data_source)
    if settings.strategy == "range":
        logger_instance.info("rsi_period: %s", settings.rsi_period)
        logger_instance.info("rsi_lower: %s", settings.rsi_lower)
        logger_instance.info("rsi_upper: %s", settings.rsi_upper)

    return Engine(
        config=EngineConfig(
            symbol=settings.symbol,
            strategy_name=settings.strategy,
            quantity=settings.quantity,
        ),
        data_provider=data_provider or create_data_provider(settings=settings),
        strategy=create_strategy(settings=settings),
        executor=create_executor(settings=settings, live_order_sender=live_order_sender),
        market_time_checker=market_time_checker or DefaultMarketTimeChecker(),
        system_logger=logger_instance,
        trade_logger=TradeLogger(log_file_path=trade_log_path),
    )


def run_trading(
    *,
    settings_path: Path | None = None,
    current_datetime: datetime | None = None,
    market_time_checker: MarketTimeChecker | None = None,
    live_order_sender: LiveOrderSender | None = None,
) -> RunnerResult:
    """
    通常売買 runner を 1 回実行する。
    """
    settings = load_settings(settings_path=settings_path)
    system_logger = create_system_logger(settings.system_log_path)

    system_logger.info(
        "runner started: symbol=%s mode=%s strategy=%s quantity=%s",
        settings.symbol,
        settings.mode,
        settings.strategy,
        settings.quantity,
    )

    try:
        validate_settings(
            settings=settings,
            market_time_checker=market_time_checker,
            current_datetime=current_datetime,
        )
        engine = build_engine(
            settings=settings,
            system_logger=system_logger,
            market_time_checker=market_time_checker,
            live_order_sender=live_order_sender,
        )
        execution_result = engine.run_once(current_datetime=current_datetime)
        if execution_result is None:
            system_logger.info(
                "runner skipped: symbol=%s mode=%s strategy=%s",
                settings.symbol,
                settings.mode,
                settings.strategy,
            )
            return RunnerResult(status="skipped", reason="engine returned no execution")

        system_logger.info(
            "runner finished: symbol=%s mode=%s strategy=%s success=%s",
            settings.symbol,
            settings.mode,
            settings.strategy,
            execution_result.success,
        )
        return RunnerResult(status="executed", execution_result=execution_result)
    except Exception as exc:
        system_logger.exception(
            "runner failed: symbol=%s mode=%s strategy=%s reason=%s",
            settings.symbol,
            settings.mode,
            settings.strategy,
            str(exc),
        )
        raise


def parse_args(argv: list[str] | None = None) -> Namespace:
    """
    CLI 引数を解析する。
    """
    parser = ArgumentParser(description="Run trading-engine trading runner once.")
    parser.add_argument(
        "--settings",
        default=str(DEFAULT_SETTINGS_PATH),
        help="通常売買設定ファイルのパス",
    )
    return parser.parse_args(argv)


def main(
    settings_path: Path | None = None,
    current_datetime: datetime | None = None,
    market_time_checker: MarketTimeChecker | None = None,
    argv: list[str] | None = None,
) -> int:
    """
    通常売買 runner の CLI / 呼び出し入口。
    """
    resolved_settings_path = settings_path
    if resolved_settings_path is None and argv is not None:
        resolved_settings_path = Path(parse_args(argv).settings)
    elif resolved_settings_path is None and argv is None:
        resolved_settings_path = DEFAULT_SETTINGS_PATH

    try:
        run_trading(
            settings_path=resolved_settings_path,
            current_datetime=current_datetime,
            market_time_checker=market_time_checker,
        )
        return 0
    except Exception:
        return 1


def _load_yaml_like_settings(settings_path: Path) -> dict[str, Any]:
    """
    設定ファイルを読み込み、フラットな辞書へ変換する。
    """
    try:
        import yaml  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("PyYAML is required to load trading settings") from exc

    loaded = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError("settings.yaml must contain a mapping")
    return {str(key): value for key, value in loaded.items()}


def _read_string_setting(
    raw_settings: dict[str, Any],
    environ: dict[str, str],
    key: str,
    env_name: str,
    default: str,
) -> str:
    value = environ.get(env_name, raw_settings.get(key, default))
    return str(value)


def _read_optional_string_setting(
    raw_settings: dict[str, Any],
    environ: dict[str, str],
    key: str,
    env_name: str,
) -> str | None:
    value = environ.get(env_name, raw_settings.get(key))
    if value in {None, ""}:
        return None
    return str(value)


def _read_int_setting(
    raw_settings: dict[str, Any],
    environ: dict[str, str],
    key: str,
    env_name: str,
    default: int,
) -> int:
    value = environ.get(env_name, raw_settings.get(key, default))
    return int(value)


def _read_optional_int_setting(
    raw_settings: dict[str, Any],
    environ: dict[str, str],
    key: str,
    env_name: str,
) -> int | None:
    value = environ.get(env_name, raw_settings.get(key))
    if value in {None, ""}:
        return None
    return int(value)


def _read_float_setting(
    raw_settings: dict[str, Any],
    environ: dict[str, str],
    key: str,
    env_name: str,
    default: float,
) -> float:
    value = environ.get(env_name, raw_settings.get(key, default))
    return float(value)


def _has_file_handler(logger: logging.Logger, log_path: Path) -> bool:
    resolved_log_path = log_path.resolve()
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            base_filename = getattr(handler, "baseFilename", None)
            if base_filename and Path(base_filename).resolve() == resolved_log_path:
                return True
    return False


if __name__ == "__main__":
    raise SystemExit(main(argv=os.sys.argv[1:]))

from __future__ import annotations

from dataclasses import dataclass

from app.run_trading import (
    DEFAULT_SETTINGS_PATH,
    DEFAULT_SYSTEM_LOG_PATH,
    DEFAULT_TRADE_LOG_PATH,
    ApiSettings,
    RunnerResult,
    SUPPORTED_DATA_SOURCES,
    TradingSettings,
    build_engine,
    create_data_provider,
    create_executor,
    create_strategy,
    create_system_logger,
    load_settings,
    main,
    parse_args,
    run_trading,
    validate_settings,
)

AppSettings = TradingSettings


@dataclass(frozen=True)
class TrendStrategySettings:
    short_window: int = 5
    long_window: int = 25


@dataclass(frozen=True)
class RangeStrategySettings:
    rsi_period: int = 14
    rsi_lower: float = 30.0
    rsi_upper: float = 70.0


@dataclass(frozen=True)
class LogSettings:
    system_log_path: str | None = None
    trade_log_path: str | None = None


if __name__ == "__main__":
    raise SystemExit(main())

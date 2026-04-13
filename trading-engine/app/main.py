from __future__ import annotations

from app.run_trading import (
    DEFAULT_SETTINGS_PATH,
    DEFAULT_SYSTEM_LOG_PATH,
    DEFAULT_TRADE_LOG_PATH,
    ApiSettings,
    LogSettings,
    RangeStrategySettings,
    RunnerResult,
    TradingSettings,
    TrendStrategySettings,
    build_engine,
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


if __name__ == "__main__":
    raise SystemExit(main())

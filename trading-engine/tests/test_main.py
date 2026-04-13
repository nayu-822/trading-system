from __future__ import annotations

from datetime import datetime
from pathlib import Path
import csv
import io
import logging
import shutil
import sys
from uuid import uuid4

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.execution.live_executor import LiveExecutor
from app.main import (
    DEFAULT_TRADE_LOG_PATH,
    AppSettings,
    ApiSettings,
    LogSettings,
    RangeStrategySettings,
    TrendStrategySettings,
    build_engine,
    create_strategy,
    create_system_logger,
    load_settings,
    main,
    run_trading,
)
from app.strategies.range.range_strategy import RangeStrategy
from app.strategies.trend.trend_strategy import TrendStrategy


class AlwaysOpenMarketTimeChecker:
    def is_open(self, current_datetime: datetime | None = None) -> bool:
        return True


class ClosedMarketTimeChecker:
    def is_open(self, current_datetime: datetime | None = None) -> bool:
        return False


def _create_workspace_temp_dir() -> Path:
    temp_dir = Path(".tmp") / f"main-test-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    return temp_dir


def test_load_settings_returns_defaults_when_file_does_not_exist() -> None:
    settings = load_settings(settings_path=Path(".tmp/not-found-settings.yaml"))

    assert settings == AppSettings()


def test_load_settings_reads_range_parameters() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        settings_path = temp_dir / "settings.yaml"
        settings_path.write_text(
            "\n".join(
                [
                    "symbol: '1306'",
                    "mode: paper",
                    "strategy: range",
                    "quantity: 100",
                    "rsi_period: 10",
                    "rsi_lower: 25",
                    "rsi_upper: 75",
                ]
            ),
            encoding="utf-8",
        )

        settings = load_settings(settings_path=settings_path)

        assert settings.strategy == "range"
        assert settings.range.rsi_period == 10
        assert settings.range.rsi_lower == 25.0
        assert settings.range.rsi_upper == 75.0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_load_settings_prefers_environment_variables() -> None:
    settings = load_settings(
        settings_path=Path(".tmp/not-found-settings.yaml"),
        environ={
            "TRADING_MODE": "live",
            "TRADING_SYMBOL": "7203",
            "TRADING_QUANTITY": "200",
            "TRADING_STRATEGY": "range",
            "RANGE_RSI_PERIOD": "9",
            "RANGE_RSI_LOWER": "20",
            "RANGE_RSI_UPPER": "80",
            "KABU_API_EXCHANGE": "1",
            "KABU_API_TOKEN": "token",
        },
    )

    assert settings.mode == "live"
    assert settings.symbol == "7203"
    assert settings.quantity == 200
    assert settings.strategy == "range"
    assert settings.range.rsi_period == 9
    assert settings.api.exchange == 1
    assert settings.api.api_token == "token"


def test_main_runs_paper_mode_and_creates_trade_log_csv() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        settings_path = temp_dir / "settings.yaml"
        log_file_path = temp_dir / "logs" / "trades" / "trade_log.csv"
        settings_path.write_text(
            "\n".join(
                [
                    "symbol: '7203'",
                    "mode: paper",
                    "strategy: trend",
                    f"trade_log_path: '{log_file_path.as_posix()}'",
                    "quantity: 100",
                ]
            ),
            encoding="utf-8",
        )

        result_code = main(
            settings_path=settings_path,
            current_datetime=datetime(2026, 4, 13, 9, 0, 0),
            market_time_checker=AlwaysOpenMarketTimeChecker(),
        )

        assert result_code == 0
        assert log_file_path.exists()

        with log_file_path.open("r", encoding="utf-8", newline="") as csv_file:
            rows = list(csv.reader(csv_file))

        assert rows[0] == [
            "timestamp",
            "symbol",
            "side",
            "price",
            "quantity",
            "success",
            "message",
            "strategy",
        ]
        assert rows[1][1] == "7203"
        assert rows[1][2] == "buy"
        assert rows[1][7] == "trend"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_run_trading_returns_skipped_when_market_is_closed_in_paper_mode() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        settings_path = temp_dir / "settings.yaml"
        settings_path.write_text(
            "\n".join(
                [
                    "symbol: '7203'",
                    "mode: paper",
                    "strategy: trend",
                    "quantity: 100",
                ]
            ),
            encoding="utf-8",
        )

        result = run_trading(
            settings_path=settings_path,
            current_datetime=datetime(2026, 4, 13, 20, 0, 0),
            market_time_checker=ClosedMarketTimeChecker(),
        )

        assert result.status == "skipped"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_main_returns_one_when_live_mode_missing_api_settings() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        settings_path = temp_dir / "settings.yaml"
        settings_path.write_text(
            "\n".join(
                [
                    "symbol: '7203'",
                    "mode: live",
                    "strategy: trend",
                    "quantity: 100",
                    "api_exchange: 1",
                ]
            ),
            encoding="utf-8",
        )

        result_code = main(
            settings_path=settings_path,
            current_datetime=datetime(2026, 4, 13, 9, 0, 0),
            market_time_checker=AlwaysOpenMarketTimeChecker(),
        )

        assert result_code == 1
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_build_engine_uses_trade_log_path_from_settings() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        log_file_path = temp_dir / "custom" / "trade_log.csv"
        engine = build_engine(
            settings=AppSettings(
                symbol="1306",
                mode="paper",
                strategy="trend",
                quantity=100,
                logs=LogSettings(trade_log_path=str(log_file_path)),
            ),
            market_time_checker=AlwaysOpenMarketTimeChecker(),
        )

        assert engine.trade_logger is not None
        assert engine.trade_logger.log_file_path == log_file_path  # type: ignore[attr-defined]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_build_engine_uses_default_trade_log_path_when_log_path_is_not_set() -> None:
    engine = build_engine(
        settings=AppSettings(
            symbol="1306",
            mode="paper",
            strategy="trend",
            quantity=100,
        ),
        market_time_checker=AlwaysOpenMarketTimeChecker(),
    )

    assert engine.trade_logger is not None
    assert engine.trade_logger.log_file_path == DEFAULT_TRADE_LOG_PATH  # type: ignore[attr-defined]


def test_create_system_logger_does_not_add_duplicate_handlers() -> None:
    logger_first = create_system_logger()
    handler_count = len(logger_first.handlers)

    logger_second = create_system_logger()

    assert logger_second is logger_first
    assert logger_second.propagate is False
    assert len(logger_second.handlers) == handler_count


def test_create_strategy_returns_trend_strategy_instance() -> None:
    strategy = create_strategy(
        AppSettings(
            strategy="trend",
            trend=TrendStrategySettings(short_window=3, long_window=10),
        )
    )

    assert hasattr(strategy, "generate_signal")
    assert isinstance(strategy, TrendStrategy)
    assert strategy.short_window == 3
    assert strategy.long_window == 10


def test_create_strategy_returns_range_strategy_instance() -> None:
    strategy = create_strategy(
        AppSettings(
            strategy="range",
            range=RangeStrategySettings(rsi_period=10, rsi_lower=25.0, rsi_upper=75.0),
        )
    )

    assert isinstance(strategy, RangeStrategy)
    assert strategy.rsi_period == 10
    assert strategy.lower_threshold == 25.0
    assert strategy.upper_threshold == 75.0


def test_build_engine_logs_range_strategy_parameters() -> None:
    logger = create_system_logger()
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    try:
        build_engine(
            settings=AppSettings(
                symbol="1306",
                mode="paper",
                strategy="range",
                quantity=100,
                range=RangeStrategySettings(rsi_period=10, rsi_lower=25.0, rsi_upper=75.0),
            ),
            system_logger=logger,
            market_time_checker=AlwaysOpenMarketTimeChecker(),
        )
    finally:
        logger.removeHandler(handler)

    logged_text = log_stream.getvalue()
    assert "strategy: range" in logged_text
    assert "rsi_period: 10" in logged_text
    assert "rsi_lower: 25.0" in logged_text
    assert "rsi_upper: 75.0" in logged_text


def test_build_engine_raises_for_unsupported_mode() -> None:
    with pytest.raises(ValueError, match="unsupported mode: invalid"):
        build_engine(
            settings=AppSettings(
                symbol="1306",
                mode="invalid",
                strategy="trend",
                quantity=100,
            ),
            market_time_checker=AlwaysOpenMarketTimeChecker(),
        )


def test_build_engine_raises_for_unsupported_strategy() -> None:
    with pytest.raises(ValueError, match="unsupported strategy: invalid"):
        build_engine(
            settings=AppSettings(
                symbol="1306",
                mode="paper",
                strategy="invalid",
                quantity=100,
            ),
            market_time_checker=AlwaysOpenMarketTimeChecker(),
        )


def test_build_engine_creates_live_executor() -> None:
    engine = build_engine(
        settings=AppSettings(
            symbol="1306",
            mode="live",
            strategy="trend",
            quantity=100,
            api=ApiSettings(
                exchange=1,
                api_token="token",
            ),
        ),
        market_time_checker=AlwaysOpenMarketTimeChecker(),
    )

    assert isinstance(engine.executor, LiveExecutor)

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

from app.data.market_data import CsvMarketDataProvider, DummyMarketDataProvider, YFinanceMarketDataProvider
from app.main import (
    DEFAULT_TRADE_LOG_PATH,
    AppSettings,
    ApiSettings,
    build_engine,
    create_data_provider,
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
                    "data_source: dummy",
                    "rsi_period: 10",
                    "rsi_lower: 25",
                    "rsi_upper: 75",
                ]
            ),
            encoding="utf-8",
        )

        settings = load_settings(settings_path=settings_path)

        assert settings.strategy == "range"
        assert settings.rsi_period == 10
        assert settings.rsi_lower == 25.0
        assert settings.rsi_upper == 75.0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_load_settings_raises_when_file_does_not_exist() -> None:
    with pytest.raises(FileNotFoundError, match="settings file not found"):
        load_settings(settings_path=Path(".tmp/not-found-settings.yaml"))


def test_load_settings_prefers_environment_variables() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        settings_path = temp_dir / "settings.yaml"
        settings_path.write_text(
            "\n".join(
                [
                    "symbol: '1306'",
                    "mode: paper",
                    "strategy: trend",
                    "quantity: 100",
                    "data_source: dummy",
                ]
            ),
            encoding="utf-8",
        )

        settings = load_settings(
            settings_path=settings_path,
            environ={
                "TRADING_MODE": "paper",
                "TRADING_SYMBOL": "7203",
                "TRADING_QUANTITY": "200",
                "TRADING_STRATEGY": "range",
                "TRADING_DATA_SOURCE": "yfinance",
                "RANGE_RSI_PERIOD": "9",
                "RANGE_RSI_LOWER": "20",
                "RANGE_RSI_UPPER": "80",
            },
        )

        assert settings.symbol == "7203"
        assert settings.quantity == 200
        assert settings.strategy == "range"
        assert settings.data_source == "yfinance"
        assert settings.rsi_period == 9
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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
                    "strategy: range",
                    "quantity: 100",
                    "data_source: dummy",
                    "rsi_period: 14",
                    "rsi_lower: 30",
                    "rsi_upper: 70",
                    f"log_path: '{log_file_path.as_posix()}'",
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
        assert rows[1][2] == "sell"
        assert rows[1][7] == "range"
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
                    "data_source: dummy",
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


def test_main_returns_one_when_live_mode_is_requested() -> None:
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
                    "data_source: dummy",
                    "api_exchange: 1",
                    "api_token: token",
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
                data_source="dummy",
                log_path=str(log_file_path),
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
            data_source="dummy",
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


def test_create_system_logger_writes_file() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        log_path = temp_dir / "logs" / "system" / "runner.log"
        logger = create_system_logger(log_path=log_path)

        logger.info("system log test")

        assert log_path.exists()
        assert "system log test" in log_path.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_create_strategy_returns_trend_strategy_instance() -> None:
    strategy = create_strategy(
        AppSettings(
            strategy="trend",
            trend_short_window=3,
            trend_long_window=10,
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
            rsi_period=10,
            rsi_lower=25.0,
            rsi_upper=75.0,
        )
    )

    assert isinstance(strategy, RangeStrategy)
    assert strategy.rsi_period == 10
    assert strategy.lower_threshold == 25.0
    assert strategy.upper_threshold == 75.0


def test_create_data_provider_returns_dummy_provider() -> None:
    provider = create_data_provider(
        AppSettings(
            data_source="dummy",
        )
    )

    assert isinstance(provider, DummyMarketDataProvider)


def test_create_data_provider_returns_csv_provider() -> None:
    provider = create_data_provider(
        AppSettings(
            data_source="csv",
            csv_path="sample.csv",
        )
    )

    assert isinstance(provider, CsvMarketDataProvider)
    assert provider.csv_path == Path("sample.csv")


def test_create_data_provider_returns_yfinance_provider() -> None:
    provider = create_data_provider(
        AppSettings(
            data_source="yfinance",
            yfinance_period="1d",
            yfinance_interval="1m",
        )
    )

    assert isinstance(provider, YFinanceMarketDataProvider)
    assert provider.period == "1d"
    assert provider.interval == "1m"


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
                data_source="dummy",
                rsi_period=10,
                rsi_lower=25.0,
                rsi_upper=75.0,
            ),
            system_logger=logger,
            market_time_checker=AlwaysOpenMarketTimeChecker(),
        )
    finally:
        logger.removeHandler(handler)

    logged_text = log_stream.getvalue()
    assert "strategy: range" in logged_text
    assert "data_source: dummy" in logged_text
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
                data_source="dummy",
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
                data_source="dummy",
            ),
            market_time_checker=AlwaysOpenMarketTimeChecker(),
        )


def test_build_engine_raises_for_unsupported_data_source() -> None:
    with pytest.raises(ValueError, match="unsupported data_source: invalid"):
        build_engine(
            settings=AppSettings(
                symbol="1306",
                mode="paper",
                strategy="trend",
                quantity=100,
                data_source="invalid",
            ),
            market_time_checker=AlwaysOpenMarketTimeChecker(),
        )

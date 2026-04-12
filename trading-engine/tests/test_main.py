from __future__ import annotations

from datetime import datetime
from pathlib import Path
import csv
import shutil
import sys
from uuid import uuid4

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import app.main as main_module
from app.main import (
    DEFAULT_TRADE_LOG_PATH,
    AppSettings,
    build_engine,
    create_strategy,
    create_system_logger,
    load_settings,
    main,
)
from app.strategies.signal import BaseSignal, SignalType


class DummyBuySignal(BaseSignal):
    pass


class DummyBuyStrategy:
    def generate_signal(self, market_data: object) -> DummyBuySignal:
        """
        テスト用に常に buy シグナルを返す。
        引数:
            market_data: Engine から渡される市場データ

        戻り値:
            DummyBuySignal: buy シグナル
        """
        return DummyBuySignal(signal=SignalType.BUY)


class AlwaysOpenMarketTimeChecker:
    def is_open(self, current_datetime: datetime | None = None) -> bool:
        """
        テスト用に常に市場時間内として扱う。
        引数:
            current_datetime: 判定対象の日時

        戻り値:
            bool: 常に True
        """
        return True


def _create_workspace_temp_dir() -> Path:
    """
    ワークスペース配下にテスト用の一時ディレクトリを作成する。
    引数:
        なし

    戻り値:
        Path: 作成した一時ディレクトリ
    """
    temp_dir = Path(".tmp") / f"main-test-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    return temp_dir


def test_load_settings_returns_defaults_when_file_does_not_exist() -> None:
    settings = load_settings(settings_path=Path(".tmp/not-found-settings.yaml"))

    assert settings == AppSettings()


def test_main_runs_paper_mode_and_creates_trade_log_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        monkeypatch.setattr(main_module, "create_strategy", lambda strategy_name: DummyBuyStrategy())

        settings_path = temp_dir / "settings.yaml"
        log_file_path = temp_dir / "logs" / "trades" / "trade_log.csv"
        settings_path.write_text(
            "\n".join(
                [
                    "symbol: '7203'",
                    "mode: paper",
                    "strategy: trend",
                    f"log_path: '{log_file_path.as_posix()}'",
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


def test_build_engine_uses_log_path_from_settings() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        log_file_path = temp_dir / "custom" / "trade_log.csv"
        engine = build_engine(
            settings=AppSettings(
                symbol="1306",
                mode="paper",
                strategy="trend",
                log_path=str(log_file_path),
                quantity=100,
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
            log_path=None,
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


def test_create_strategy_returns_strategy_protocol_compatible_instance() -> None:
    strategy = create_strategy("trend")

    assert hasattr(strategy, "generate_signal")


def test_build_engine_raises_for_unsupported_mode() -> None:
    with pytest.raises(ValueError, match="unsupported mode: invalid"):
        build_engine(
            settings=AppSettings(
                symbol="1306",
                mode="invalid",
                strategy="trend",
                log_path=None,
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
                log_path=None,
                quantity=100,
            ),
            market_time_checker=AlwaysOpenMarketTimeChecker(),
        )

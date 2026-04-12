from __future__ import annotations

from datetime import datetime
from pathlib import Path
import csv
import shutil
import sys
from uuid import uuid4


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.logging.trade_logger import TRADE_LOG_HEADERS, TradeLog, TradeLogger


def _create_workspace_temp_dir() -> Path:
    """
    ワークスペース配下にテスト用の一時ディレクトリを作成する。
    引数:
        なし

    戻り値:
        Path: 作成した一時ディレクトリのパス
    """
    temp_dir = Path(".tmp") / f"trade-logger-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    return temp_dir


def test_trade_logger_creates_csv_with_header() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        log_file_path = temp_dir / "logs" / "trades" / "trade_log.csv"
        trade_logger = TradeLogger(log_file_path=log_file_path)
        trade_logger.log(
            TradeLog(
                timestamp=datetime(2026, 4, 12, 9, 0, 0),
                symbol="7203",
                side="buy",
                price=2500.0,
                quantity=100.0,
                success=True,
                message="paper execution simulated",
                strategy="trend",
            )
        )

        with log_file_path.open("r", encoding="utf-8", newline="") as csv_file:
            rows = list(csv.reader(csv_file))

        assert rows[0] == list(TRADE_LOG_HEADERS)
        assert rows[1][1] == "7203"
        assert rows[1][2] == "buy"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_trade_logger_appends_rows() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        log_file_path = temp_dir / "logs" / "trades" / "trade_log.csv"
        trade_logger = TradeLogger(log_file_path=log_file_path)

        trade_logger.log(
            TradeLog(
                timestamp=datetime(2026, 4, 12, 9, 0, 0),
                symbol="7203",
                side="buy",
                price=2500.0,
                quantity=100.0,
                success=True,
                message="first",
                strategy="trend",
            )
        )
        trade_logger.log(
            TradeLog(
                timestamp=datetime(2026, 4, 12, 9, 1, 0),
                symbol="6758",
                side="sell",
                price=3200.0,
                quantity=200.0,
                success=True,
                message="second",
                strategy="range",
            )
        )

        with log_file_path.open("r", encoding="utf-8", newline="") as csv_file:
            rows = list(csv.reader(csv_file))

        assert len(rows) == 3
        assert rows[1][1] == "7203"
        assert rows[2][1] == "6758"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_trade_logger_writes_expected_values() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        log_file_path = temp_dir / "logs" / "trades" / "trade_log.csv"
        trade_logger = TradeLogger(log_file_path=log_file_path)
        trade_logger.log(
            TradeLog(
                timestamp=datetime(2026, 4, 12, 9, 5, 0),
                symbol="8306",
                side="sell",
                price=1800.5,
                quantity=50.0,
                success=False,
                message="rejected",
                strategy=None,
            )
        )

        with log_file_path.open("r", encoding="utf-8", newline="") as csv_file:
            rows = list(csv.reader(csv_file))

        assert rows[1] == [
            "2026-04-12T09:05:00",
            "8306",
            "sell",
            "1800.5",
            "50.0",
            "False",
            "rejected",
            "",
        ]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_trade_logger_writes_empty_message_when_message_is_none() -> None:
    temp_dir = _create_workspace_temp_dir()

    try:
        log_file_path = temp_dir / "logs" / "trades" / "trade_log.csv"
        trade_logger = TradeLogger(log_file_path=log_file_path)
        trade_logger.log(
            TradeLog(
                timestamp=datetime(2026, 4, 12, 9, 10, 0),
                symbol="7203",
                side="buy",
                price=2500.0,
                quantity=100.0,
                success=True,
                message=None,
                strategy="trend",
            )
        )

        with log_file_path.open("r", encoding="utf-8", newline="") as csv_file:
            rows = list(csv.reader(csv_file))

        assert rows[1][6] == ""
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

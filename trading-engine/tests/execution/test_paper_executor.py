from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.execution.paper_executor import PaperExecutor, PaperOrder


def test_execute_returns_filled_paper_result(caplog: pytest.LogCaptureFixture) -> None:
    executor = PaperExecutor()
    order = PaperOrder(
        symbol="7203",
        strategy="trend",
        side="buy",
        quantity=100,
        price=2500.0,
    )

    with caplog.at_level("INFO"):
        result = executor.execute(order=order)

    assert result.status == "filled"
    assert result.symbol == "7203"
    assert result.strategy == "trend"
    assert result.side == "buy"
    assert result.quantity == 100
    assert result.price == 2500.0
    assert result.order_id.startswith("paper-")
    assert "paper execution filled" in caplog.text


def test_to_log_record_returns_dictionary() -> None:
    executor = PaperExecutor()
    order = PaperOrder(
        symbol="6758",
        strategy="range",
        side="sell",
        quantity=200,
        price=3200.0,
    )

    result = executor.execute(order=order)
    log_record = executor.to_log_record(execution_result=result)

    assert log_record["symbol"] == "6758"
    assert log_record["strategy"] == "range"
    assert log_record["side"] == "sell"
    assert log_record["status"] == "filled"


def test_execute_raises_when_side_is_invalid() -> None:
    executor = PaperExecutor()
    order = PaperOrder(
        symbol="7203",
        strategy="trend",
        side="hold",
        quantity=100,
        price=2500.0,
    )

    with pytest.raises(ValueError):
        executor.execute(order=order)


def test_execute_raises_when_quantity_is_invalid() -> None:
    executor = PaperExecutor()
    order = PaperOrder(
        symbol="7203",
        strategy="trend",
        side="buy",
        quantity=0,
        price=2500.0,
    )

    with pytest.raises(ValueError):
        executor.execute(order=order)

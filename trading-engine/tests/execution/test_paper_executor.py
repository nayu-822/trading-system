from __future__ import annotations

from pathlib import Path
import sys

import pytest


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.execution.models import Order
from app.execution.paper_executor import PaperExecutor


def test_execute_returns_successful_paper_result() -> None:
    executor = PaperExecutor()
    order = Order(
        symbol="7203",
        side="buy",
        quantity=100,
        price=2500.0,
    )

    result = executor.execute(order=order)

    assert result.success is True
    assert result.executed_price == 2500.0
    assert result.quantity == 100.0
    assert result.message == "paper execution simulated"


def test_execute_raises_when_price_is_missing() -> None:
    executor = PaperExecutor()
    order = Order(
        symbol="6758",
        side="sell",
        quantity=200,
        price=None,
    )

    with pytest.raises(ValueError, match="price is required for paper execution"):
        executor.execute(order=order)


def test_execute_raises_when_side_is_invalid() -> None:
    executor = PaperExecutor()
    order = Order(
        symbol="7203",
        side="hold",  # type: ignore[arg-type]
        quantity=100,
        price=2500.0,
    )

    with pytest.raises(ValueError, match="side must be either 'buy' or 'sell'"):
        executor.execute(order=order)


def test_execute_raises_when_quantity_is_invalid() -> None:
    executor = PaperExecutor()
    order = Order(
        symbol="7203",
        side="buy",
        quantity=0,
        price=2500.0,
    )

    with pytest.raises(ValueError, match="quantity must be greater than zero"):
        executor.execute(order=order)

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


OrderSide = Literal["buy", "sell"]


@dataclass(frozen=True)
class Order:
    """
    executor へ渡す共通の注文情報を表すモデル。
    """

    symbol: str
    side: OrderSide
    quantity: float
    price: float | None


@dataclass(frozen=True)
class ExecutionResult:
    """
    executor が返す共通の実行結果を表すモデル。
    """

    success: bool
    executed_price: float
    quantity: float
    message: str | None

from __future__ import annotations

from dataclasses import dataclass

from app.execution.models import ExecutionResult, Order


@dataclass(frozen=True)
class PaperExecutor:
    """
    実発注を行わず、注文内容を擬似約定として扱う executor。
    """

    def execute(self, order: Order) -> ExecutionResult:
        """
        注文内容を検証し、擬似約定結果を返す。
        引数:
            order: 擬似約定の対象となる注文情報

        戻り値:
            ExecutionResult: 擬似約定として返す実行結果
        """
        self._validate_order(order=order)

        if order.price is None:
            raise ValueError("price is required for paper execution")

        return ExecutionResult(
            success=True,
            executed_price=float(order.price),
            quantity=float(order.quantity),
            message="paper execution simulated",
        )

    def _validate_order(self, order: Order) -> None:
        """
        擬似約定に必要な注文情報の妥当性を検証する。
        引数:
            order: 検証対象の注文情報

        戻り値:
            なし
        """
        if not order.symbol:
            raise ValueError("symbol is required")

        if order.side not in {"buy", "sell"}:
            raise ValueError("side must be either 'buy' or 'sell'")

        if order.quantity <= 0:
            raise ValueError("quantity must be greater than zero")

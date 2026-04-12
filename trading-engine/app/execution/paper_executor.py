from __future__ import annotations

from dataclasses import dataclass

from app.domain.models import ExecutionResult, Order, OrderSide


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
        paper_order = self._to_paper_order(order=order)

        return ExecutionResult(
            success=True,
            executed_price=paper_order.price,
            quantity=paper_order.quantity,
            message="paper execution simulated",
        )

    def _to_paper_order(self, order: Order) -> PaperOrder:
        """
        共通注文モデルをペーパートレード用の注文情報へ変換する。
        引数:
            order: 共通の注文情報

        戻り値:
            PaperOrder: 擬似約定で利用する注文情報
        """
        self._validate_order(order=order)

        if order.price is None:
            raise ValueError("price is required for paper execution")

        return PaperOrder(
            symbol=order.symbol,
            side=order.side,
            quantity=float(order.quantity),
            price=float(order.price),
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


@dataclass(frozen=True)
class PaperOrder:
    """
    ペーパートレード内部で利用する注文情報を表すモデル。
    """

    symbol: str
    side: OrderSide
    quantity: float
    price: float

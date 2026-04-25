import logging
from dataclasses import dataclass, field
from datetime import datetime

from domain.enums import OrderSide, OrderStatus
from domain.models import Order, Position, TradeHistory


class OrderFillReconcileError(ValueError):
    """約定反映に失敗した場合の例外。"""


@dataclass(frozen=True)
class OrderFillReconcileResult:
    """約定反映結果。"""

    newly_reflected_quantity: int
    trade_histories: tuple[TradeHistory, ...]


@dataclass
class OrderFillReconciler:
    """注文状態同期結果を建玉と履歴へ差分反映する。"""

    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def reconcile(
        self,
        order: Order,
        position: Position,
        timestamp: datetime,
        filled_quantity: int,
        average_fill_price: float | None,
        status: OrderStatus,
    ) -> OrderFillReconcileResult:
        """同期済み約定数量の差分を建玉と履歴へ反映する。

        Args:
            order: 反映対象の注文。
            position: 更新対象の建玉。
            timestamp: 約定反映日時。
            filled_quantity: APIが返した累計約定数量。
            average_fill_price: APIが返した平均約定価格。
            status: APIが返した注文状態。
        Returns:
            反映した数量と追加した履歴。
        Raises:
            OrderFillReconcileError: 差分計算や建玉更新に失敗した場合。
        """

        if filled_quantity < order.reflected_filled_quantity:
            raise OrderFillReconcileError(
                f"filled quantity decreased order_id={order.order_id}"
            )
        if filled_quantity > order.quantity:
            raise OrderFillReconcileError(
                f"filled quantity exceeds order quantity order_id={order.order_id}"
            )
        newly_reflected_quantity = filled_quantity - order.reflected_filled_quantity
        if newly_reflected_quantity == 0:
            return OrderFillReconcileResult(
                newly_reflected_quantity=0,
                trade_histories=(),
            )
        if average_fill_price is None or average_fill_price <= 0:
            raise OrderFillReconcileError(
                f"average fill price is invalid order_id={order.order_id}"
            )

        self._apply_position_delta(
            position=position,
            order=order,
            filled_quantity=newly_reflected_quantity,
            average_fill_price=average_fill_price,
        )
        order.reflected_filled_quantity = filled_quantity
        history = TradeHistory(
            order_id=order.external_order_id or order.order_id,
            symbol=order.symbol,
            side=order.side,
            is_exit=order.is_exit,
            filled_quantity=newly_reflected_quantity,
            fill_price=average_fill_price,
            average_fill_price=average_fill_price,
            filled_at=timestamp,
            status=status,
            source="api_order_sync",
        )
        self.logger.info(
            "order fill reflected order_id=%s symbol=%s side=%s is_exit=%s order_quantity=%s filled_quantity=%s newly_reflected_quantity=%s average_fill_price=%s status=%s",
            order.external_order_id or order.order_id,
            order.symbol,
            order.side.value,
            order.is_exit,
            order.quantity,
            filled_quantity,
            newly_reflected_quantity,
            average_fill_price,
            status.value,
        )
        return OrderFillReconcileResult(
            newly_reflected_quantity=newly_reflected_quantity,
            trade_histories=(history,),
        )

    def _apply_position_delta(
        self,
        position: Position,
        order: Order,
        filled_quantity: int,
        average_fill_price: float,
    ) -> None:
        """差分約定を建玉へ反映する。

        Args:
            position: 更新対象の建玉。
            order: 反映対象の注文。
            filled_quantity: 今回新たに反映する約定数量。
            average_fill_price: 平均約定価格。
        Returns:
            なし。
        Raises:
            OrderFillReconcileError: 建玉整合性に問題がある場合。
        """

        if order.is_exit:
            self._apply_exit_fill(
                position=position,
                order=order,
                filled_quantity=filled_quantity,
            )
            return
        self._apply_entry_fill(
            position=position,
            order=order,
            filled_quantity=filled_quantity,
            average_fill_price=average_fill_price,
        )

    def _apply_entry_fill(
        self,
        position: Position,
        order: Order,
        filled_quantity: int,
        average_fill_price: float,
    ) -> None:
        """新規約定を建玉へ反映する。"""

        signed_quantity = (
            filled_quantity if order.side == OrderSide.BUY else -filled_quantity
        )
        before_quantity = position.quantity
        after_quantity = before_quantity + signed_quantity
        if after_quantity == 0:
            position.average_price = 0.0
        elif before_quantity == 0 or self._is_same_direction(
            before_quantity, signed_quantity
        ):
            total_cost = abs(before_quantity) * position.average_price
            total_cost += filled_quantity * average_fill_price
            position.average_price = total_cost / abs(after_quantity)
        elif self._is_reversed(before_quantity, after_quantity):
            position.average_price = average_fill_price
        position.quantity = after_quantity

    def _apply_exit_fill(
        self,
        position: Position,
        order: Order,
        filled_quantity: int,
    ) -> None:
        """返済約定を建玉へ反映する。"""

        if position.quantity == 0:
            raise OrderFillReconcileError(
                f"exit fill requires current position order_id={order.order_id}"
            )
        if order.side == OrderSide.SELL and position.quantity <= 0:
            raise OrderFillReconcileError(
                f"exit sell requires long position order_id={order.order_id}"
            )
        if order.side == OrderSide.BUY and position.quantity >= 0:
            raise OrderFillReconcileError(
                f"exit buy requires short position order_id={order.order_id}"
            )
        if filled_quantity > abs(position.quantity):
            raise OrderFillReconcileError(
                f"exit fill exceeds position quantity order_id={order.order_id}"
            )
        if order.side == OrderSide.SELL:
            position.quantity -= filled_quantity
        else:
            position.quantity += filled_quantity
        if position.quantity == 0:
            position.average_price = 0.0

    def _is_same_direction(self, current_quantity: int, add_quantity: int) -> bool:
        """数量の符号が同方向かを判定する。

        Args:
            current_quantity: 現在建玉数量。
            add_quantity: 加算する数量。
        Returns:
            同方向の場合は True。
        """

        return (current_quantity > 0 and add_quantity > 0) or (
            current_quantity < 0 and add_quantity < 0
        )

    def _is_reversed(self, before_quantity: int, after_quantity: int) -> bool:
        """建玉方向が反転したかを判定する。

        Args:
            before_quantity: 反映前の建玉数量。
            after_quantity: 反映後の建玉数量。
        Returns:
            建玉方向が反転した場合は True。
        """

        return (before_quantity > 0 and after_quantity < 0) or (
            before_quantity < 0 and after_quantity > 0
        )

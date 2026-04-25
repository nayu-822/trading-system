import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field

from domain.enums import KabuApiEnvironment, OrderSide, OrderStatus, TradingMode
from domain.models import Order, Position

INCOMPLETE_ORDER_STATUSES = {
    OrderStatus.NEW,
    OrderStatus.REQUESTED,
    OrderStatus.PARTIALLY_FILLED,
}


class OrderSafetyError(ValueError):
    """注文前チェックで安全条件を満たさない場合の例外。"""


@dataclass(frozen=True)
class OrderSafetyState:
    """注文前チェックに必要な現在状態。"""

    position: Position | None
    open_orders: tuple[Order, ...] = ()


StateProvider = Callable[[str], OrderSafetyState]


@dataclass(frozen=True)
class OrderSafetyDecision:
    """注文前チェック結果。"""

    allowed: bool
    reason: str | None = None


def _default_state_provider(symbol: str) -> OrderSafetyState:
    return OrderSafetyState(position=Position(symbol=symbol))


@dataclass
class OrderSafetyValidator:
    """実注文前の安全チェックを共通化する。"""

    trading_mode: TradingMode
    kabu_api_environment: KabuApiEnvironment
    max_order_quantity: int
    trade_symbols: tuple[str, ...]
    enabled_symbols: tuple[str, ...]
    state_provider: StateProvider = _default_state_provider
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def validate_order(self, order: Order) -> None:
        """注文前チェックを実行し、NGなら例外を送出する。"""

        decision = self.evaluate_order(order)
        self._log_decision(order, decision)
        if not decision.allowed:
            raise OrderSafetyError(decision.reason or "order is not allowed")

    def evaluate_order(self, order: Order) -> OrderSafetyDecision:
        """注文前チェックの判定結果を返す。"""

        if self.trading_mode != TradingMode.LIVE:
            return OrderSafetyDecision(False, "trading_mode is not live")
        if self.kabu_api_environment != KabuApiEnvironment.LIVE:
            return OrderSafetyDecision(False, "kabu_api_environment is not live")
        if order.symbol not in self.trade_symbols:
            return OrderSafetyDecision(False, "symbol is not in trade_symbols")
        if order.symbol not in self.enabled_symbols:
            return OrderSafetyDecision(False, "symbol is not enabled")
        if order.quantity < 1:
            return OrderSafetyDecision(False, "quantity must be >= 1")
        if order.quantity > self.max_order_quantity:
            return OrderSafetyDecision(False, "quantity exceeds max_order_quantity")
        if order.price is not None and (
            not math.isfinite(order.price) or order.price <= 0
        ):
            return OrderSafetyDecision(False, "price is invalid")
        if order.order_type.upper() != "MARKET":
            return OrderSafetyDecision(False, "unsupported order_type")

        try:
            state = self.state_provider(order.symbol)
        except Exception as error:
            return OrderSafetyDecision(False, f"position state is unavailable: {error}")

        if state.position is None:
            return OrderSafetyDecision(False, "position state is unavailable")

        if self._has_incomplete_order(order, state.open_orders):
            return OrderSafetyDecision(False, "incomplete order exists")

        position_quantity = state.position.quantity
        if order.is_exit:
            if position_quantity == 0:
                return OrderSafetyDecision(False, "exit target position does not exist")
            if order.side == OrderSide.SELL and position_quantity <= 0:
                return OrderSafetyDecision(
                    False, "sell exit requires long position"
                )
            if order.side == OrderSide.BUY and position_quantity >= 0:
                return OrderSafetyDecision(
                    False, "buy exit requires short position"
                )
        if position_quantity > 0 and order.side == OrderSide.BUY:
            return OrderSafetyDecision(False, "long position already exists")
        if position_quantity < 0 and order.side == OrderSide.SELL:
            return OrderSafetyDecision(False, "short position already exists")
        if (
            position_quantity > 0
            and order.side == OrderSide.SELL
            and order.quantity > abs(position_quantity)
        ):
            return OrderSafetyDecision(False, "close quantity exceeds long position")
        if (
            position_quantity < 0
            and order.side == OrderSide.BUY
            and order.quantity > abs(position_quantity)
        ):
            return OrderSafetyDecision(False, "close quantity exceeds short position")

        return OrderSafetyDecision(True)

    def _has_incomplete_order(
        self,
        target_order: Order,
        open_orders: tuple[Order, ...],
    ) -> bool:
        for order in open_orders:
            if order.order_id == target_order.order_id:
                continue
            if order.symbol != target_order.symbol:
                continue
            if order.status not in INCOMPLETE_ORDER_STATUSES:
                continue
            return True
        return False

    def _log_decision(
        self,
        order: Order,
        decision: OrderSafetyDecision,
    ) -> None:
        log_method = self.logger.info if decision.allowed else self.logger.warning
        log_method(
            "order safety check trading_mode=%s kabu_api_environment=%s symbol=%s side=%s quantity=%s price=%s result=%s reason=%s",
            self.trading_mode.value,
            self.kabu_api_environment.value,
            order.symbol,
            order.side.value,
            order.quantity,
            order.price,
            "allowed" if decision.allowed else "rejected",
            decision.reason or "",
        )

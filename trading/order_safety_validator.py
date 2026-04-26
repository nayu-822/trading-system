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
    raise OrderSafetyError(f"position state provider is not configured: {symbol}")


@dataclass
class OrderSafetyValidator:
    """注文前チェックの安全条件を評価する。"""

    trading_mode: TradingMode
    kabu_api_environment: KabuApiEnvironment
    max_order_quantity: int
    trade_symbols: tuple[str, ...]
    enabled_symbols: tuple[str, ...]
    allow_api_paper_orders: bool = False
    state_provider: StateProvider = _default_state_provider
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def validate_order(self, order: Order) -> None:
        """注文前チェックを実行し、NGなら例外を送出する。

        Args:
            order: 判定対象の注文。

        Returns:
            None

        Raises:
            OrderSafetyError: 注文前チェックで安全条件を満たさない場合。
        """

        decision = self.evaluate_order(order)
        self._log_decision(order, decision)
        if not decision.allowed:
            raise OrderSafetyError(decision.reason or "order is not allowed")

    def evaluate_order(self, order: Order) -> OrderSafetyDecision:
        """注文前チェックの判定結果を返す。

        Args:
            order: 判定対象の注文。

        Returns:
            OrderSafetyDecision: 注文可否と理由を含む判定結果。
        """

        if self.allow_api_paper_orders:
            if self.kabu_api_environment != KabuApiEnvironment.PAPER:
                return OrderSafetyDecision(False, "kabu_api_environment is not paper")
        else:
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
        if position_quantity == 0:
            if order.is_exit:
                return OrderSafetyDecision(False, "exit target position does not exist")
            return OrderSafetyDecision(True)

        if position_quantity > 0:
            if order.is_exit:
                if order.side != OrderSide.SELL:
                    return OrderSafetyDecision(
                        False,
                        "long position exit requires sell order",
                    )
                if order.quantity > abs(position_quantity):
                    return OrderSafetyDecision(
                        False,
                        "close quantity exceeds long position",
                    )
                return OrderSafetyDecision(True)
            if order.side == OrderSide.BUY:
                return OrderSafetyDecision(False, "long position already exists")
            return OrderSafetyDecision(
                False,
                "non-exit sell cannot reduce long position",
            )

        if order.is_exit:
            if order.side != OrderSide.BUY:
                return OrderSafetyDecision(
                    False,
                    "short position exit requires buy order",
                )
            if order.quantity > abs(position_quantity):
                return OrderSafetyDecision(
                    False,
                    "close quantity exceeds short position",
                )
            return OrderSafetyDecision(True)

        if order.side == OrderSide.SELL:
            return OrderSafetyDecision(False, "short position already exists")
        return OrderSafetyDecision(False, "non-exit buy cannot reduce short position")

    def _has_incomplete_order(
        self,
        target_order: Order,
        open_orders: tuple[Order, ...],
    ) -> bool:
        """未完了注文が存在するかを判定する。

        Args:
            target_order: 判定対象の注文。
            open_orders: 現在保持している未完了注文一覧。

        Returns:
            bool: 対象注文以外に未完了注文が存在する場合は True。
        """

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

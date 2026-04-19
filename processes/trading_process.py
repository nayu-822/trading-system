import logging
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from domain.enums import EventSource, EventType, OrderSide, OrderStatus, SignalType
from domain.events import (
    BaseEvent,
    EventFactory,
    OrderRequestedPayload,
    OrderStatusPayload,
    OrderStatusUpdated,
    SignalDetected,
)
from domain.models import Order, Position, TradingSymbolConfig, TradingSymbolState
from domain.snapshots import (
    OrderSnapshot,
    PositionStateSnapshot,
    TradingStateSnapshot,
    TradingSymbolStateSnapshot,
)
from infrastructure.event_bus import EventBus


@dataclass
class TradingProcess:
    """シグナルから発注可否と売買状態を管理する。"""

    event_bus: EventBus
    order_quantity: int = 100
    order_type: str = "MARKET"
    lot_configs: tuple[TradingSymbolConfig, ...] = ()
    auto_fill_orders: bool = True
    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.TRADING)
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    states: list[TradingSymbolState] = field(default_factory=list)

    def start(self) -> None:
        """SignalDetected と OrderStatusUpdated の購読を開始する。"""

        self.event_bus.subscribe(EventType.SIGNAL_DETECTED, self.handle_signal)
        self.event_bus.subscribe(
            EventType.ORDER_STATUS_UPDATED,
            self.handle_order_status,
        )

    def handle_signal(self, event: BaseEvent) -> None:
        """シグナルを受けて発注要求を生成する。"""

        if not isinstance(event, SignalDetected):
            return
        if event.symbol is None:
            self.logger.warning("signal ignored because symbol is empty")
            return

        state = self.get_state(event.symbol)
        if self._has_open_order(state):
            self.logger.warning(
                "signal ignored because open order exists symbol=%s", event.symbol
            )
            return

        order_side = self._resolve_order_side(
            signal_type=event.payload.signal_type,
            position=self._ensure_position(state),
        )
        if order_side is None:
            self.logger.info(
                "signal ignored signal_type=%s symbol=%s",
                event.payload.signal_type.value,
                event.symbol,
            )
            return
        if self._has_same_direction_position(
            position=self._ensure_position(state),
            side=order_side,
            signal_type=event.payload.signal_type,
        ):
            self.logger.info(
                "signal ignored because same direction position exists symbol=%s",
                event.symbol,
            )
            return

        quantity = self._resolve_order_quantity(
            signal_type=event.payload.signal_type,
            position=self._ensure_position(state),
            lot_size=state.lot_size,
        )
        order = Order(
            order_id=str(uuid4()),
            symbol=event.symbol,
            side=order_side,
            quantity=quantity,
            order_type=self.order_type,
            status=OrderStatus.REQUESTED,
            remaining_quantity=quantity,
        )
        state.orders.append(order)
        order_event = self.event_factory.create(
            event_type=EventType.ORDER_REQUESTED,
            timestamp=event.timestamp,
            symbol=event.symbol,
            payload=OrderRequestedPayload(
                symbol=event.symbol,
                side=order.side,
                quantity=order.quantity,
                order_type=order.order_type,
                price=order.price,
            ),
        )
        self.event_bus.publish(order_event)
        self.logger.info(
            "order requested order_id=%s symbol=%s side=%s quantity=%s",
            order.order_id,
            order.symbol,
            order.side.value,
            order.quantity,
        )
        if self.auto_fill_orders:
            self._publish_filled_order(order=order, timestamp=event.timestamp)

    def handle_order_status(self, event: BaseEvent) -> None:
        """注文状態イベントを受けて注文と建玉を更新する。"""

        if not isinstance(event, OrderStatusUpdated):
            return

        order = self._find_order(event.payload.order_id)
        if order is None:
            self.logger.warning(
                "order status ignored because order is unknown order_id=%s",
                event.payload.order_id,
            )
            return

        state = self.get_state(order.symbol)
        order.status = event.payload.status
        order.filled_quantity = event.payload.filled_quantity
        order.remaining_quantity = event.payload.remaining_quantity
        order.avg_price = event.payload.avg_price
        if event.payload.status == OrderStatus.FILLED:
            self._update_position(
                position=self._ensure_position(state),
                order=order,
                filled_quantity=event.payload.filled_quantity,
                avg_price=event.payload.avg_price or 0.0,
            )
        self.logger.info(
            "order status updated order_id=%s status=%s filled_quantity=%s remaining_quantity=%s",
            order.order_id,
            order.status.value,
            order.filled_quantity,
            order.remaining_quantity,
        )

    def get_state(self, symbol: str) -> TradingSymbolState:
        """銘柄別の売買状態を取得し、なければ初期化する。"""

        for state in self.states:
            if state.symbol == symbol:
                return state
        state = TradingSymbolState(
            symbol=symbol,
            lot_size=self._resolve_lot_size(symbol),
            position=Position(symbol=symbol),
        )
        self.states.append(state)
        return state

    def get_snapshot(self, timestamp: datetime) -> TradingStateSnapshot:
        """現在の売買状態を復旧用スナップショットへ変換する。"""

        return TradingStateSnapshot(
            version=1,
            created_at=timestamp,
            updated_at=timestamp,
            sequence_no=self.event_factory.sequence_no,
            symbols=tuple(self._state_to_snapshot(state) for state in self.states),
        )

    def restore_snapshot(self, snapshot: TradingStateSnapshot) -> None:
        """復旧用スナップショットから内部状態を復元する。"""

        self.states = [
            TradingSymbolState(
                symbol=symbol_state.symbol,
                lot_size=symbol_state.lot_size,
                position=Position(
                    symbol=symbol_state.position.symbol,
                    quantity=symbol_state.position.quantity,
                    average_price=symbol_state.position.average_price,
                ),
                orders=[
                    Order(
                        order_id=order.order_id,
                        symbol=order.symbol,
                        side=order.side,
                        quantity=order.quantity,
                        order_type=order.order_type,
                        status=order.status,
                        price=order.price,
                        filled_quantity=order.filled_quantity,
                        remaining_quantity=order.remaining_quantity,
                        avg_price=order.avg_price,
                    )
                    for order in symbol_state.orders
                ],
            )
            for symbol_state in snapshot.symbols
        ]

    def _resolve_lot_size(self, symbol: str) -> int:
        for lot_config in self.lot_configs:
            if lot_config.symbol == symbol:
                return lot_config.lot_size
        return self.order_quantity

    def _state_to_snapshot(
        self,
        state: TradingSymbolState,
    ) -> TradingSymbolStateSnapshot:
        position = self._ensure_position(state)
        return TradingSymbolStateSnapshot(
            symbol=state.symbol,
            lot_size=state.lot_size,
            position=PositionStateSnapshot(
                symbol=position.symbol,
                quantity=position.quantity,
                average_price=position.average_price,
            ),
            orders=tuple(
                OrderSnapshot(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    order_type=order.order_type,
                    status=order.status,
                    price=order.price,
                    filled_quantity=order.filled_quantity,
                    remaining_quantity=order.remaining_quantity,
                    avg_price=order.avg_price,
                )
                for order in state.orders
            ),
        )

    def _has_open_order(self, state: TradingSymbolState) -> bool:
        incomplete_statuses = {
            OrderStatus.NEW,
            OrderStatus.REQUESTED,
            OrderStatus.PARTIALLY_FILLED,
        }
        return any(order.status in incomplete_statuses for order in state.orders)

    def _resolve_order_side(
        self,
        signal_type: SignalType,
        position: Position,
    ) -> OrderSide | None:
        if signal_type == SignalType.BUY:
            return OrderSide.BUY
        if signal_type == SignalType.SELL:
            return OrderSide.SELL
        if signal_type == SignalType.EXIT and position.quantity > 0:
            return OrderSide.SELL
        if signal_type == SignalType.EXIT and position.quantity < 0:
            return OrderSide.BUY
        return None

    def _has_same_direction_position(
        self,
        position: Position,
        side: OrderSide,
        signal_type: SignalType,
    ) -> bool:
        if signal_type == SignalType.EXIT:
            return False
        return (side == OrderSide.BUY and position.quantity > 0) or (
            side == OrderSide.SELL and position.quantity < 0
        )

    def _resolve_order_quantity(
        self,
        signal_type: SignalType,
        position: Position,
        lot_size: int,
    ) -> int:
        if signal_type == SignalType.EXIT:
            return abs(position.quantity)
        return lot_size

    def _publish_filled_order(self, order: Order, timestamp: datetime) -> None:
        status_event = self.event_factory.create(
            event_type=EventType.ORDER_STATUS_UPDATED,
            timestamp=timestamp,
            symbol=order.symbol,
            payload=OrderStatusPayload(
                order_id=order.order_id,
                status=OrderStatus.FILLED,
                filled_quantity=order.quantity,
                remaining_quantity=0,
                avg_price=order.price or 0.0,
            ),
        )
        self.event_bus.publish(status_event)

    def _find_order(self, order_id: str) -> Order | None:
        for state in self.states:
            for order in state.orders:
                if order.order_id == order_id:
                    return order
        return None

    def _ensure_position(self, state: TradingSymbolState) -> Position:
        if state.position is None:
            state.position = Position(symbol=state.symbol)
        return state.position

    def _update_position(
        self,
        position: Position,
        order: Order,
        filled_quantity: int,
        avg_price: float,
    ) -> None:
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
            total_cost += filled_quantity * avg_price
            position.average_price = total_cost / abs(after_quantity)
        elif self._is_reversed(before_quantity, after_quantity):
            position.average_price = avg_price
        position.quantity = after_quantity

    def _is_same_direction(self, current_quantity: int, add_quantity: int) -> bool:
        return (current_quantity > 0 and add_quantity > 0) or (
            current_quantity < 0 and add_quantity < 0
        )

    def _is_reversed(self, before_quantity: int, after_quantity: int) -> bool:
        return (before_quantity > 0 and after_quantity < 0) or (
            before_quantity < 0 and after_quantity > 0
        )

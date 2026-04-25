import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from domain.enums import EventSource, EventType, OrderSide, OrderStatus, SignalType
from domain.events import (
    BaseEvent,
    EventFactory,
    MarketDataUpdated,
    OrderRequestedPayload,
    OrderStatusUpdated,
    PositionPayload,
    SignalDetected,
)
from domain.models import (
    AccountState,
    Order,
    Position,
    RiskConfig,
    RiskControlState,
    RiskSymbolConfig,
    TradeHistory,
    TradeResult,
    TradingSymbolConfig,
    TradingSymbolState,
)
from domain.snapshots import (
    OrderSnapshot,
    PositionStateSnapshot,
    RiskControlSnapshot,
    TradeHistorySnapshot,
    TradingStateSnapshot,
    TradingSymbolStateSnapshot,
)
from infrastructure.event_bus import EventBus
from trading.mock_order_gateway import MockOrderGateway
from trading.order_fill_reconciler import OrderFillReconcileError, OrderFillReconciler
from trading.order_gateway import OrderGateway
from trading.risk_manager import RiskManager

ORDER_STATUS_PRIORITY = {
    OrderStatus.REQUESTED: 0,
    OrderStatus.NEW: 1,
    OrderStatus.PARTIALLY_FILLED: 2,
    OrderStatus.FILLED: 3,
    OrderStatus.CANCELED: 3,
    OrderStatus.EXPIRED: 3,
    OrderStatus.FAILED: 3,
    OrderStatus.REJECTED: 3,
}

TERMINAL_ORDER_STATUSES = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.EXPIRED,
    OrderStatus.FAILED,
    OrderStatus.REJECTED,
}


@dataclass
class TradingProcess:
    """シグナルから発注可否と売買状態を管理する。"""

    event_bus: EventBus
    order_quantity: int = 100
    order_type: str = "MARKET"
    lot_configs: tuple[TradingSymbolConfig, ...] = ()
    auto_fill_orders: bool = True
    order_gateway: OrderGateway | None = None
    risk_manager: RiskManager | None = None
    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.TRADING)
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    states: list[TradingSymbolState] = field(default_factory=list)
    latest_prices: list[tuple[str, float]] = field(default_factory=list)
    order_status_syncer: Callable[[], int] | None = None
    order_fill_reconciler: OrderFillReconciler = field(
        default_factory=OrderFillReconciler
    )
    _subscribed: bool = False

    def __post_init__(self) -> None:
        if self.order_gateway is None:
            self.order_gateway = MockOrderGateway(event_factory=self.event_factory)
        if self.risk_manager is None:
            self.risk_manager = _default_risk_manager(
                lot_configs=self.lot_configs,
                order_quantity=self.order_quantity,
            )

    def start(self) -> None:
        """SignalDetected と OrderStatusUpdated の購読を開始する。"""

        if self._subscribed:
            return
        self.event_bus.subscribe(EventType.MARKET_DATA_UPDATED, self.handle_market_data)
        self.event_bus.subscribe(EventType.SIGNAL_DETECTED, self.handle_signal)
        self.event_bus.subscribe(
            EventType.ORDER_STATUS_UPDATED,
            self.handle_order_status,
        )
        self._subscribed = True

    def stop(self) -> None:
        """SignalDetected と OrderStatusUpdated の購読を解除する。"""

        if not self._subscribed:
            return
        self.event_bus.unsubscribe(
            EventType.MARKET_DATA_UPDATED, self.handle_market_data
        )
        self.event_bus.unsubscribe(EventType.SIGNAL_DETECTED, self.handle_signal)
        self.event_bus.unsubscribe(
            EventType.ORDER_STATUS_UPDATED,
            self.handle_order_status,
        )
        self._subscribed = False

    def handle_market_data(self, event: BaseEvent) -> None:
        """市場価格をロット計算の基準価格として保持する。"""

        if not isinstance(event, MarketDataUpdated) or event.symbol is None:
            return
        self._set_latest_price(symbol=event.symbol, price=event.payload.price)

    def handle_signal(self, event: BaseEvent) -> None:
        """シグナルを受けて発注要求を生成する。"""

        if not isinstance(event, SignalDetected):
            return
        if event.symbol is None:
            self.logger.warning("signal ignored because symbol is empty")
            return

        self._set_latest_price_from_signal(event)
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

        position = self._ensure_position(state)
        quantity = self._resolve_order_quantity(
            signal_type=event.payload.signal_type,
            position=position,
            lot_size=state.lot_size,
            symbol=event.symbol,
            side=order_side,
        )
        if quantity <= 0:
            self.logger.warning(
                "signal ignored because calculated lot is zero symbol=%s",
                event.symbol,
            )
            return
        order = Order(
            order_id=str(uuid4()),
            symbol=event.symbol,
            side=order_side,
            quantity=quantity,
            order_type=self.order_type,
            is_exit=event.payload.signal_type == SignalType.EXIT,
            status=OrderStatus.REQUESTED,
            price=self._reference_price(event.symbol),
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
            "order requested order_id=%s symbol=%s side=%s quantity=%s status=%s",
            order.order_id,
            order.symbol,
            order.side.value,
            order.quantity,
            order.status.value,
        )
        self._execute_order(order=order, timestamp=event.timestamp)

    def handle_order_status(self, event: BaseEvent) -> None:
        """注文状態イベントを受けて注文と建玉を更新する。"""

        if not isinstance(event, OrderStatusUpdated):
            return

        order = self._find_order(event.payload.order_id)
        if order is None:
            order = self._restore_order_from_status_event(event)
        if order is None:
            self.logger.warning(
                "order status ignored because order is unknown order_id=%s",
                event.payload.order_id,
            )
            return

        state = self.get_state(order.symbol)
        if not self._is_order_status_consistent(order=order, event=event):
            self.logger.error(
                "order status ignored because quantity is inconsistent order_id=%s order_quantity=%s filled_quantity=%s",
                order.order_id,
                order.quantity,
                event.payload.filled_quantity,
            )
            return
        if not self._should_apply_order_status_update(order=order, event=event):
            self.logger.info(
                "order status discarded order_id=%s current_status=%s incoming_status=%s reason=stale_or_duplicate",
                order.order_id,
                order.status.value,
                event.payload.status.value,
            )
            return

        if event.payload.external_order_id is not None:
            order.external_order_id = event.payload.external_order_id
        elif (
            order.external_order_id is None and event.payload.order_id != order.order_id
        ):
            order.external_order_id = event.payload.order_id
        try:
            reconcile_result = self.order_fill_reconciler.reconcile(
                order=order,
                position=self._ensure_position(state),
                timestamp=event.timestamp,
                filled_quantity=event.payload.filled_quantity,
                average_fill_price=event.payload.avg_price,
                status=event.payload.status,
            )
        except OrderFillReconcileError:
            self.logger.exception(
                "order fill reconcile failed order_id=%s symbol=%s",
                order.order_id,
                order.symbol,
            )
            if self.risk_manager is not None:
                self.risk_manager.on_api_error()
            return
        order.status = event.payload.status
        order.filled_quantity = event.payload.filled_quantity
        order.remaining_quantity = event.payload.remaining_quantity
        order.avg_price = event.payload.avg_price
        if reconcile_result.newly_reflected_quantity > 0:
            state.trade_histories.extend(reconcile_result.trade_histories)
            self._publish_position_updated(
                position=self._ensure_position(state),
                timestamp=event.timestamp,
            )
            if self.risk_manager is not None:
                self.risk_manager.on_trade_result(
                    TradeResult(symbol=order.symbol, realized_pnl=0.0)
                )
        self.logger.info(
            "order status updated order_id=%s external_order_id=%s status=%s filled_quantity=%s reflected_filled_quantity=%s remaining_quantity=%s",
            order.order_id,
            order.external_order_id,
            order.status.value,
            order.filled_quantity,
            order.reflected_filled_quantity,
            order.remaining_quantity,
        )
        if order.status in TERMINAL_ORDER_STATUSES:
            self._remove_order(state=state, order=order)

    def apply_order_status_events(self, events: tuple[OrderStatusUpdated, ...]) -> None:
        """再同期で取得した注文状態イベントを内部状態へ取り込む。"""

        self.logger.info("order resync started count=%s", len(events))
        for event in events:
            self.handle_order_status(event)
        self.logger.info("order resync completed count=%s", len(events))

    def _execute_order(self, order: Order, timestamp: datetime) -> None:
        if not self.auto_fill_orders:
            return
        if self.order_gateway is None:
            self.logger.warning("order gateway is not configured")
            return
        try:
            status_events = self.order_gateway.place_order(
                order=order,
                timestamp=timestamp,
            )
        except NotImplementedError:
            self.logger.warning("order gateway is not implemented", exc_info=True)
            return
        except Exception:
            self.logger.exception("order gateway failed order_id=%s", order.order_id)
            if self.risk_manager is not None:
                self.risk_manager.on_api_error()
            return

        for status_event in status_events:
            self.event_bus.publish(status_event)
        if self.order_status_syncer is None:
            return
        try:
            synced_count = self.order_status_syncer()
        except Exception:
            self.logger.exception(
                "order status sync failed after order order_id=%s", order.order_id
            )
            if self.risk_manager is not None:
                self.risk_manager.on_api_error()
            return
        self.logger.info(
            "order status sync completed after order order_id=%s synced_count=%s",
            order.order_id,
            synced_count,
        )

    def _publish_position_updated(
        self,
        position: Position,
        timestamp: datetime,
    ) -> None:
        position_event = self.event_factory.create(
            event_type=EventType.POSITION_UPDATED,
            timestamp=timestamp,
            symbol=position.symbol,
            payload=PositionPayload(
                symbol=position.symbol,
                quantity=position.quantity,
                avg_price=position.average_price,
                realized_pnl=None,
                unrealized_pnl=None,
            ),
        )
        self.event_bus.publish(position_event)

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
            risk_state=self._risk_state_to_snapshot(),
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
                        external_order_id=order.external_order_id,
                        symbol=order.symbol,
                        side=order.side,
                        quantity=order.quantity,
                        order_type=order.order_type,
                        is_exit=order.is_exit,
                        status=order.status,
                        price=order.price,
                        filled_quantity=order.filled_quantity,
                        remaining_quantity=order.remaining_quantity,
                        avg_price=order.avg_price,
                        reflected_filled_quantity=order.reflected_filled_quantity,
                    )
                    for order in symbol_state.orders
                ],
                trade_histories=[
                    TradeHistory(
                        order_id=trade_history.order_id,
                        symbol=trade_history.symbol,
                        side=trade_history.side,
                        is_exit=trade_history.is_exit,
                        filled_quantity=trade_history.filled_quantity,
                        fill_price=trade_history.fill_price,
                        average_fill_price=trade_history.average_fill_price,
                        filled_at=trade_history.filled_at,
                        status=trade_history.status,
                        source=trade_history.source,
                    )
                    for trade_history in symbol_state.trade_histories
                ],
            )
            for symbol_state in snapshot.symbols
        ]
        if snapshot.risk_state is not None and self.risk_manager is not None:
            self.risk_manager.restore_state(
                RiskControlState(
                    consecutive_losses=snapshot.risk_state.consecutive_losses,
                    consecutive_wins=snapshot.risk_state.consecutive_wins,
                    max_equity=snapshot.risk_state.max_equity,
                    current_equity=snapshot.risk_state.current_equity,
                    kill_switch_active=snapshot.risk_state.kill_switch_active,
                    stopped_by_losses=snapshot.risk_state.stopped_by_losses,
                    daily_realized_loss=snapshot.risk_state.daily_realized_loss,
                    api_error_count=snapshot.risk_state.api_error_count,
                    business_date=snapshot.risk_state.business_date,
                )
            )

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
                    external_order_id=order.external_order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    order_type=order.order_type,
                    is_exit=order.is_exit,
                    status=order.status,
                    price=order.price,
                    filled_quantity=order.filled_quantity,
                    remaining_quantity=order.remaining_quantity,
                    avg_price=order.avg_price,
                    reflected_filled_quantity=order.reflected_filled_quantity,
                )
                for order in state.orders
            ),
            trade_histories=tuple(
                TradeHistorySnapshot(
                    order_id=trade_history.order_id,
                    symbol=trade_history.symbol,
                    side=trade_history.side,
                    is_exit=trade_history.is_exit,
                    filled_quantity=trade_history.filled_quantity,
                    fill_price=trade_history.fill_price,
                    average_fill_price=trade_history.average_fill_price,
                    filled_at=trade_history.filled_at,
                    status=trade_history.status,
                    source=trade_history.source,
                )
                for trade_history in state.trade_histories
            ),
        )

    def _risk_state_to_snapshot(self) -> RiskControlSnapshot | None:
        if self.risk_manager is None:
            return None
        return RiskControlSnapshot(
            consecutive_losses=self.risk_manager.state.consecutive_losses,
            consecutive_wins=self.risk_manager.state.consecutive_wins,
            max_equity=self.risk_manager.state.max_equity,
            current_equity=self.risk_manager.state.current_equity,
            kill_switch_active=self.risk_manager.state.kill_switch_active,
            stopped_by_losses=self.risk_manager.state.stopped_by_losses,
            daily_realized_loss=self.risk_manager.state.daily_realized_loss,
            api_error_count=self.risk_manager.state.api_error_count,
            business_date=self.risk_manager.state.business_date,
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
        symbol: str,
        side: OrderSide,
    ) -> int:
        if signal_type == SignalType.EXIT:
            return abs(position.quantity)
        if self.risk_manager is None:
            return lot_size
        if not self.risk_manager.can_enter(
            symbol=symbol,
            side=side,
            current_state=tuple(self.states),
        ):
            return 0
        return self.risk_manager.calculate_lot(
            symbol=symbol,
            account_state=AccountState(
                available_equity=self.risk_manager.state.current_equity,
                reference_price=self._reference_price(symbol),
            ),
        )

    def _find_order(self, order_id: str) -> Order | None:
        for state in self.states:
            for order in state.orders:
                if order.order_id == order_id or order.external_order_id == order_id:
                    return order
        return None

    def _set_latest_price(self, symbol: str, price: float) -> None:
        for index, (current_symbol, _) in enumerate(self.latest_prices):
            if current_symbol == symbol:
                self.latest_prices[index] = (symbol, price)
                return
        self.latest_prices.append((symbol, price))

    def _set_latest_price_from_signal(self, event: SignalDetected) -> None:
        if event.symbol is None:
            return
        for indicator in event.payload.indicators:
            if indicator.name in ("current_price", "reference_price"):
                self._set_latest_price(symbol=event.symbol, price=indicator.value)
                return

    def _reference_price(self, symbol: str) -> float | None:
        for current_symbol, price in self.latest_prices:
            if current_symbol == symbol:
                return price
        self.logger.warning(
            "reference price is missing symbol=%s lot_calculation_skipped", symbol
        )
        return None

    def _should_apply_order_status_update(
        self,
        order: Order,
        event: OrderStatusUpdated,
    ) -> bool:
        incoming_status = event.payload.status
        if order.status in TERMINAL_ORDER_STATUSES and incoming_status != order.status:
            return False
        current_priority = ORDER_STATUS_PRIORITY[order.status]
        incoming_priority = ORDER_STATUS_PRIORITY[incoming_status]
        if incoming_priority > current_priority:
            return True
        if incoming_priority < current_priority:
            return False
        if event.payload.filled_quantity > order.filled_quantity:
            return True
        if event.payload.remaining_quantity < order.remaining_quantity:
            return True
        if order.external_order_id is None and event.payload.external_order_id:
            return True
        return False

    def _ensure_position(self, state: TradingSymbolState) -> Position:
        if state.position is None:
            state.position = Position(symbol=state.symbol)
        return state.position

    def _restore_order_from_status_event(
        self,
        event: OrderStatusUpdated,
    ) -> Order | None:
        """API同期イベントから未完了注文を復元する。

        Args:
            event: 復元元の注文状態更新イベント。
        Returns:
            復元した注文。復元不要または復元失敗時は None。
        """

        if event.symbol is None:
            return None
        if (
            event.payload.status in TERMINAL_ORDER_STATUSES
            and event.payload.filled_quantity == 0
        ):
            return None
        if event.payload.side is None or event.payload.order_quantity < 1:
            self.logger.error(
                "order restore failed because required fields are missing order_id=%s side=%s order_quantity=%s",
                event.payload.order_id,
                event.payload.side,
                event.payload.order_quantity,
            )
            return None
        state = self.get_state(event.symbol)
        order = Order(
            order_id=event.payload.external_order_id or event.payload.order_id,
            external_order_id=event.payload.external_order_id or event.payload.order_id,
            symbol=event.symbol,
            side=event.payload.side,
            quantity=event.payload.order_quantity,
            order_type="MARKET",
            status=OrderStatus.REQUESTED,
            filled_quantity=0,
            remaining_quantity=event.payload.order_quantity,
            avg_price=None,
        )
        state.orders.append(order)
        self.logger.info(
            "order restored from api order_id=%s symbol=%s side=%s quantity=%s status=%s",
            order.order_id,
            order.symbol,
            order.side.value,
            order.quantity,
            order.status.value,
        )
        return order

    def _is_order_status_consistent(
        self,
        order: Order,
        event: OrderStatusUpdated,
    ) -> bool:
        """注文数量と約定数量の整合を判定する。

        Args:
            order: 既存の内部注文。
            event: 適用対象の注文状態更新イベント。
        Returns:
            整合している場合は True。
        """

        order_quantity = event.payload.order_quantity or order.quantity
        return (
            order.reflected_filled_quantity <= event.payload.filled_quantity
            and event.payload.filled_quantity <= order_quantity
        )

    def _remove_order(
        self,
        state: TradingSymbolState,
        order: Order,
    ) -> None:
        """終端状態の注文を未完了注文一覧から取り除く。

        Args:
            state: 注文を保持する銘柄状態。
            order: 取り除く注文。
        Returns:
            なし。
        """

        if order in state.orders:
            state.orders.remove(order)

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


def _default_risk_manager(
    lot_configs: tuple[TradingSymbolConfig, ...],
    order_quantity: int,
) -> RiskManager:
    max_lot = max(
        (lot_config.lot_size for lot_config in lot_configs), default=order_quantity
    )
    return RiskManager(
        config=RiskConfig(
            max_daily_loss=1_000_000_000.0,
            max_consecutive_losses=1_000_000,
            resume_consecutive_wins=1,
            max_positions=1_000_000,
            max_position_per_symbol=1_000_000_000,
            account_equity=1_000_000_000.0,
            max_drawdown=1_000_000_000.0,
            kill_switch_enabled=True,
            api_error_limit=1_000_000,
            trading_start_time="00:00",
            trading_end_time="23:59",
            order_timeout_sec=30,
        ),
        symbol_configs=tuple(
            RiskSymbolConfig(
                symbol=lot_config.symbol,
                lot_min=lot_config.lot_size,
                lot_max=max(lot_config.lot_size, max_lot),
                allocation_ratio=1.0,
            )
            for lot_config in lot_configs
        )
        or (
            RiskSymbolConfig(
                symbol="7203",
                lot_min=order_quantity,
                lot_max=order_quantity,
                allocation_ratio=1.0,
            ),
        ),
    )

import logging
from dataclasses import dataclass, field

from domain.enums import EventSource, EventType, OrderSide, SignalType
from domain.events import BaseEvent, EventFactory, OrderRequestedPayload, SignalDetected
from infrastructure.event_bus import EventBus


@dataclass
class TradingProcess:
    """シグナルから最小の発注要求を生成するプロセス。"""

    event_bus: EventBus
    order_quantity: int = 100
    order_type: str = "MARKET"
    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.TRADING)
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def start(self) -> None:
        """SignalDetected の購読を開始する。"""

        self.event_bus.subscribe(EventType.SIGNAL_DETECTED, self.handle_signal)

    def handle_signal(self, event: BaseEvent) -> None:
        """シグナルを受けて OrderRequested を publish する。"""

        if not isinstance(event, SignalDetected):
            return
        if event.symbol is None:
            self.logger.warning("signal ignored because symbol is empty")
            return

        side = self._to_order_side(event.payload.signal_type)
        order_event = self.event_factory.create(
            event_type=EventType.ORDER_REQUESTED,
            timestamp=event.timestamp,
            symbol=event.symbol,
            payload=OrderRequestedPayload(
                symbol=event.symbol,
                side=side,
                quantity=self.order_quantity,
                order_type=self.order_type,
                price=None,
            ),
        )
        self.event_bus.publish(order_event)
        self.logger.info(
            "order requested symbol=%s side=%s quantity=%s sequence_no=%s",
            order_event.symbol,
            side.value,
            self.order_quantity,
            order_event.sequence_no,
        )

    def _to_order_side(self, signal_type: SignalType) -> OrderSide:
        if signal_type == SignalType.BUY:
            return OrderSide.BUY
        return OrderSide.SELL

from dataclasses import dataclass, field
from datetime import datetime

from domain.enums import EventSource, EventType, OrderStatus
from domain.events import EventFactory, OrderStatusPayload, OrderStatusUpdated
from domain.models import Order


@dataclass
class MockOrderGateway:
    """ペーパートレード用に注文を即時全部約定へ変換する gateway。"""

    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.TRADING)
    )

    def place_order(
        self,
        order: Order,
        timestamp: datetime,
    ) -> tuple[OrderStatusUpdated, ...]:
        """注文価格を約定価格として使い、未指定時は 0.0 で即時約定する。"""

        filled_event = self.event_factory.create(
            event_type=EventType.ORDER_STATUS_UPDATED,
            timestamp=timestamp,
            symbol=order.symbol,
            payload=OrderStatusPayload(
                order_id=order.order_id,
                status=OrderStatus.FILLED,
                filled_quantity=order.quantity,
                remaining_quantity=0,
                avg_price=order.price or 0.0,
                side=order.side,
                order_quantity=order.quantity,
            ),
        )
        return (filled_event,)

    def cancel_order(
        self,
        order: Order,
        timestamp: datetime,
    ) -> tuple[OrderStatusUpdated, ...]:
        """未使用の取消枠。paper では即時 CANCELED として扱う。"""

        canceled_event = self.event_factory.create(
            event_type=EventType.ORDER_STATUS_UPDATED,
            timestamp=timestamp,
            symbol=order.symbol,
            payload=OrderStatusPayload(
                order_id=order.order_id,
                status=OrderStatus.CANCELED,
                filled_quantity=order.filled_quantity,
                remaining_quantity=order.remaining_quantity,
                avg_price=order.avg_price,
                side=order.side,
                order_quantity=order.quantity,
            ),
        )
        return (canceled_event,)

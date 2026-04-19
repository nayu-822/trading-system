import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from data_source.kabu_api_client import KabuApiClient
from domain.enums import EventSource, EventType
from domain.events import EventFactory, OrderStatusPayload, OrderStatusUpdated
from domain.models import KabuOrderStatus


@dataclass
class RestPoller:
    """REST API の注文状態をイベントへ変換する。"""

    api_client: KabuApiClient
    token: str
    interval_sec: int
    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.EXTERNAL_DATA)
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def poll_once(self) -> tuple[OrderStatusUpdated, ...]:
        """注文状態を1回取得して OrderStatusUpdated へ変換する。"""

        order_statuses = self.api_client.get_orders(self.token)
        return tuple(self.to_event(order_status) for order_status in order_statuses)

    def to_event(self, order_status: KabuOrderStatus) -> OrderStatusUpdated:
        """構造化済み注文状態をイベントへ変換する。"""

        return self.event_factory.create(
            event_type=EventType.ORDER_STATUS_UPDATED,
            timestamp=datetime.now(timezone.utc),
            symbol=order_status.symbol,
            payload=OrderStatusPayload(
                order_id=order_status.order_id,
                status=order_status.status,
                filled_quantity=order_status.filled_quantity,
                remaining_quantity=order_status.remaining_quantity,
                avg_price=order_status.avg_price,
            ),
        )

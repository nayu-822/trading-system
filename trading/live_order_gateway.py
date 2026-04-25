import logging
from dataclasses import dataclass, field
from datetime import datetime

from data_source.kabu_api_client import KabuApiClient
from domain.enums import EventSource, EventType
from domain.events import EventFactory, OrderStatusPayload, OrderStatusUpdated
from domain.models import KabuOrderRequest, Order
from trading.order_safety_validator import OrderSafetyValidator


@dataclass
class LiveOrderGateway:
    """kabu API 経由で live 注文を送信する gateway。"""

    api_client: KabuApiClient
    token: str
    allowed_symbols: tuple[str, ...]
    safety_validator: OrderSafetyValidator
    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.TRADING)
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def place_order(
        self,
        order: Order,
        timestamp: datetime,
    ) -> tuple[OrderStatusUpdated, ...]:
        """注文を API へ送信し、受付状態イベントへ変換する。"""

        self._validate_order(order)
        if self.safety_validator is None:
            raise ValueError("safety_validator is required")
        self.safety_validator.validate_order(order)
        self.logger.info(
            "LIVE MODE order request gateway=LiveOrderGateway order_id=%s symbol=%s side=%s quantity=%s order_type=%s",
            order.order_id,
            order.symbol,
            order.side.value,
            order.quantity,
            order.order_type,
        )
        try:
            result = self.api_client.send_order(
                token=self.token,
                order_request=KabuOrderRequest(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side,
                    quantity=order.quantity,
                    order_type=order.order_type,
                    price=order.price,
                ),
            )
        except Exception:
            self.logger.exception(
                "LIVE MODE order failed gateway=LiveOrderGateway order_id=%s symbol=%s",
                order.order_id,
                order.symbol,
            )
            raise
        status_event = self.event_factory.create(
            event_type=EventType.ORDER_STATUS_UPDATED,
            timestamp=timestamp,
            symbol=result.symbol,
            payload=OrderStatusPayload(
                order_id=order.order_id,
                status=result.status,
                filled_quantity=result.filled_quantity,
                remaining_quantity=result.remaining_quantity,
                avg_price=result.avg_price,
                side=order.side,
                order_quantity=order.quantity,
                is_exit=order.is_exit,
                external_order_id=result.order_id,
            ),
        )
        self.logger.info(
            "LIVE MODE order accepted gateway=LiveOrderGateway order_id=%s status=%s",
            result.order_id,
            result.status.value,
        )
        return (status_event,)

    def cancel_order(
        self,
        order: Order,
        timestamp: datetime,
    ) -> tuple[OrderStatusUpdated, ...]:
        """live 注文取消は未実装。"""

        raise NotImplementedError("live order cancel is not implemented")

    def _validate_order(self, order: Order) -> None:
        if order.symbol not in self.allowed_symbols:
            raise ValueError(f"live order symbol is not enabled: {order.symbol}")
        if order.quantity <= 0:
            raise ValueError("live order quantity must be positive")
        if order.order_type.upper() != "MARKET":
            raise ValueError(f"unsupported live order_type={order.order_type}")

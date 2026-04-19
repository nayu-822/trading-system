from datetime import datetime, timezone

import pytest

from domain.enums import OrderSide, OrderStatus
from domain.models import Order
from trading.live_order_gateway import LiveOrderGateway
from trading.mock_order_gateway import MockOrderGateway


def test_mock_order_gateway_converts_order_to_filled_status() -> None:
    gateway = MockOrderGateway()
    order = Order(
        order_id="order-1",
        symbol="7203",
        side=OrderSide.BUY,
        quantity=100,
        order_type="MARKET",
        price=1234.5,
        remaining_quantity=100,
    )

    events = gateway.place_order(order=order, timestamp=_timestamp())

    assert len(events) == 1
    assert events[0].payload.order_id == "order-1"
    assert events[0].payload.status == OrderStatus.FILLED
    assert events[0].payload.filled_quantity == 100
    assert events[0].payload.remaining_quantity == 0
    assert events[0].payload.avg_price == 1234.5


def test_live_order_gateway_is_explicitly_unimplemented() -> None:
    gateway = LiveOrderGateway()
    order = Order(
        order_id="order-1",
        symbol="7203",
        side=OrderSide.BUY,
        quantity=100,
        order_type="MARKET",
    )

    with pytest.raises(NotImplementedError):
        gateway.place_order(order=order, timestamp=_timestamp())


def _timestamp() -> datetime:
    return datetime(2026, 4, 18, tzinfo=timezone.utc)

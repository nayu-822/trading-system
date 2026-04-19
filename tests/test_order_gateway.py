from datetime import datetime, timezone

import pytest

from data_source.kabu_api_client import KabuApiError
from domain.enums import OrderSide, OrderStatus
from domain.events import OrderStatusUpdated
from domain.models import KabuOrderRequest, KabuOrderResult, Order
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


def test_live_order_gateway_sends_order_through_api_client() -> None:
    class FakeApiClient:
        def __init__(self) -> None:
            self.sent_request: KabuOrderRequest | None = None

        def send_order(
            self,
            token: str,
            order_request: KabuOrderRequest,
        ) -> KabuOrderResult:
            assert token == "token-1"
            self.sent_request = order_request
            return KabuOrderResult(
                order_id="api-order-1",
                symbol=order_request.symbol,
                status=OrderStatus.REQUESTED,
                filled_quantity=0,
                remaining_quantity=order_request.quantity,
                avg_price=None,
            )

    api_client = FakeApiClient()
    gateway = LiveOrderGateway(
        api_client=api_client,  # type: ignore[arg-type]
        token="token-1",
        allowed_symbols=("7203",),
    )
    order = Order(
        order_id="order-1",
        symbol="7203",
        side=OrderSide.BUY,
        quantity=100,
        order_type="MARKET",
    )

    events = gateway.place_order(order=order, timestamp=_timestamp())

    assert api_client.sent_request is not None
    assert api_client.sent_request.order_id == "order-1"
    assert len(events) == 1
    assert isinstance(events[0], OrderStatusUpdated)
    assert events[0].payload.order_id == "api-order-1"
    assert events[0].payload.status == OrderStatus.REQUESTED
    assert events[0].payload.remaining_quantity == 100


def test_live_order_gateway_rejects_unsupported_order_type() -> None:
    class FakeApiClient:
        def send_order(
            self,
            token: str,
            order_request: KabuOrderRequest,
        ) -> KabuOrderResult:
            raise AssertionError("send_order should not be called")

    gateway = LiveOrderGateway(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
        allowed_symbols=("7203",),
    )
    order = Order(
        order_id="order-1",
        symbol="7203",
        side=OrderSide.BUY,
        quantity=100,
        order_type="LIMIT",
    )

    with pytest.raises(ValueError):
        gateway.place_order(order=order, timestamp=_timestamp())


def test_live_order_gateway_raises_api_error_safely() -> None:
    class FakeApiClient:
        def send_order(
            self,
            token: str,
            order_request: KabuOrderRequest,
        ) -> KabuOrderResult:
            raise KabuApiError("api failed")

    gateway = LiveOrderGateway(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
        allowed_symbols=("7203",),
    )
    order = Order(
        order_id="order-1",
        symbol="7203",
        side=OrderSide.BUY,
        quantity=100,
        order_type="MARKET",
    )

    with pytest.raises(KabuApiError):
        gateway.place_order(order=order, timestamp=_timestamp())


def _timestamp() -> datetime:
    return datetime(2026, 4, 18, tzinfo=timezone.utc)

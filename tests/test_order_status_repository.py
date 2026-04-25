import logging

import pytest

from data_source.kabu_api_client import KabuApiError
from domain.enums import OrderSide, OrderStatus
from infrastructure.repositories.order_status_repository import (
    OrderStatusRepository,
    OrderStatusRepositoryError,
)


def test_order_status_repository_returns_open_orders() -> None:
    class FakeApiClient:
        def get_orders(self, token: str):
            assert token == "token-1"
            return (
                _api_order(status=OrderStatus.REQUESTED),
                _api_order(order_id="filled-1", status=OrderStatus.FILLED),
            )

    repository = OrderStatusRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    orders = repository.get_open_orders(symbol="7203")

    assert len(orders) == 1
    assert orders[0].order_id == "order-1"
    assert orders[0].status == OrderStatus.REQUESTED


def test_order_status_repository_returns_order_status_by_order_id() -> None:
    class FakeApiClient:
        def get_orders(self, token: str):
            return (_api_order(order_id="order-1"),)

    repository = OrderStatusRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    order = repository.get_order_status("order-1")

    assert order is not None
    assert order.symbol == "7203"
    assert order.side == OrderSide.BUY


def test_order_status_repository_raises_when_api_fails() -> None:
    class FakeApiClient:
        def get_orders(self, token: str):
            raise KabuApiError("api failed")

    repository = OrderStatusRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    with pytest.raises(OrderStatusRepositoryError):
        repository.get_open_orders(symbol="7203")


def test_order_status_repository_raises_when_order_status_is_unknown() -> None:
    class FakeApiClient:
        def get_orders(self, token: str):
            raise KabuApiError("unsupported order status=UNKNOWN_STATUS")

    repository = OrderStatusRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    with pytest.raises(OrderStatusRepositoryError):
        repository.get_open_orders(symbol="7203")


def test_order_status_repository_raises_when_symbol_is_empty() -> None:
    class FakeApiClient:
        def get_orders(self, token: str):
            return (_api_order(symbol=None),)

    repository = OrderStatusRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    with pytest.raises(OrderStatusRepositoryError):
        repository.list_orders()


def test_order_status_repository_raises_when_filled_quantity_exceeds_order_quantity() -> None:
    class FakeApiClient:
        def get_orders(self, token: str):
            return (_api_order(quantity=100, filled_quantity=200),)

    repository = OrderStatusRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    with pytest.raises(OrderStatusRepositoryError):
        repository.list_orders()


def test_order_status_repository_uses_cache_within_ttl() -> None:
    class FakeApiClient:
        def __init__(self) -> None:
            self.call_count = 0

        def get_orders(self, token: str):
            self.call_count += 1
            return (_api_order(order_id=f"order-{self.call_count}"),)

    current_time = 100.0
    api_client = FakeApiClient()
    repository = OrderStatusRepository(
        api_client=api_client,  # type: ignore[arg-type]
        token="token-1",
        cache_ttl_sec=1.0,
        time_provider=lambda: current_time,
    )

    first_orders = repository.list_orders()
    second_orders = repository.list_orders()

    assert api_client.call_count == 1
    assert first_orders[0].order_id == second_orders[0].order_id


def test_order_status_repository_logs_order_status(caplog) -> None:
    class FakeApiClient:
        def get_orders(self, token: str):
            return (_api_order(),)

    repository = OrderStatusRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    with caplog.at_level(logging.INFO):
        repository.list_orders()

    assert "order status loaded source=api" in caplog.text


def _api_order(
    order_id: str = "order-1",
    symbol: str | None = "7203",
    status: OrderStatus = OrderStatus.REQUESTED,
    quantity: int = 100,
    filled_quantity: int = 0,
):
    from domain.models import KabuOrderStatus

    return KabuOrderStatus(
        order_id=order_id,
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=quantity,
        status=status,
        filled_quantity=filled_quantity,
        remaining_quantity=max(quantity - filled_quantity, 0),
        avg_price=1000.0 if filled_quantity > 0 else None,
        external_order_id=order_id,
    )

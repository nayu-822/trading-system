from datetime import datetime, timezone

import pytest

from data_source.kabu_api_client import KabuApiError
from domain.enums import (
    KabuApiEnvironment,
    OrderSide,
    OrderStatus,
    TradingMode,
)
from domain.events import OrderStatusUpdated
from domain.models import KabuOrderRequest, KabuOrderResult, Order, Position
from trading.live_order_gateway import LiveOrderGateway
from trading.mock_order_gateway import MockOrderGateway
from trading.order_safety_validator import (
    OrderSafetyError,
    OrderSafetyState,
    OrderSafetyValidator,
)


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
    assert events[0].payload.order_id == "order-1"
    assert events[0].payload.external_order_id == "api-order-1"
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


@pytest.mark.parametrize(
    ("trading_mode", "environment", "allowed"),
    [
        (TradingMode.LIVE, KabuApiEnvironment.LIVE, True),
        (TradingMode.PAPER, KabuApiEnvironment.LIVE, False),
        (TradingMode.LIVE, KabuApiEnvironment.PAPER, False),
        (TradingMode.PAPER, KabuApiEnvironment.PAPER, False),
    ],
)
def test_live_order_gateway_allows_real_order_only_in_live_live(
    trading_mode: TradingMode,
    environment: KabuApiEnvironment,
    allowed: bool,
) -> None:
    class FakeApiClient:
        def __init__(self) -> None:
            self.call_count = 0

        def send_order(
            self,
            token: str,
            order_request: KabuOrderRequest,
        ) -> KabuOrderResult:
            self.call_count += 1
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
        safety_validator=_validator(
            trading_mode=trading_mode,
            environment=environment,
        ),
    )

    if allowed:
        gateway.place_order(order=_market_buy_order(), timestamp=_timestamp())
        assert api_client.call_count == 1
        return

    with pytest.raises(OrderSafetyError):
        gateway.place_order(order=_market_buy_order(), timestamp=_timestamp())
    assert api_client.call_count == 0


def test_live_order_gateway_rejects_symbol_not_in_trade_symbols() -> None:
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
        safety_validator=_validator(trade_symbols=("6758",)),
    )

    with pytest.raises(OrderSafetyError):
        gateway.place_order(order=_market_buy_order(), timestamp=_timestamp())


def test_live_order_gateway_rejects_quantity_over_max_order_quantity() -> None:
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
        safety_validator=_validator(max_order_quantity=1),
    )

    with pytest.raises(OrderSafetyError):
        gateway.place_order(
            order=_market_buy_order(quantity=2),
            timestamp=_timestamp(),
        )


def test_live_order_gateway_rejects_non_positive_quantity() -> None:
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
        safety_validator=_validator(),
    )

    with pytest.raises(ValueError):
        gateway.place_order(
            order=_market_buy_order(quantity=0),
            timestamp=_timestamp(),
        )


def test_live_order_gateway_rejects_when_incomplete_order_exists() -> None:
    class FakeApiClient:
        def send_order(
            self,
            token: str,
            order_request: KabuOrderRequest,
        ) -> KabuOrderResult:
            raise AssertionError("send_order should not be called")

    existing_order = _market_buy_order(order_id="existing-order")
    gateway = LiveOrderGateway(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
        allowed_symbols=("7203",),
        safety_validator=_validator(
            state_provider=lambda symbol: OrderSafetyState(
                position=Position(symbol=symbol),
                open_orders=(existing_order,),
            )
        ),
    )

    with pytest.raises(OrderSafetyError):
        gateway.place_order(order=_market_buy_order(), timestamp=_timestamp())


def test_live_order_gateway_rejects_when_position_state_is_unavailable() -> None:
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
        safety_validator=_validator(
            state_provider=lambda _symbol: (_ for _ in ()).throw(
                ValueError("position unavailable")
            )
        ),
    )

    with pytest.raises(OrderSafetyError):
        gateway.place_order(order=_market_buy_order(), timestamp=_timestamp())


def test_live_order_gateway_rejects_exit_without_target_position() -> None:
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
        safety_validator=_validator(
            state_provider=lambda symbol: OrderSafetyState(
                position=Position(symbol=symbol, quantity=0),
            )
        ),
    )

    with pytest.raises(OrderSafetyError):
        gateway.place_order(
            order=_market_sell_order(quantity=1, is_exit=True),
            timestamp=_timestamp(),
        )


def _timestamp() -> datetime:
    return datetime(2026, 4, 18, tzinfo=timezone.utc)


def _market_buy_order(
    order_id: str = "order-1",
    quantity: int = 1,
    is_exit: bool = False,
) -> Order:
    return Order(
        order_id=order_id,
        symbol="7203",
        side=OrderSide.BUY,
        quantity=quantity,
        order_type="MARKET",
        is_exit=is_exit,
    )


def _market_sell_order(
    order_id: str = "order-1",
    quantity: int = 1,
    is_exit: bool = False,
) -> Order:
    return Order(
        order_id=order_id,
        symbol="7203",
        side=OrderSide.SELL,
        quantity=quantity,
        order_type="MARKET",
        is_exit=is_exit,
    )


def _validator(
    trading_mode: TradingMode = TradingMode.LIVE,
    environment: KabuApiEnvironment = KabuApiEnvironment.LIVE,
    max_order_quantity: int = 1,
    trade_symbols: tuple[str, ...] = ("7203",),
    state_provider=None,
) -> OrderSafetyValidator:
    return OrderSafetyValidator(
        trading_mode=trading_mode,
        kabu_api_environment=environment,
        max_order_quantity=max_order_quantity,
        trade_symbols=trade_symbols,
        enabled_symbols=("7203",),
        state_provider=state_provider
        or (lambda symbol: OrderSafetyState(position=Position(symbol=symbol))),
    )

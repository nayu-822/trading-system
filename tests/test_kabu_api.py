import json
import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from data_source.kabu_api_client import KabuApiClient, KabuApiError
from data_source.push_client import PushClient
from data_source.rest_poller import RestPoller
from domain.enums import (
    DataSourceMode,
    EventSource,
    EventType,
    KabuApiEnvironment,
    OrderSide,
    OrderStatus,
)
from domain.events import (
    BaseEvent,
    EventFactory,
    MarketDataUpdated,
    OrderStatusPayload,
    OrderStatusUpdated,
)
from domain.models import KabuApiConfig, KabuOrderRequest, Order
from infrastructure.event_bus import EventBus
from processes.external_data_process import ExternalDataProcess


def test_kabu_api_client_gets_token_and_orders(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_sec: int,
    ) -> bytes:
        calls.append((method, url))
        assert timeout_sec == 5
        if url.endswith("/token"):
            assert body is not None
            assert json.loads(body.decode("utf-8"))["APIPassword"] == "secret"
            return b'{"Token": "token-1"}'
        assert headers["X-API-KEY"] == "token-1"
        return json.dumps(
            {
                "Orders": [
                    {
                        "ID": "order-1",
                        "Symbol": "7203",
                        "Side": "BUY",
                        "Status": "FILLED",
                        "Qty": 100,
                        "CumQty": 100,
                        "LeavesQty": 0,
                        "AvgPrice": 1000.0,
                    }
                ]
            }
        ).encode("utf-8")

    monkeypatch.setenv("KABU_API_PASSWORD", "secret")
    client = KabuApiClient(config=_api_config(), http_request=fake_request)

    token = client.get_token()
    orders = client.get_orders(token)

    assert token == "token-1"
    assert calls == [
        ("POST", "http://localhost:18081/kabusapi/token"),
        ("GET", "http://localhost:18081/kabusapi/orders"),
    ]
    assert orders[0].order_id == "order-1"
    assert orders[0].side == OrderSide.BUY
    assert orders[0].quantity == 100
    assert orders[0].status == OrderStatus.FILLED


def test_kabu_api_client_logs_and_raises_on_failure(caplog) -> None:
    def failing_request(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_sec: int,
    ) -> bytes:
        raise OSError("connection failed")

    client = KabuApiClient(config=_api_config(), http_request=failing_request)

    with pytest.raises(KabuApiError), caplog.at_level(logging.ERROR):
        client.get_orders("token-1")

    assert "kabu orders request failed" in caplog.text


def test_kabu_api_client_filters_orders_by_symbol_and_order_id() -> None:
    def fake_request(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_sec: int,
    ) -> bytes:
        return json.dumps(
            {
                "Orders": [
                    {
                        "ID": "order-1",
                        "Symbol": "7203",
                        "Side": "BUY",
                        "Status": "REQUESTED",
                        "Qty": 100,
                        "CumQty": 0,
                        "LeavesQty": 100,
                    },
                    {
                        "ID": "order-2",
                        "Symbol": "6758",
                        "Side": "SELL",
                        "Status": "REQUESTED",
                        "Qty": 50,
                        "CumQty": 0,
                        "LeavesQty": 50,
                    },
                ]
            }
        ).encode("utf-8")

    client = KabuApiClient(config=_api_config(), http_request=fake_request)

    orders = client.get_orders(token="token-1", symbol="7203", order_id="order-1")

    assert len(orders) == 1
    assert orders[0].order_id == "order-1"
    assert orders[0].symbol == "7203"


def test_kabu_api_client_raises_when_order_status_is_unknown() -> None:
    def fake_request(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_sec: int,
    ) -> bytes:
        return json.dumps(
            {
                "Orders": [
                    {
                        "ID": "order-1",
                        "Symbol": "7203",
                        "Side": "BUY",
                        "Status": "UNKNOWN_STATUS",
                        "Qty": 100,
                        "CumQty": 0,
                        "LeavesQty": 100,
                    }
                ]
            }
        ).encode("utf-8")

    client = KabuApiClient(config=_api_config(), http_request=fake_request)

    with pytest.raises(KabuApiError):
        client.get_orders(token="token-1")


@pytest.mark.parametrize(
    "status_value",
    [None, ""],
)
def test_kabu_api_client_raises_when_order_status_is_missing_or_empty(
    status_value: str | None,
) -> None:
    def fake_request(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_sec: int,
    ) -> bytes:
        return json.dumps(
            {
                "Orders": [
                    {
                        "ID": "order-1",
                        "Symbol": "7203",
                        "Side": "BUY",
                        "Status": status_value,
                        "Qty": 100,
                        "CumQty": 0,
                        "LeavesQty": 100,
                    }
                ]
            }
        ).encode("utf-8")

    client = KabuApiClient(config=_api_config(), http_request=fake_request)

    with pytest.raises(KabuApiError):
        client.get_orders(token="token-1")


def test_kabu_api_client_raises_when_order_status_key_is_missing() -> None:
    def fake_request(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_sec: int,
    ) -> bytes:
        return json.dumps(
            {
                "Orders": [
                    {
                        "ID": "order-1",
                        "Symbol": "7203",
                        "Side": "BUY",
                        "Qty": 100,
                        "CumQty": 0,
                        "LeavesQty": 100,
                    }
                ]
            }
        ).encode("utf-8")

    client = KabuApiClient(config=_api_config(), http_request=fake_request)

    with pytest.raises(KabuApiError):
        client.get_orders(token="token-1")


def test_kabu_api_client_sends_order_and_returns_result() -> None:
    calls: list[tuple[str, str, Mapping[str, str], dict[str, Any]]] = []

    def fake_request(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_sec: int,
    ) -> bytes:
        assert body is not None
        calls.append((method, url, headers, json.loads(body.decode("utf-8"))))
        return json.dumps(
            {
                "OrderID": "api-order-1",
                "Symbol": "7203",
                "Status": "REQUESTED",
                "CumQty": 0,
                "LeavesQty": 100,
            }
        ).encode("utf-8")

    client = KabuApiClient(config=_api_config(), http_request=fake_request)
    result = client.send_order(
        token="token-1",
        order_request=KabuOrderRequest(
            order_id="local-order-1",
            symbol="7203",
            side=OrderSide.BUY,
            quantity=100,
            order_type="MARKET",
            price=None,
        ),
    )

    assert calls[0][0] == "POST"
    assert calls[0][1] == "http://localhost:18081/kabusapi/sendorder"
    assert calls[0][2]["X-API-KEY"] == "token-1"
    assert calls[0][3]["Symbol"] == "7203"
    assert calls[0][3]["Side"] == "BUY"
    assert calls[0][3]["Qty"] == 100
    assert result.order_id == "api-order-1"
    assert result.status == OrderStatus.REQUESTED
    assert result.remaining_quantity == 100


def test_kabu_api_client_gets_positions_and_filters_by_symbol() -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_sec: int,
    ) -> bytes:
        calls.append((method, url))
        return json.dumps(
            [
                {
                    "Symbol": "7203",
                    "Side": "BUY",
                    "HoldQty": 100,
                    "Price": 1000.0,
                },
                {
                    "Symbol": "6758",
                    "Side": "SELL",
                    "HoldQty": 50,
                    "Price": 2000.0,
                },
            ]
        ).encode("utf-8")

    client = KabuApiClient(config=_api_config(), http_request=fake_request)

    positions = client.get_positions(token="token-1", symbol="7203")

    assert calls == [("GET", "http://localhost:18081/kabusapi/positions")]
    assert len(positions) == 1
    assert positions[0].symbol == "7203"
    assert positions[0].quantity == 100
    assert positions[0].average_price == 1000.0


def test_kabu_api_client_ignores_different_symbol_positions() -> None:
    def fake_request(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_sec: int,
    ) -> bytes:
        return json.dumps(
            [
                {
                    "Symbol": "6758",
                    "Side": "BUY",
                    "HoldQty": 100,
                    "Price": 1000.0,
                },
            ]
        ).encode("utf-8")

    client = KabuApiClient(config=_api_config(), http_request=fake_request)

    positions = client.get_positions(token="token-1", symbol="7203")

    assert positions == ()


def test_push_client_converts_message_to_market_data_event() -> None:
    push_client = PushClient(config=_api_config())

    event = push_client.handle_message(
        {
            "Symbol": "7203",
            "CurrentPrice": 1000.0,
            "BidPrice": 999.0,
            "AskPrice": 1001.0,
            "TradingVolume": 12300,
            "CurrentPriceTime": "2026-04-18T09:00:00+09:00",
        }
    )

    assert isinstance(event, MarketDataUpdated)
    assert event.event_type == EventType.MARKET_DATA_UPDATED
    assert event.symbol == "7203"
    assert event.payload.price == 1000.0
    assert event.payload.bid == 999.0
    assert event.payload.volume == 12300


def test_push_client_warns_when_connector_is_not_configured(caplog) -> None:
    push_client = PushClient(config=_api_config())

    with caplog.at_level(logging.WARNING):
        push_client.start()

    assert push_client.running is False
    assert "push connector is not configured" in caplog.text


def test_rest_poller_converts_orders_to_events() -> None:
    class FakeOrderStatusRepository:
        def list_orders(self, force_refresh: bool = False):
            assert force_refresh is False
            return (
                Order(
                    order_id="order-1",
                    external_order_id="order-1",
                    symbol="7203",
                    side=OrderSide.BUY,
                    quantity=100,
                    order_type="MARKET",
                    status=OrderStatus.FILLED,
                    filled_quantity=100,
                    remaining_quantity=0,
                    avg_price=1000.0,
                ),
            )

    poller = RestPoller(
        order_status_repository=FakeOrderStatusRepository(),  # type: ignore[arg-type]
        interval_sec=5,
    )

    events = poller.poll_once()

    assert len(events) == 1
    assert isinstance(events[0], OrderStatusUpdated)
    assert events[0].payload.order_id == "order-1"
    assert events[0].payload.status == OrderStatus.FILLED
    assert events[0].payload.order_quantity == 100
    assert events[0].payload.side == OrderSide.BUY


def test_rest_poller_starts_periodic_polling() -> None:
    class FakeOrderStatusRepository:
        def __init__(self) -> None:
            self.call_count = 0

        def list_orders(self, force_refresh: bool = False):
            self.call_count += 1
            return (
                Order(
                    order_id=f"order-{self.call_count}",
                    external_order_id=f"order-{self.call_count}",
                    symbol="7203",
                    side=OrderSide.BUY,
                    quantity=100,
                    order_type="MARKET",
                    status=OrderStatus.FILLED,
                    filled_quantity=100,
                    remaining_quantity=0,
                    avg_price=1000.0,
                ),
            )
    order_status_repository = FakeOrderStatusRepository()
    received_events: list[OrderStatusUpdated] = []
    poller = RestPoller(
        order_status_repository=order_status_repository,  # type: ignore[arg-type]
        interval_sec=0.01,
        on_event=received_events.append,
    )

    poller.start()
    _wait_until(lambda: len(received_events) >= 2)
    poller.stop()

    assert order_status_repository.call_count >= 2
    assert received_events[0].payload.order_id == "order-1"


def test_external_data_process_switches_csv_mode() -> None:
    class FakeCsvLoader:
        def load_events(self, csv_path: Path, symbol: str):
            assert csv_path == Path("dummy.csv")
            assert symbol == "7203"
            return ()

    process = ExternalDataProcess(
        event_bus=EventBus(),
        csv_loader=FakeCsvLoader(),  # type: ignore[arg-type]
    )

    process.run(
        data_source_mode=DataSourceMode.CSV,
        csv_path=Path("dummy.csv"),
        symbol="7203",
    )


def test_external_data_process_switches_api_mode() -> None:
    class FakePushClient:
        on_event = None
        started = False

        def start(self) -> None:
            self.started = True
            assert self.on_event is not None

    class FakeRestPoller:
        on_event = None
        started = False

        def start(self) -> None:
            self.started = True
            assert self.on_event is not None

    event_bus = EventBus()
    push_client = FakePushClient()
    process = ExternalDataProcess(
        event_bus=event_bus,
        push_client=push_client,  # type: ignore[arg-type]
        rest_poller=FakeRestPoller(),  # type: ignore[arg-type]
    )

    process.run(
        data_source_mode=DataSourceMode.API,
        csv_path=None,
        symbol="7203",
    )

    assert push_client.started is True
    assert process.rest_poller.started is True  # type: ignore[union-attr]


def test_external_data_process_syncs_orders_once() -> None:
    class FakeRestPoller:
        def poll_once(self, force_refresh: bool = False) -> tuple[OrderStatusUpdated, ...]:
            return (
                EventFactory(source=EventSource.EXTERNAL_DATA).create(
                    event_type=EventType.ORDER_STATUS_UPDATED,
                    timestamp=_timestamp(),
                    symbol="7203",
                    payload=OrderStatusPayload(
                        order_id="order-1",
                        status=OrderStatus.FILLED,
                        filled_quantity=100,
                        remaining_quantity=0,
                        avg_price=1000.0,
                    ),
                ),
            )

    event_bus = EventBus()
    received_events: list[BaseEvent[Any]] = []
    event_bus.subscribe(EventType.ORDER_STATUS_UPDATED, received_events.append)
    process = ExternalDataProcess(
        event_bus=event_bus,
        rest_poller=FakeRestPoller(),  # type: ignore[arg-type]
    )

    synced_count = process.sync_orders_once()

    assert synced_count == 1
    assert len(received_events) == 1
    assert received_events[0].payload.order_id == "order-1"


def _api_config() -> KabuApiConfig:
    return KabuApiConfig(
        environment=KabuApiEnvironment.PAPER,
        base_url="http://localhost:18081/kabusapi",
        push_url="ws://localhost:18081/kabusapi/websocket",
        timeout_sec=5,
        token_env_name="KABU_API_PASSWORD",
    )


def _timestamp() -> datetime:
    return datetime(2026, 4, 18, tzinfo=timezone.utc)


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met")

import json
import logging
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from data_source.kabu_api_client import KabuApiClient, KabuApiError
from data_source.push_client import PushClient
from data_source.rest_poller import RestPoller
from domain.enums import DataSourceMode, EventType, OrderStatus
from domain.events import BaseEvent, MarketDataUpdated, OrderStatusUpdated
from domain.models import KabuApiConfig, KabuOrderStatus
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
                        "Status": "FILLED",
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
        ("POST", "http://localhost:18080/kabusapi/token"),
        ("GET", "http://localhost:18080/kabusapi/orders"),
    ]
    assert orders[0].order_id == "order-1"
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
    class FakeApiClient:
        def get_orders(self, token: str) -> tuple[KabuOrderStatus, ...]:
            assert token == "token-1"
            return (
                KabuOrderStatus(
                    order_id="order-1",
                    symbol="7203",
                    status=OrderStatus.FILLED,
                    filled_quantity=100,
                    remaining_quantity=0,
                    avg_price=1000.0,
                ),
            )

    poller = RestPoller(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
        interval_sec=5,
    )

    events = poller.poll_once()

    assert len(events) == 1
    assert isinstance(events[0], OrderStatusUpdated)
    assert events[0].payload.order_id == "order-1"
    assert events[0].payload.status == OrderStatus.FILLED


def test_rest_poller_starts_periodic_polling() -> None:
    class FakeApiClient:
        def __init__(self) -> None:
            self.call_count = 0

        def get_orders(self, token: str) -> tuple[KabuOrderStatus, ...]:
            self.call_count += 1
            return (
                KabuOrderStatus(
                    order_id=f"order-{self.call_count}",
                    symbol="7203",
                    status=OrderStatus.FILLED,
                    filled_quantity=100,
                    remaining_quantity=0,
                    avg_price=1000.0,
                ),
            )

    api_client = FakeApiClient()
    received_events: list[OrderStatusUpdated] = []
    poller = RestPoller(
        api_client=api_client,  # type: ignore[arg-type]
        token="token-1",
        interval_sec=0.01,
        on_event=received_events.append,
    )

    poller.start()
    _wait_until(lambda: len(received_events) >= 2)
    poller.stop()

    assert api_client.call_count >= 2
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


def _api_config() -> KabuApiConfig:
    return KabuApiConfig(
        base_url="http://localhost:18080/kabusapi",
        push_url="ws://localhost:18080/kabusapi/websocket",
        timeout_sec=5,
        token_env_name="KABU_API_PASSWORD",
    )


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met")

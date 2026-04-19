from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import UUID

import pytest

from domain.enums import (
    EventSource,
    EventType,
    OrderSide,
    SignalType,
    StrategyType,
)
from domain.events import (
    BaseEvent,
    BasePayload,
    MarketDataPayload,
    MarketDataUpdated,
    OrderPayload,
    OrderRequested,
    OrderRequestedPayload,
    SignalDetected,
    SignalPayload,
)
from domain.models import IndicatorValue


def test_event_can_be_created_with_typed_payload() -> None:
    event = MarketDataUpdated(
        event_id=UUID("11111111-1111-1111-1111-111111111111"),
        timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
        source=EventSource.EXTERNAL_DATA,
        symbol="7203",
        payload=MarketDataPayload(
            price=2500.0,
            bid=2499.5,
            ask=2500.5,
            volume=1000,
            timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
        ),
        sequence_no=1,
    )

    assert event.event_type == EventType.MARKET_DATA_UPDATED
    assert event.payload.price == 2500.0
    assert isinstance(event.payload, BasePayload)
    with pytest.raises(FrozenInstanceError):
        event.sequence_no = 2  # type: ignore[misc]


def test_event_to_dict_from_dict_round_trip() -> None:
    event = SignalDetected(
        event_id=UUID("11111111-1111-1111-1111-111111111111"),
        timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
        source=EventSource.SIGNAL,
        symbol="7203",
        payload=SignalPayload(
            signal_type=SignalType.BUY,
            strategy_type=StrategyType.TREND,
            confidence=0.82,
            indicators=(IndicatorValue(name="ma_diff", value=12.3),),
        ),
        sequence_no=3,
    )

    restored_event = BaseEvent.from_dict(event.to_dict())

    assert restored_event == event
    assert isinstance(restored_event, SignalDetected)
    assert restored_event.payload.signal_type == SignalType.BUY
    assert restored_event.payload.indicators[0].name == "ma_diff"


def test_enum_values_are_serialized_as_strings() -> None:
    event = OrderRequested(
        event_id=UUID("11111111-1111-1111-1111-111111111111"),
        timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
        source=EventSource.TRADING,
        symbol="7203",
        payload=OrderPayload(
            symbol="7203",
            side=OrderSide.BUY,
            quantity=100,
            order_type="LIMIT",
            price=2500.0,
        ),
        sequence_no=5,
    )

    event_data = event.to_dict()

    assert event_data["event_type"] == "OrderRequested"
    assert event_data["source"] == "trading"
    assert event_data["payload"]["side"] == "BUY"
    assert BaseEvent.from_dict(event_data).payload.side == OrderSide.BUY
    assert isinstance(BaseEvent.from_dict(event_data).payload, OrderRequestedPayload)


def test_event_rejects_invalid_payload_type() -> None:
    with pytest.raises(TypeError):
        OrderRequested(
            timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
            source=EventSource.TRADING,
            symbol="7203",
            payload={  # type: ignore[arg-type]
                "symbol": "7203",
                "side": "BUY",
                "quantity": 100,
                "order_type": "LIMIT",
            },
            sequence_no=1,
        )


def test_event_rejects_mismatched_payload_type() -> None:
    with pytest.raises(TypeError):
        OrderRequested(
            timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
            source=EventSource.TRADING,
            symbol="7203",
            payload=MarketDataPayload(  # type: ignore[arg-type]
                price=2500.0,
                bid=2499.5,
                ask=2500.5,
                volume=1000,
                timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
            ),
            sequence_no=1,
        )


def test_event_type_matches_event_definition() -> None:
    assert {event_type.value for event_type in EventType} >= {
        "MarketDataUpdated",
        "SignalDetected",
        "OrderRequested",
        "OrderStatusUpdated",
        "PositionUpdated",
        "RiskUpdated",
        "LotUpdated",
        "SnapshotRequested",
        "SnapshotCreated",
        "ErrorOccurred",
    }


def test_from_dict_rejects_invalid_datetime() -> None:
    event_data = {
        "event_id": "11111111-1111-1111-1111-111111111111",
        "event_type": "MarketDataUpdated",
        "timestamp": "invalid",
        "source": "external_data",
        "symbol": "7203",
        "payload": {
            "price": 2500.0,
            "bid": 2499.5,
            "ask": 2500.5,
            "volume": 1000,
            "timestamp": "2026-04-18T00:00:00+00:00",
        },
        "sequence_no": 1,
    }

    with pytest.raises(ValueError):
        BaseEvent.from_dict(event_data)

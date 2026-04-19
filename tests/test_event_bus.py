from datetime import datetime, timezone
from typing import Any

import pytest

from domain.enums import EventSource, EventType
from domain.events import BaseEvent, ErrorPayload, EventFactory, SystemStartedPayload
from infrastructure.event_bus import EventBus


def _create_event(event_type: EventType = EventType.SYSTEM_STARTED) -> BaseEvent[Any]:
    payload = (
        ErrorPayload(error_type="RuntimeError", message="failed")
        if event_type == EventType.ERROR_OCCURRED
        else SystemStartedPayload(mode="mock")
    )
    return EventFactory(source=EventSource.MAIN).create(
        event_type=event_type,
        timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
        symbol=None,
        payload=payload,
    )


def test_publish_delivers_event_to_subscriber() -> None:
    event_bus = EventBus()
    received_events: list[BaseEvent[Any]] = []
    event = _create_event()

    event_bus.subscribe(EventType.SYSTEM_STARTED, received_events.append)
    event_bus.publish(event)

    assert received_events == [event]


def test_publish_delivers_event_to_multiple_subscribers() -> None:
    event_bus = EventBus()
    received_events_a: list[BaseEvent[Any]] = []
    received_events_b: list[BaseEvent[Any]] = []
    event = _create_event()

    event_bus.subscribe(EventType.SYSTEM_STARTED, received_events_a.append)
    event_bus.subscribe(EventType.SYSTEM_STARTED, received_events_b.append)
    event_bus.publish(event)

    assert received_events_a == [event]
    assert received_events_b == [event]


def test_publish_continues_delivery_then_raises_when_handler_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event_bus = EventBus()
    event = _create_event()
    received_events: list[BaseEvent[Any]] = []

    def raise_error(_: BaseEvent[Any]) -> None:
        raise RuntimeError("handler failed")

    event_bus.subscribe(EventType.SYSTEM_STARTED, raise_error)
    event_bus.subscribe(EventType.SYSTEM_STARTED, received_events.append)

    with pytest.raises(RuntimeError), caplog.at_level("ERROR"):
        event_bus.publish(event)

    assert received_events == [event]
    assert "event handler failed" in caplog.text


def test_unsubscribe_removes_handler() -> None:
    event_bus = EventBus()
    received_events: list[BaseEvent[Any]] = []
    event = _create_event()

    event_bus.subscribe(EventType.SYSTEM_STARTED, received_events.append)
    event_bus.unsubscribe(EventType.SYSTEM_STARTED, received_events.append)
    event_bus.publish(event)

    assert received_events == []


def test_publish_ignores_unsubscribed_event_type() -> None:
    event_bus = EventBus()
    received_events: list[BaseEvent[Any]] = []
    event = _create_event(EventType.ERROR_OCCURRED)

    event_bus.subscribe(EventType.SYSTEM_STARTED, received_events.append)
    event_bus.publish(event)

    assert received_events == []


def test_event_factory_increments_sequence_no_per_process() -> None:
    event_factory = EventFactory(source=EventSource.MAIN)

    first_event = event_factory.create(
        event_type=EventType.SYSTEM_STARTED,
        timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
        symbol=None,
        payload=SystemStartedPayload(mode="mock"),
    )
    second_event = event_factory.create(
        event_type=EventType.ERROR_OCCURRED,
        timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
        symbol=None,
        payload=ErrorPayload(error_type="RuntimeError", message="failed"),
    )

    assert first_event.sequence_no == 1
    assert second_event.sequence_no == 2
    assert first_event.event_id != second_event.event_id


def test_event_rejects_string_event_type() -> None:
    with pytest.raises(TypeError):
        EventFactory(source=EventSource.MAIN).create(
            event_type="SystemStarted",  # type: ignore[arg-type]
            timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
            symbol=None,
            payload=SystemStartedPayload(mode="mock"),
        )

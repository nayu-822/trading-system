from datetime import datetime, timezone

from domain.enums import EventSource, EventType
from domain.events import BaseEvent
from infrastructure.event_bus import EventBus


def test_publish_delivers_event_to_subscriber() -> None:
    event_bus = EventBus()
    received_events: list[BaseEvent] = []
    event = BaseEvent(
        event_type=EventType.SYSTEM_STARTED,
        timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
        source=EventSource.MAIN,
        symbol=None,
        payload={"mode": "mock"},
        sequence_no=1,
    )

    event_bus.subscribe(EventType.SYSTEM_STARTED, received_events.append)
    event_bus.publish(event)

    assert received_events == [event]


def test_publish_ignores_unsubscribed_event_type() -> None:
    event_bus = EventBus()
    received_events: list[BaseEvent] = []
    event = BaseEvent(
        event_type=EventType.ERROR_OCCURRED,
        timestamp=datetime(2026, 4, 18, tzinfo=timezone.utc),
        source=EventSource.MAIN,
        symbol=None,
        payload={"message": "test"},
        sequence_no=1,
    )

    event_bus.subscribe(EventType.SYSTEM_STARTED, received_events.append)
    event_bus.publish(event)

    assert received_events == []

import logging
from collections import defaultdict
from collections.abc import Callable

from domain.events import BaseEvent
from domain.enums import EventType

EventHandler = Callable[[BaseEvent], None]


class EventBus:
    """同一プロセス内の publish / subscribe を提供する。"""

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._logger = logging.getLogger(__name__)

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """イベント購読ハンドラを登録する。

        Args:
            event_type: 購読するイベント種別。
            handler: イベント受信時に呼び出す関数。

        Returns:
            なし。
        """

        self._subscribers[event_type].append(handler)

    def publish(self, event: BaseEvent) -> None:
        """イベントを購読者へ同期的に配送する。

        Args:
            event: 配送対象イベント。

        Returns:
            なし。
        """

        handlers = list(self._subscribers.get(event.event_type, []))
        self._logger.debug("publish event_type=%s handlers=%s", event.event_type.value, len(handlers))
        for handler in handlers:
            handler(event)

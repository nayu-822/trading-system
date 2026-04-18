import logging
from collections import defaultdict
from collections.abc import Callable

from domain.enums import EventType
from domain.events import BaseEvent

EventHandler = Callable[[BaseEvent], None]


class EventBus:
    """同一プロセス内の同期 publish / subscribe を提供する。"""

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

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """イベント購読ハンドラを解除する。

        Args:
            event_type: 購読解除するイベント種別。
            handler: 解除対象の関数。

        Returns:
            なし。
        """

        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)

    def publish(self, event: BaseEvent) -> None:
        """イベントを購読者へ同期的に配送する。

        Args:
            event: 配送対象イベント。

        Returns:
            なし。
        """

        handlers = list(self._subscribers.get(event.event_type, []))
        self._logger.debug(
            "publish event_type=%s handlers=%s",
            event.event_type.value,
            len(handlers),
        )
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                self._logger.exception(
                    "event handler failed event_id=%s event_type=%s sequence_no=%s",
                    event.event_id,
                    event.event_type.value,
                    event.sequence_no,
                )
                raise

import logging
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Any

from domain.enums import EventType
from domain.events import BaseEvent
from infrastructure.event_bus import EventBus
from infrastructure.file_storage import FileStorage


@dataclass
class PersistenceProcess:
    """イベントを非同期に JSON Lines へ保存する。"""

    event_bus: EventBus
    storage: FileStorage
    event_log_path: Path
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    _queue: Queue[BaseEvent[Any] | None] = field(default_factory=Queue, init=False)
    _worker: Thread | None = field(default=None, init=False)

    def start(self) -> None:
        """永続化対象イベントの購読と worker を開始する。"""

        if self._worker is None:
            self._worker = Thread(target=self._run, daemon=True)
            self._worker.start()
        for event_type in self._event_types():
            self.event_bus.subscribe(event_type, self.enqueue)

    def stop(self) -> None:
        """worker を停止し、未保存イベントを処理しきる。"""

        if self._worker is None:
            return
        self._queue.put(None)
        self._queue.join()
        self._worker.join(timeout=5)
        self._worker = None

    def flush(self) -> None:
        """テストや終了処理でキューの処理完了を待つ。"""

        self._queue.join()

    def enqueue(self, event: BaseEvent[Any]) -> None:
        """イベントハンドラから保存キューへ積む。"""

        self._queue.put(event)

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            try:
                if event is None:
                    return
                self.storage.append_json_line(self.event_log_path, event.to_dict())
            except OSError:
                self.logger.exception("event persistence failed")
            finally:
                self._queue.task_done()

    def _event_types(self) -> tuple[EventType, ...]:
        return (
            EventType.MARKET_DATA_UPDATED,
            EventType.SIGNAL_DETECTED,
            EventType.ORDER_REQUESTED,
            EventType.ORDER_STATUS_UPDATED,
        )

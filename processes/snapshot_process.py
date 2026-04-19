import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from queue import Queue
from threading import Thread

from domain.enums import EventType
from domain.events import BaseEvent
from domain.snapshots import TradingStateSnapshot
from infrastructure.event_bus import EventBus
from infrastructure.file_storage import FileStorage
from processes.trading_process import TradingProcess


@dataclass
class SnapshotProcess:
    """trading_process の状態を非同期にスナップショット保存する。"""

    event_bus: EventBus
    trading_process: TradingProcess
    storage: FileStorage
    snapshot_path: Path
    event_threshold: int = 10
    interval_sec: int = 60
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    _event_count: int = 0
    _last_saved_at: datetime | None = None
    _queue: Queue[TradingStateSnapshot | None] = field(
        default_factory=Queue, init=False
    )
    _worker: Thread | None = field(default=None, init=False)

    def start(self) -> None:
        """スナップショット対象イベントの購読と worker を開始する。"""

        if self._worker is None:
            self._worker = Thread(target=self._run, daemon=True)
            self._worker.start()
        for event_type in self._event_types():
            self.event_bus.subscribe(event_type, self.handle_event)

    def stop(self) -> None:
        """worker を停止し、保存キューを処理しきる。"""

        if self._worker is None:
            return
        self._queue.put(None)
        self._queue.join()
        self._worker.join(timeout=5)
        self._worker = None

    def flush(self) -> None:
        """テストや終了処理でキューの処理完了を待つ。"""

        self._queue.join()

    def handle_event(self, event: BaseEvent) -> None:
        """イベント数または時間条件に応じて snapshot を保存キューへ積む。"""

        self._event_count += 1
        now = event.timestamp
        if self._should_save(now):
            self.save_snapshot(now)

    def save_snapshot(self, timestamp: datetime | None = None) -> None:
        """現在状態の snapshot を非同期保存キューへ積む。"""

        now = timestamp or datetime.now(timezone.utc)
        snapshot = self.trading_process.get_snapshot(now)
        self._queue.put(snapshot)
        self._event_count = 0
        self._last_saved_at = now

    def load_snapshot(self) -> TradingStateSnapshot | None:
        """保存済み snapshot を読み込む。壊れている場合はログ出力してスキップする。"""

        try:
            data = self.storage.read_json(self.snapshot_path)
            if data is None:
                return None
            return TradingStateSnapshot.from_dict(data)
        except (OSError, TypeError, ValueError, KeyError):
            self.logger.exception("snapshot load failed")
            return None

    def restore(self) -> bool:
        """保存済み snapshot があれば trading_process へ復元する。"""

        snapshot = self.load_snapshot()
        if snapshot is None:
            return False
        self.trading_process.restore_snapshot(snapshot)
        return True

    def _run(self) -> None:
        while True:
            snapshot = self._queue.get()
            try:
                if snapshot is None:
                    return
                self.storage.overwrite_json(self.snapshot_path, snapshot.to_dict())
            except OSError:
                self.logger.exception("snapshot persistence failed")
            finally:
                self._queue.task_done()

    def _should_save(self, now: datetime) -> bool:
        if self._event_count >= self.event_threshold:
            return True
        if self._last_saved_at is None:
            return False
        return now - self._last_saved_at >= timedelta(seconds=self.interval_sec)

    def _event_types(self) -> tuple[EventType, ...]:
        return (
            EventType.ORDER_REQUESTED,
            EventType.ORDER_STATUS_UPDATED,
            EventType.POSITION_UPDATED,
        )

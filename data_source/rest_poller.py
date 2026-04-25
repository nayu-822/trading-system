import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, Thread

from domain.enums import EventSource, EventType
from domain.events import EventFactory, OrderStatusPayload, OrderStatusUpdated
from domain.models import Order
from infrastructure.repositories.order_status_repository import OrderStatusRepository

OrderStatusHandler = Callable[[OrderStatusUpdated], None]


@dataclass
class RestPoller:
    """REST API の注文状態を定期取得し、イベントへ変換する。"""

    order_status_repository: OrderStatusRepository
    interval_sec: float
    on_event: OrderStatusHandler | None = None
    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.EXTERNAL_DATA)
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    _stop_event: Event = field(default_factory=Event, init=False)
    _worker: Thread | None = field(default=None, init=False)

    def start(self) -> None:
        """定期ポーリングを開始する。"""

        if self._worker is not None:
            return
        self._stop_event.clear()
        self._worker = Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        """定期ポーリングを停止する。"""

        if self._worker is None:
            return
        self._stop_event.set()
        self._worker.join(timeout=max(self.interval_sec, 1.0))
        self._worker = None

    def poll_once(self, force_refresh: bool = False) -> tuple[OrderStatusUpdated, ...]:
        """注文状態を1回取得して OrderStatusUpdated へ変換する。

        Args:
            force_refresh: True の場合はキャッシュを使わず再取得する。
        Returns:
            取得した注文状態イベントのタプル。
        """

        self.logger.info("order resync polling started")
        orders = self.order_status_repository.list_orders(force_refresh=force_refresh)
        events = tuple(self.to_event(order) for order in orders)
        self.logger.info("order resync polling completed count=%s", len(events))
        return events

    def to_event(self, order: Order) -> OrderStatusUpdated:
        """構造化済み注文状態をイベントへ変換する。

        Args:
            order: イベント化する注文状態。
        Returns:
            注文状態更新イベント。
        """

        return self.event_factory.create(
            event_type=EventType.ORDER_STATUS_UPDATED,
            timestamp=datetime.now(timezone.utc),
            symbol=order.symbol,
            payload=OrderStatusPayload(
                order_id=order.order_id,
                status=order.status,
                filled_quantity=order.filled_quantity,
                remaining_quantity=order.remaining_quantity,
                avg_price=order.avg_price,
                side=order.side,
                order_quantity=order.quantity,
                external_order_id=order.external_order_id,
            ),
        )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                for event in self.poll_once():
                    if self.on_event is not None:
                        self.on_event(event)
            except Exception:
                self.logger.exception("rest polling failed")
            self._stop_event.wait(self.interval_sec)

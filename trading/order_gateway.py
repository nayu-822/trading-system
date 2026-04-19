from datetime import datetime
from typing import Protocol

from domain.events import OrderStatusUpdated
from domain.models import Order


class OrderGateway(Protocol):
    """注文実行先を差し替えるための共通インターフェース。"""

    def place_order(
        self,
        order: Order,
        timestamp: datetime,
    ) -> tuple[OrderStatusUpdated, ...]:
        """注文を実行し、発生した注文状態イベントを返す。"""
        ...

    def cancel_order(
        self,
        order: Order,
        timestamp: datetime,
    ) -> tuple[OrderStatusUpdated, ...]:
        """注文取消を実行し、発生した注文状態イベントを返す。"""
        ...

from dataclasses import dataclass
from datetime import datetime

from domain.events import OrderStatusUpdated
from domain.models import Order


@dataclass
class LiveOrderGateway:
    """live 注文実装のための最小枠。現時点では実注文を送信しない。"""

    def place_order(
        self,
        order: Order,
        timestamp: datetime,
    ) -> tuple[OrderStatusUpdated, ...]:
        """実注文送信は未実装。"""

        raise NotImplementedError("live order gateway is not implemented")

    def cancel_order(
        self,
        order: Order,
        timestamp: datetime,
    ) -> tuple[OrderStatusUpdated, ...]:
        """実注文取消は未実装。"""

        raise NotImplementedError("live order cancel is not implemented")

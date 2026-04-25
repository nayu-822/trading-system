import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from data_source.kabu_api_client import KabuApiClient, KabuApiError
from domain.enums import OrderStatus
from domain.models import KabuOrderStatus, Order

INCOMPLETE_ORDER_STATUSES = {
    OrderStatus.NEW,
    OrderStatus.REQUESTED,
    OrderStatus.PARTIALLY_FILLED,
}


class OrderStatusRepositoryError(RuntimeError):
    """API注文状態の取得や変換に失敗した場合の例外。"""


@dataclass
class OrderStatusRepository:
    """API注文状態を取得してドメイン注文へ変換するリポジトリ。"""

    api_client: KabuApiClient
    token: str
    cache_ttl_sec: float = 1.0
    time_provider: Callable[[], float] = time.monotonic
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    _cache: tuple[float, tuple[Order, ...]] | None = field(default=None, init=False)

    def list_orders(
        self,
        symbol: str | None = None,
        force_refresh: bool = False,
    ) -> tuple[Order, ...]:
        """注文一覧を取得し、必要に応じて銘柄で絞り込んで返す。

        Args:
            symbol: 対象銘柄。未指定時は全注文を返す。
            force_refresh: True の場合はキャッシュを使わず再取得する。
        Returns:
            APIから取得した注文一覧。
        Raises:
            OrderStatusRepositoryError: API取得やレスポンス変換に失敗した場合。
        """

        orders = self._get_orders(force_refresh=force_refresh)
        if symbol is None:
            return orders
        return tuple(order for order in orders if order.symbol == symbol)

    def get_open_orders(
        self,
        symbol: str | None = None,
        force_refresh: bool = False,
    ) -> list[Order]:
        """未完了注文一覧を取得する。

        Args:
            symbol: 対象銘柄。未指定時は全銘柄を返す。
            force_refresh: True の場合はキャッシュを使わず再取得する。
        Returns:
            未完了注文のリスト。
        Raises:
            OrderStatusRepositoryError: API取得やレスポンス変換に失敗した場合。
        """

        return [
            order
            for order in self.list_orders(symbol=symbol, force_refresh=force_refresh)
            if order.status in INCOMPLETE_ORDER_STATUSES
        ]

    def get_order_status(
        self,
        order_id: str,
        force_refresh: bool = False,
    ) -> Order | None:
        """指定注文IDの注文状態を取得する。

        Args:
            order_id: 対象注文ID。
            force_refresh: True の場合はキャッシュを使わず再取得する。
        Returns:
            対象注文。見つからない場合は None。
        Raises:
            OrderStatusRepositoryError: API取得やレスポンス変換に失敗した場合。
        """

        for order in self.list_orders(force_refresh=force_refresh):
            if order.order_id == order_id or order.external_order_id == order_id:
                return order
        return None

    def clear_cache(self) -> None:
        """注文状態キャッシュを破棄する。

        Returns:
            なし。
        """

        self._cache = None

    def _get_orders(self, force_refresh: bool) -> tuple[Order, ...]:
        cached_orders = self._get_cached_orders(force_refresh=force_refresh)
        if cached_orders is not None:
            return cached_orders
        try:
            orders = tuple(
                self._to_order(order_status)
                for order_status in self.api_client.get_orders(token=self.token)
            )
        except (KabuApiError, ValueError, TypeError) as error:
            raise OrderStatusRepositoryError(
                f"failed to fetch order status from api: {error}"
            ) from error
        self._cache = (self.time_provider(), orders)
        for order in orders:
            self._log_order(order)
        return orders

    def _get_cached_orders(
        self,
        force_refresh: bool,
    ) -> tuple[Order, ...] | None:
        """TTL内の注文状態キャッシュを返す。

        Args:
            force_refresh: True の場合はキャッシュを無効化する。
        Returns:
            利用可能なキャッシュ。未保持またはTTL超過時は None。
        """

        if force_refresh or self._cache is None:
            return None
        cached_at, orders = self._cache
        if self.time_provider() - cached_at > self.cache_ttl_sec:
            return None
        return orders

    def _to_order(self, order_status: KabuOrderStatus) -> Order:
        """API注文状態をドメイン注文へ変換する。

        Args:
            order_status: APIから取得した注文状態。
        Returns:
            ドメイン注文モデル。
        Raises:
            OrderStatusRepositoryError: 必須項目不足や数量不整合がある場合。
        """

        if order_status.symbol is None or not order_status.symbol:
            raise OrderStatusRepositoryError("order response symbol is empty")
        if order_status.side is None:
            raise OrderStatusRepositoryError(
                f"order response side is empty: {order_status.order_id}"
            )
        if order_status.quantity < 1:
            raise OrderStatusRepositoryError(
                f"order quantity must be positive: {order_status.order_id}"
            )
        if order_status.filled_quantity > order_status.quantity:
            raise OrderStatusRepositoryError(
                f"filled quantity exceeds order quantity: {order_status.order_id}"
            )
        remaining_quantity = (
            order_status.remaining_quantity
            if order_status.remaining_quantity > 0
            else max(order_status.quantity - order_status.filled_quantity, 0)
        )
        return Order(
            order_id=order_status.order_id,
            external_order_id=order_status.external_order_id or order_status.order_id,
            symbol=order_status.symbol,
            side=order_status.side,
            quantity=order_status.quantity,
            order_type="MARKET",
            status=order_status.status,
            filled_quantity=order_status.filled_quantity,
            remaining_quantity=remaining_quantity,
            avg_price=order_status.avg_price,
        )

    def _log_order(self, order: Order) -> None:
        """注文状態同期ログを出力する。

        Args:
            order: ログ出力対象の注文。
        Returns:
            なし。
        """

        self.logger.info(
            "order status loaded source=api order_id=%s symbol=%s side=%s order_quantity=%s filled_quantity=%s remaining_quantity=%s average_fill_price=%s status=%s",
            order.external_order_id or order.order_id,
            order.symbol,
            order.side.value,
            order.quantity,
            order.filled_quantity,
            order.remaining_quantity,
            order.avg_price,
            order.status.value,
        )

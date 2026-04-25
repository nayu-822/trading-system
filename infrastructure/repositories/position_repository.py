import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from data_source.kabu_api_client import KabuApiClient, KabuApiError
from domain.enums import OrderSide
from domain.models import Position


class PositionRepositoryError(ValueError):
    """建玉取得に失敗した場合の例外。"""


TimeProvider = Callable[[], float]


@dataclass
class PositionRepository:
    """kabuステーションAPIから建玉を取得する。"""

    api_client: KabuApiClient
    token: str
    cache_ttl_sec: float = 1.0
    time_provider: TimeProvider = time.monotonic
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    _cache: dict[str, tuple[float, Position]] = field(default_factory=dict)

    def get_position(self, symbol: str) -> Position:
        """指定銘柄の建玉を取得する。

        Args:
            symbol: 取得対象の銘柄コード。

        Returns:
            Position: API応答から変換した建玉。

        Raises:
            PositionRepositoryError: 建玉取得に失敗した場合。
        """

        cached = self._get_cached_position(symbol)
        if cached is not None:
            self._log_position(cached, source="cache")
            return cached

        try:
            positions = self.api_client.get_positions(token=self.token, symbol=symbol)
        except (KabuApiError, ValueError) as error:
            raise PositionRepositoryError(
                f"failed to fetch position from api: {symbol}"
            ) from error

        if not positions:
            raise PositionRepositoryError(f"position response is empty: {symbol}")

        position = _merge_positions(symbol, positions)
        if position.symbol != symbol:
            raise PositionRepositoryError(f"position symbol mismatch: {symbol}")

        self._cache[symbol] = (self.time_provider() + self.cache_ttl_sec, position)
        self._log_position(position, source="api")
        return position

    def _get_cached_position(self, symbol: str) -> Position | None:
        cached = self._cache.get(symbol)
        if cached is None:
            return None
        expires_at, position = cached
        if self.time_provider() > expires_at:
            del self._cache[symbol]
            return None
        return position

    def _log_position(self, position: Position, source: str) -> None:
        side = _resolve_position_side(position)
        self.logger.info(
            "position fetched symbol=%s side=%s quantity=%s source=%s",
            position.symbol,
            side,
            abs(position.quantity),
            source,
        )


def _merge_positions(symbol: str, positions: tuple[Position, ...]) -> Position:
    total_quantity = sum(position.quantity for position in positions)
    if total_quantity == 0:
        raise PositionRepositoryError(f"position response is empty: {symbol}")

    total_cost = sum(
        abs(position.quantity) * position.average_price for position in positions
    )
    return Position(
        symbol=symbol,
        quantity=total_quantity,
        average_price=total_cost / sum(abs(position.quantity) for position in positions),
    )


def _resolve_position_side(position: Position) -> str:
    if position.quantity > 0:
        return OrderSide.BUY.value
    if position.quantity < 0:
        return OrderSide.SELL.value
    return "NONE"

import logging
from dataclasses import dataclass, field

from domain.enums import OrderSide
from domain.models import Position
from infrastructure.repositories.position_repository import (
    PositionRepository,
    PositionRepositoryError,
)


class PositionReconciliationError(ValueError):
    """建玉突合で不整合または取得失敗を検知した場合の例外。"""


@dataclass(frozen=True)
class PositionReconciliationResult:
    """建玉突合結果を表す。"""

    symbol: str
    api_position: Position
    internal_position: Position
    matched: bool
    mismatch_reason: str | None = None


@dataclass
class PositionReconciliationService:
    """API実建玉と内部建玉の差異を検知する。"""

    position_repository: PositionRepository
    average_price_tolerance: float = 0.01
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def reconcile(
        self,
        symbol: str,
        internal_position: Position | None,
    ) -> PositionReconciliationResult:
        """指定銘柄のAPI実建玉と内部建玉を突合する。

        Args:
            symbol: 突合対象の銘柄コード。
            internal_position: 内部で保持している建玉。未保有時は None を許可する。

        Returns:
            PositionReconciliationResult: 突合結果。

        Raises:
            PositionReconciliationError: API建玉取得失敗または突合不一致の場合。
        """

        normalized_internal = self._normalize_internal_position(
            symbol=symbol,
            internal_position=internal_position,
        )
        try:
            api_position = self.position_repository.get_position(symbol)
        except PositionRepositoryError as error:
            self._log_result(
                symbol=symbol,
                api_position=None,
                internal_position=normalized_internal,
                result="error",
                mismatch_reason="api_position_unavailable",
            )
            raise PositionReconciliationError(
                f"failed to fetch api position: {symbol}"
            ) from error

        mismatch_reason = self._detect_mismatch(
            api_position=api_position,
            internal_position=normalized_internal,
        )
        result = PositionReconciliationResult(
            symbol=symbol,
            api_position=api_position,
            internal_position=normalized_internal,
            matched=mismatch_reason is None,
            mismatch_reason=mismatch_reason,
        )
        self._log_result(
            symbol=symbol,
            api_position=api_position,
            internal_position=normalized_internal,
            result="matched" if result.matched else "mismatched",
            mismatch_reason=mismatch_reason,
        )
        if mismatch_reason is not None:
            raise PositionReconciliationError(
                f"position mismatch detected: {symbol} reason={mismatch_reason}"
            )
        return result

    def _normalize_internal_position(
        self,
        symbol: str,
        internal_position: Position | None,
    ) -> Position:
        """内部建玉を比較用の安全な値へ正規化する。

        Args:
            symbol: 対象銘柄コード。
            internal_position: 内部建玉。

        Returns:
            Position: 比較用に正規化した内部建玉。
        """

        if internal_position is None:
            return Position(symbol=symbol, quantity=0, average_price=0.0)
        return Position(
            symbol=symbol,
            quantity=internal_position.quantity,
            average_price=internal_position.average_price,
        )

    def _detect_mismatch(
        self,
        api_position: Position,
        internal_position: Position,
    ) -> str | None:
        """API建玉と内部建玉の差異理由を判定する。

        Args:
            api_position: APIから取得した実建玉。
            internal_position: 内部建玉。

        Returns:
            str | None: 不一致理由。一致時は None。
        """

        if api_position.symbol != internal_position.symbol:
            return "symbol_mismatch"
        if self._resolve_side(api_position) != self._resolve_side(internal_position):
            return "side_mismatch"
        if api_position.quantity != internal_position.quantity:
            return "quantity_mismatch"
        if (
            abs(api_position.average_price - internal_position.average_price)
            > self.average_price_tolerance
        ):
            return "average_price_mismatch"
        return None

    def _log_result(
        self,
        symbol: str,
        api_position: Position | None,
        internal_position: Position,
        result: str,
        mismatch_reason: str | None,
    ) -> None:
        """突合結果をログ出力する。

        Args:
            symbol: 対象銘柄コード。
            api_position: API実建玉。取得失敗時は None。
            internal_position: 内部建玉。
            result: 判定結果。
            mismatch_reason: 不一致理由。

        Returns:
            なし。
        """

        log_method = self.logger.info if result == "matched" else self.logger.error
        log_method(
            "position reconciliation symbol=%s api_side=%s internal_side=%s api_quantity=%s internal_quantity=%s api_average_price=%s internal_average_price=%s result=%s mismatch_reason=%s",
            symbol,
            self._resolve_side(api_position),
            self._resolve_side(internal_position),
            0 if api_position is None else api_position.quantity,
            internal_position.quantity,
            None if api_position is None else api_position.average_price,
            internal_position.average_price,
            result,
            mismatch_reason or "",
        )

    def _resolve_side(self, position: Position | None) -> str:
        """建玉数量から売買方向文字列を返す。

        Args:
            position: 判定対象の建玉。

        Returns:
            str: BUY / SELL / NONE のいずれか。
        """

        if position is None or position.quantity == 0:
            return "NONE"
        if position.quantity > 0:
            return OrderSide.BUY.value
        return OrderSide.SELL.value

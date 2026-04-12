from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any
from uuid import uuid4


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaperOrder:
    symbol: str
    strategy: str
    side: str
    quantity: int
    price: float


@dataclass(frozen=True)
class PaperExecutionResult:
    order_id: str
    symbol: str
    strategy: str
    side: str
    quantity: int
    price: float
    status: str
    executed_at: str


class PaperExecutor:
    """
    実発注を行わず、注文内容を擬似約定として処理する実行クラス。
    """

    def execute(self, order: PaperOrder) -> PaperExecutionResult:
        """
        注文内容を受け取り、ログ出力ベースの擬似約定結果を返す。

        引数:
            order: 擬似約定対象の注文情報

        戻り値:
            PaperExecutionResult: 擬似約定として扱う実行結果
        """
        self._validate_order(order=order)

        execution_result = PaperExecutionResult(
            order_id=self._generate_order_id(),
            symbol=order.symbol,
            strategy=order.strategy,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
            status="filled",
            executed_at=self._generate_executed_at(),
        )

        self._log_execution(execution_result=execution_result)
        return execution_result

    def to_log_record(self, execution_result: PaperExecutionResult) -> dict[str, Any]:
        """
        擬似約定結果をログ保存しやすい辞書形式へ変換する。

        引数:
            execution_result: 擬似約定結果

        戻り値:
            dict[str, Any]: ログ保存用の辞書データ
        """
        return {
            "order_id": execution_result.order_id,
            "symbol": execution_result.symbol,
            "strategy": execution_result.strategy,
            "side": execution_result.side,
            "quantity": execution_result.quantity,
            "price": execution_result.price,
            "status": execution_result.status,
            "executed_at": execution_result.executed_at,
        }

    def _validate_order(self, order: PaperOrder) -> None:
        """
        擬似約定に必要な注文情報の妥当性を検証する。

        引数:
            order: 検証対象の注文情報

        戻り値:
            なし
        """
        if not order.symbol:
            message = "symbol is required"
            logger.error(message)
            raise ValueError(message)

        if not order.strategy:
            message = "strategy is required"
            logger.error(message)
            raise ValueError(message)

        if order.side not in {"buy", "sell"}:
            message = "side must be either 'buy' or 'sell'"
            logger.error(message)
            raise ValueError(message)

        if order.quantity <= 0:
            message = "quantity must be greater than zero"
            logger.error(message)
            raise ValueError(message)

        if order.price <= 0:
            message = "price must be greater than zero"
            logger.error(message)
            raise ValueError(message)

    def _generate_order_id(self) -> str:
        """
        擬似約定用の注文 ID を生成する。

        引数:
            なし

        戻り値:
            str: 一意な注文 ID
        """
        return f"paper-{uuid4().hex}"

    def _generate_executed_at(self) -> str:
        """
        擬似約定時刻を ISO 8601 形式で生成する。

        引数:
            なし

        戻り値:
            str: UTC ベースの擬似約定時刻
        """
        return datetime.now(timezone.utc).isoformat()

    def _log_execution(self, execution_result: PaperExecutionResult) -> None:
        """
        擬似約定結果を取引ログ向けに出力する。

        引数:
            execution_result: ログ出力対象の擬似約定結果

        戻り値:
            なし
        """
        logger.info(
            "paper execution filled: order_id=%s symbol=%s strategy=%s side=%s quantity=%s price=%s executed_at=%s",
            execution_result.order_id,
            execution_result.symbol,
            execution_result.strategy,
            execution_result.side,
            execution_result.quantity,
            execution_result.price,
            execution_result.executed_at,
        )

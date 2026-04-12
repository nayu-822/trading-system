from __future__ import annotations

from typing import Protocol

from app.domain.models import ExecutionResult, Order


class Executor(Protocol):
    """
    注文実行処理の共通インターフェース。
    """

    def execute(self, order: Order) -> ExecutionResult:
        """
        注文情報を実行し、結果を返す。
        引数:
            order: 実行対象の注文情報

        戻り値:
            ExecutionResult: 実行結果
        """
        ...

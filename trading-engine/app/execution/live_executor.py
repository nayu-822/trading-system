from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.models import ExecutionResult, Order
from app.execution.kabus_api_client import KabuApiClientConfig, KabuStationApiClient


class LiveOrderSender(Protocol):
    """
    live 注文送信の差し替えポイントを表すインターフェース。
    """

    def send(self, order: Order) -> ExecutionResult:
        """
        live 注文を送信し、約定結果を返す。
        """
        ...


@dataclass(frozen=True)
class LiveExecutionConfig:
    """
    live 実行に必要な接続設定を保持する。
    """

    host: str = "localhost"
    port: int = 18080
    timeout_seconds: float = 10.0
    exchange: int | None = None
    api_password: str | None = None
    api_token: str | None = None


@dataclass(frozen=True)
class KabuLiveOrderSender:
    """
    kabuステーション API へ接続する live 注文送信の土台実装。
    """

    config: LiveExecutionConfig
    client: KabuStationApiClient | None = None

    def send(self, order: Order) -> ExecutionResult:
        """
        live 注文送信を行う。
        """
        if self.config.exchange is None:
            raise ValueError("exchange is required for live execution")

        client = self.client or KabuStationApiClient(
            config=KabuApiClientConfig(
                host=self.config.host,
                port=self.config.port,
                timeout_seconds=self.config.timeout_seconds,
            )
        )

        if self.config.api_token:
            client.set_token(self.config.api_token)
        elif self.config.api_password:
            client.authenticate(self.config.api_password)
        else:
            raise ValueError("api_password or api_token is required for live execution")

        raise NotImplementedError("live order payload mapping is not implemented")


@dataclass(frozen=True)
class LiveExecutor:
    """
    live 注文送信を委譲する executor。
    """

    order_sender: LiveOrderSender

    def execute(self, order: Order) -> ExecutionResult:
        """
        live 注文送信を実行する。
        """
        self._validate_order(order=order)
        return self.order_sender.send(order)

    def _validate_order(self, order: Order) -> None:
        """
        live 注文の最小限の安全チェックを行う。
        """
        if not order.symbol:
            raise ValueError("symbol is required")

        if order.side not in {"buy", "sell"}:
            raise ValueError("side must be either 'buy' or 'sell'")

        if order.quantity <= 0:
            raise ValueError("quantity must be greater than zero")

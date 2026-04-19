import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib import request

from domain.enums import OrderStatus
from domain.models import KabuApiConfig, KabuOrderStatus

HttpRequest = Callable[[str, str, Mapping[str, str], bytes | None, int], bytes]


class KabuApiError(Exception):
    """kabuステーション API 呼び出し失敗。"""


@dataclass
class KabuApiClient:
    """kabuステーション REST API 呼び出しを集約する。"""

    config: KabuApiConfig
    http_request: HttpRequest | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def get_token(self) -> str:
        """環境変数から API パスワードを取得し、認証トークンを取得する。"""

        api_password = os.environ.get(self.config.token_env_name)
        if not api_password:
            message = f"API password env is not set: {self.config.token_env_name}"
            self.logger.error(message)
            raise KabuApiError(message)
        try:
            response = self._request_json(
                method="POST",
                path="/token",
                headers={"Content-Type": "application/json"},
                body={"APIPassword": api_password},
            )
            token = response.get("Token")
            if not isinstance(token, str) or not token:
                raise KabuApiError("token response does not contain Token")
            return token
        except Exception as error:
            self.logger.exception("kabu token request failed")
            if isinstance(error, KabuApiError):
                raise
            raise KabuApiError("kabu token request failed") from error

    def get_orders(self, token: str) -> tuple[KabuOrderStatus, ...]:
        """注文状態を取得し、構造化モデルへ変換する。"""

        try:
            response = self._request_json(
                method="GET",
                path="/orders",
                headers={"X-API-KEY": token},
                body=None,
            )
            if isinstance(response, list):
                orders = response
            elif isinstance(response, Mapping):
                orders = response.get("Orders", response.get("orders", response))
            else:
                raise KabuApiError("orders response must be object or list")
            if not isinstance(orders, list):
                raise KabuApiError("orders response must be list")
            return tuple(self._to_order_status(order) for order in orders)
        except Exception as error:
            self.logger.exception("kabu orders request failed")
            if isinstance(error, KabuApiError):
                raise
            raise KabuApiError("kabu orders request failed") from error

    def _request_json(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: Mapping[str, Any] | None,
    ) -> Any:
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        executor = self.http_request or _default_http_request
        raw_response = executor(
            method,
            f"{self.config.base_url.rstrip('/')}{path}",
            headers,
            payload,
            self.config.timeout_sec,
        )
        return json.loads(raw_response.decode("utf-8"))

    def _to_order_status(self, data: Mapping[str, Any]) -> KabuOrderStatus:
        return KabuOrderStatus(
            order_id=str(_pick(data, "order_id", "OrderID", "ID", "id")),
            symbol=_optional_str(_pick_optional(data, "symbol", "Symbol")),
            status=_to_order_status(_pick_optional(data, "status", "Status", "State")),
            filled_quantity=_to_int(
                _pick_optional(data, "filled_quantity", "FilledQuantity", "CumQty")
            ),
            remaining_quantity=_to_int(
                _pick_optional(
                    data, "remaining_quantity", "RemainingQuantity", "LeavesQty"
                )
            ),
            avg_price=_to_optional_float(
                _pick_optional(data, "avg_price", "AvgPrice", "Price")
            ),
        )


def _default_http_request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_sec: int,
) -> bytes:
    api_request = request.Request(
        url=url,
        data=body,
        headers=dict(headers),
        method=method,
    )
    with request.urlopen(api_request, timeout=timeout_sec) as response:
        return response.read()


def _pick(data: Mapping[str, Any], *keys: str) -> Any:
    value = _pick_optional(data, *keys)
    if value is None:
        raise KabuApiError(f"required response field is missing: {keys[0]}")
    return value


def _pick_optional(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _to_order_status(value: Any) -> OrderStatus:
    status_map = {
        "NEW": OrderStatus.NEW,
        "REQUESTED": OrderStatus.REQUESTED,
        "PARTIAL": OrderStatus.PARTIALLY_FILLED,
        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
        "FILLED": OrderStatus.FILLED,
        "CANCELED": OrderStatus.CANCELED,
        "REJECTED": OrderStatus.REJECTED,
        "1": OrderStatus.NEW,
        "2": OrderStatus.REQUESTED,
        "3": OrderStatus.REQUESTED,
        "4": OrderStatus.PARTIALLY_FILLED,
        "5": OrderStatus.FILLED,
    }
    key = str(value or "NEW").upper()
    return status_map.get(key, OrderStatus.NEW)

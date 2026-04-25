import json
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib import request

from domain.enums import OrderSide, OrderStatus
from domain.models import (
    KabuApiConfig,
    KabuOrderRequest,
    KabuOrderResult,
    KabuOrderStatus,
    Position,
)

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

    def get_orders(
        self,
        token: str,
        symbol: str | None = None,
        order_id: str | None = None,
    ) -> tuple[KabuOrderStatus, ...]:
        """注文状態を取得し、必要条件で絞り込んで返す。

        Args:
            token: kabuステーションAPIの認証トークン。
            symbol: 対象銘柄。未指定時は全銘柄を返す。
            order_id: 対象注文ID。未指定時は全注文を返す。
        Returns:
            APIレスポンスを変換した注文状態のタプル。
        Raises:
            KabuApiError: API取得やレスポンス変換に失敗した場合。
        """

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
            converted = tuple(self._to_order_status(order) for order in orders)
            return tuple(
                current_order
                for current_order in converted
                if (symbol is None or current_order.symbol == symbol)
                and (
                    order_id is None
                    or current_order.order_id == order_id
                    or current_order.external_order_id == order_id
                )
            )
        except Exception as error:
            self.logger.exception(
                "kabu orders request failed symbol=%s order_id=%s",
                symbol or "",
                order_id or "",
            )
            if isinstance(error, KabuApiError):
                raise
            raise KabuApiError("kabu orders request failed") from error

    def get_positions(
        self,
        token: str,
        symbol: str | None = None,
    ) -> tuple[Position, ...]:
        """建玉一覧を取得し、構造化モデルへ変換する。"""

        try:
            response = self._request_json(
                method="GET",
                path="/positions",
                headers={"X-API-KEY": token},
                body=None,
            )
            if isinstance(response, list):
                positions = response
            elif isinstance(response, Mapping):
                positions = response.get("Positions", response.get("positions", response))
            else:
                raise KabuApiError("positions response must be object or list")
            if not isinstance(positions, list):
                raise KabuApiError("positions response must be list")
            converted = tuple(self._to_position(position) for position in positions)
            if symbol is None:
                return converted
            return tuple(position for position in converted if position.symbol == symbol)
        except Exception as error:
            self.logger.exception("kabu positions request failed symbol=%s", symbol or "")
            if isinstance(error, KabuApiError):
                raise
            raise KabuApiError("kabu positions request failed") from error

    def send_order(
        self,
        token: str,
        order_request: KabuOrderRequest,
    ) -> KabuOrderResult:
        """成行注文を送信し、API 応答を構造化モデルへ変換する。"""

        try:
            response = self._request_json(
                method="POST",
                path="/sendorder",
                headers={
                    "Content-Type": "application/json",
                    "X-API-KEY": token,
                },
                body=_to_send_order_body(order_request),
            )
            if not isinstance(response, Mapping):
                raise KabuApiError("send order response must be object")
            return _to_order_result(order_request=order_request, data=response)
        except Exception as error:
            self.logger.exception(
                "kabu send order failed order_id=%s symbol=%s",
                order_request.order_id,
                order_request.symbol,
            )
            if isinstance(error, KabuApiError):
                raise
            raise KabuApiError("kabu send order failed") from error

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
        order_id = str(_pick(data, "order_id", "OrderID", "ID", "id"))
        filled_quantity = _to_int(
            _pick_optional(data, "filled_quantity", "FilledQuantity", "CumQty")
        )
        remaining_quantity = _to_int(
            _pick_optional(data, "remaining_quantity", "RemainingQuantity", "LeavesQty")
        )
        quantity = _to_int(
            _pick_optional(data, "quantity", "OrderQty", "Qty", "OrderQuantity")
        )
        return KabuOrderStatus(
            order_id=order_id,
            symbol=_optional_str(_pick_optional(data, "symbol", "Symbol")),
            side=_to_optional_order_side(_pick_optional(data, "side", "Side")),
            quantity=quantity or filled_quantity + remaining_quantity,
            status=_to_order_status(_pick_optional(data, "status", "Status", "State")),
            filled_quantity=filled_quantity,
            remaining_quantity=remaining_quantity,
            avg_price=_to_optional_float(
                _pick_optional(data, "avg_price", "AvgPrice", "Price")
            ),
            external_order_id=_optional_str(
                _pick_optional(data, "external_order_id", "ExternalOrderID")
            )
            or order_id,
        )

    def _to_position(self, data: Mapping[str, Any]) -> Position:
        symbol = str(_pick(data, "symbol", "Symbol"))
        side = _pick(data, "side", "Side")
        quantity = _to_signed_quantity(
            side=side,
            quantity=_pick(
                data,
                "quantity",
                "Quantity",
                "Qty",
                "HoldQty",
                "LeavesQty",
            ),
        )
        average_price = _to_optional_float(
            _pick_optional(
                data,
                "avg_price",
                "AvgPrice",
                "Price",
                "HoldPrice",
            )
        )
        return Position(
            symbol=symbol,
            quantity=quantity,
            average_price=average_price or 0.0,
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


def _to_send_order_body(order_request: KabuOrderRequest) -> Mapping[str, Any]:
    order_type = order_request.order_type.upper()
    if order_type != "MARKET":
        raise KabuApiError(f"unsupported order_type={order_request.order_type}")
    if order_request.quantity <= 0:
        raise KabuApiError("order quantity must be positive")
    return {
        "Symbol": order_request.symbol,
        "Side": _to_kabu_side(order_request.side),
        "Qty": order_request.quantity,
        "OrdType": order_type,
        "Price": order_request.price,
    }


def _to_kabu_side(side: OrderSide) -> str:
    if side == OrderSide.BUY:
        return "BUY"
    if side == OrderSide.SELL:
        return "SELL"
    raise KabuApiError(f"unsupported order side={side.value}")


def _to_order_result(
    order_request: KabuOrderRequest,
    data: Mapping[str, Any],
) -> KabuOrderResult:
    status_value = _pick_optional(data, "status", "Status", "State")
    remaining_value = _pick_optional(
        data,
        "remaining_quantity",
        "RemainingQuantity",
        "LeavesQty",
    )
    return KabuOrderResult(
        order_id=str(
            _pick_optional(data, "order_id", "OrderID", "ID", "id")
            or order_request.order_id
        ),
        symbol=str(_pick_optional(data, "symbol", "Symbol") or order_request.symbol),
        status=(
            _to_order_status(status_value)
            if status_value is not None
            else OrderStatus.REQUESTED
        ),
        filled_quantity=_to_int(
            _pick_optional(data, "filled_quantity", "FilledQuantity", "CumQty")
        ),
        remaining_quantity=(
            _to_int(remaining_value)
            if remaining_value is not None
            else order_request.quantity
        ),
        avg_price=_to_optional_float(
            _pick_optional(data, "avg_price", "AvgPrice", "Price")
        ),
    )


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


def _to_signed_quantity(side: Any, quantity: Any) -> int:
    normalized_side = str(side).upper()
    normalized_quantity = int(quantity)
    if normalized_side in {"BUY", "LONG", "2"}:
        return normalized_quantity
    if normalized_side in {"SELL", "SHORT", "1"}:
        return -normalized_quantity
    raise KabuApiError(f"unsupported position side={side}")


def _to_order_status(value: Any) -> OrderStatus:
    status_map = {
        "NEW": OrderStatus.NEW,
        "REQUESTED": OrderStatus.REQUESTED,
        "PARTIAL": OrderStatus.PARTIALLY_FILLED,
        "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
        "FILLED": OrderStatus.FILLED,
        "CANCELED": OrderStatus.CANCELED,
        "CANCELLED": OrderStatus.CANCELED,
        "EXPIRED": OrderStatus.EXPIRED,
        "FAILED": OrderStatus.FAILED,
        "REJECTED": OrderStatus.REJECTED,
        "1": OrderStatus.NEW,
        "2": OrderStatus.REQUESTED,
        "3": OrderStatus.REQUESTED,
        "4": OrderStatus.PARTIALLY_FILLED,
        "5": OrderStatus.FILLED,
        "6": OrderStatus.CANCELED,
        "7": OrderStatus.EXPIRED,
        "8": OrderStatus.FAILED,
    }
    if value is None:
        raise KabuApiError("order status is missing")
    key = str(value).strip().upper()
    if not key:
        raise KabuApiError("order status is empty")
    if key not in status_map:
        raise KabuApiError(f"unsupported order status={value}")
    return status_map[key]


def _to_optional_order_side(value: Any) -> OrderSide | None:
    if value is None:
        return None
    key = str(value).upper()
    if key in {"BUY", "2"}:
        return OrderSide.BUY
    if key in {"SELL", "1"}:
        return OrderSide.SELL
    raise KabuApiError(f"unsupported order side={value}")

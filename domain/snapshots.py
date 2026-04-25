from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from domain.enums import OrderSide, OrderStatus, SignalType, StrategyType


@dataclass(frozen=True)
class BaseSnapshot:
    """スナップショット共通メタ情報。"""

    version: int
    created_at: datetime
    updated_at: datetime
    sequence_no: int

    def to_dict(self) -> dict[str, Any]:
        """保存用の辞書へ変換する。"""

        return {
            item.name: _serialize_value(getattr(self, item.name))
            for item in fields(self)
        }


@dataclass(frozen=True)
class SignalSnapshot(BaseSnapshot):
    """signal_process の復元に必要な最小状態。"""

    symbol: str
    last_signal_type: SignalType | None
    strategy_type: StrategyType


@dataclass(frozen=True)
class TradingSnapshot(BaseSnapshot):
    """trading_process の復元に必要な最小状態。"""

    symbol: str
    position_quantity: int
    avg_price: float
    current_lot: int


@dataclass(frozen=True)
class OrderSnapshot:
    """復旧に必要な注文状態。"""

    order_id: str
    external_order_id: str | None
    symbol: str
    side: OrderSide
    quantity: int
    order_type: str
    status: OrderStatus
    price: float | None
    filled_quantity: int
    remaining_quantity: int
    avg_price: float | None
    is_exit: bool = False
    reflected_filled_quantity: int = 0


@dataclass(frozen=True)
class TradeHistorySnapshot:
    """スナップショットに保存する取引履歴。"""

    order_id: str
    symbol: str
    side: OrderSide
    is_exit: bool
    filled_quantity: int
    fill_price: float
    average_fill_price: float
    filled_at: datetime
    status: OrderStatus
    source: str


@dataclass(frozen=True)
class PositionStateSnapshot:
    """復旧に必要な建玉状態。"""

    symbol: str
    quantity: int
    average_price: float


@dataclass(frozen=True)
class TradingSymbolStateSnapshot:
    """銘柄単位の売買状態。"""

    symbol: str
    lot_size: int
    position: PositionStateSnapshot
    orders: tuple[OrderSnapshot, ...]
    trade_histories: tuple[TradeHistorySnapshot, ...] = ()


@dataclass(frozen=True)
class RiskControlSnapshot:
    """RiskManager の復元に必要な最小状態。"""

    consecutive_losses: int
    consecutive_wins: int
    max_equity: float
    current_equity: float
    kill_switch_active: bool
    stopped_by_losses: bool
    daily_realized_loss: float
    api_error_count: int
    business_date: str | None


@dataclass(frozen=True)
class TradingStateSnapshot(BaseSnapshot):
    """trading_process 全体の復旧用スナップショット。"""

    symbols: tuple[TradingSymbolStateSnapshot, ...]
    risk_state: RiskControlSnapshot | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TradingStateSnapshot":
        """保存済み JSON 由来の辞書から復元する。"""

        symbols_data = data["symbols"]
        if not isinstance(symbols_data, list):
            raise TypeError("symbols must be list")
        return cls(
            version=int(data["version"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
            sequence_no=int(data["sequence_no"]),
            symbols=tuple(_symbol_snapshot_from_dict(item) for item in symbols_data),
            risk_state=_risk_snapshot_from_dict(data.get("risk_state")),
        )


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {
            item.name: _serialize_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


def _symbol_snapshot_from_dict(data: Mapping[str, Any]) -> TradingSymbolStateSnapshot:
    position_data = data["position"]
    orders_data = data["orders"]
    trade_histories_data = data.get("trade_histories", [])
    if not isinstance(position_data, Mapping):
        raise TypeError("position must be object")
    if not isinstance(orders_data, list):
        raise TypeError("orders must be list")
    if not isinstance(trade_histories_data, list):
        raise TypeError("trade_histories must be list")
    return TradingSymbolStateSnapshot(
        symbol=str(data["symbol"]),
        lot_size=int(data["lot_size"]),
        position=PositionStateSnapshot(
            symbol=str(position_data["symbol"]),
            quantity=int(position_data["quantity"]),
            average_price=float(position_data["average_price"]),
        ),
        orders=tuple(_order_snapshot_from_dict(item) for item in orders_data),
        trade_histories=tuple(
            _trade_history_snapshot_from_dict(item) for item in trade_histories_data
        ),
    )


def _order_snapshot_from_dict(data: Mapping[str, Any]) -> OrderSnapshot:
    return OrderSnapshot(
        order_id=str(data["order_id"]),
        external_order_id=(
            str(data["external_order_id"])
            if data.get("external_order_id") is not None
            else None
        ),
        symbol=str(data["symbol"]),
        side=OrderSide(str(data["side"])),
        quantity=int(data["quantity"]),
        order_type=str(data["order_type"]),
        is_exit=bool(data.get("is_exit", False)),
        status=OrderStatus(str(data["status"])),
        price=float(data["price"]) if data.get("price") is not None else None,
        filled_quantity=int(data["filled_quantity"]),
        remaining_quantity=int(data["remaining_quantity"]),
        avg_price=(
            float(data["avg_price"]) if data.get("avg_price") is not None else None
        ),
        reflected_filled_quantity=int(data.get("reflected_filled_quantity", 0)),
    )


def _trade_history_snapshot_from_dict(data: Mapping[str, Any]) -> TradeHistorySnapshot:
    return TradeHistorySnapshot(
        order_id=str(data["order_id"]),
        symbol=str(data["symbol"]),
        side=OrderSide(str(data["side"])),
        is_exit=bool(data["is_exit"]),
        filled_quantity=int(data["filled_quantity"]),
        fill_price=float(data["fill_price"]),
        average_fill_price=float(data["average_fill_price"]),
        filled_at=datetime.fromisoformat(str(data["filled_at"])),
        status=OrderStatus(str(data["status"])),
        source=str(data["source"]),
    )


def _risk_snapshot_from_dict(value: Any) -> RiskControlSnapshot | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError("risk_state must be object")
    return RiskControlSnapshot(
        consecutive_losses=int(value["consecutive_losses"]),
        consecutive_wins=int(value["consecutive_wins"]),
        max_equity=float(value["max_equity"]),
        current_equity=float(value["current_equity"]),
        kill_switch_active=bool(value["kill_switch_active"]),
        stopped_by_losses=bool(value["stopped_by_losses"]),
        daily_realized_loss=float(value.get("daily_realized_loss", 0.0)),
        api_error_count=int(value.get("api_error_count", 0)),
        business_date=(
            str(value["business_date"])
            if value.get("business_date") is not None
            else None
        ),
    )

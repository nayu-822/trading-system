from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from types import UnionType
from typing import Any, ClassVar, Generic, TypeVar, get_args, get_origin
from uuid import UUID, uuid4

from domain.enums import (
    EventSource,
    EventType,
    OrderSide,
    OrderStatus,
    SignalType,
    StrategyType,
)
from domain.models import IndicatorValue

PayloadT = TypeVar("PayloadT")


@dataclass(frozen=True)
class EmptyPayload:
    """詳細情報を持たないイベントの payload。"""


@dataclass(frozen=True)
class SystemStartedPayload:
    """システム起動イベントの payload。"""

    mode: str


@dataclass(frozen=True)
class MarketDataPayload:
    """市場データ更新イベントの payload。"""

    price: float
    bid: float | None
    ask: float | None
    volume: int | None
    timestamp: datetime


@dataclass(frozen=True)
class SignalPayload:
    """シグナル検知イベントの payload。"""

    signal_type: SignalType
    strategy_type: StrategyType
    confidence: float | None = None
    indicators: tuple[IndicatorValue, ...] = ()


@dataclass(frozen=True)
class OrderPayload:
    """発注要求イベントの payload。"""

    symbol: str
    side: OrderSide
    quantity: int
    order_type: str
    price: float | None = None


@dataclass(frozen=True)
class OrderStatusPayload:
    """注文状態更新イベントの payload。"""

    order_id: str
    status: OrderStatus
    filled_quantity: int
    remaining_quantity: int
    avg_price: float | None


@dataclass(frozen=True)
class PositionPayload:
    """建玉更新イベントの payload。"""

    symbol: str
    quantity: int
    avg_price: float
    realized_pnl: float | None
    unrealized_pnl: float | None


@dataclass(frozen=True)
class RiskPayload:
    """リスク状態更新イベントの payload。"""

    current_exposure: float
    available_margin: float
    drawdown: float


@dataclass(frozen=True)
class LotPayload:
    """ロット状態更新イベントの payload。"""

    current_lot: int
    win_streak: int
    lose_streak: int


@dataclass(frozen=True)
class SnapshotRequestPayload:
    """スナップショット要求イベントの payload。"""

    target: str


@dataclass(frozen=True)
class SnapshotCreatedPayload:
    """スナップショット作成完了イベントの payload。"""

    snapshot_id: str
    path: str
    timestamp: datetime


@dataclass(frozen=True)
class ErrorPayload:
    """エラー通知イベントの payload。"""

    error_type: str
    message: str
    stacktrace: str | None = None


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
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


def _is_optional(field_type: Any) -> bool:
    return get_origin(field_type) in (UnionType, type(None)) and type(None) in get_args(
        field_type
    )


def _deserialize_value(field_type: Any, value: Any) -> Any:
    if value is None:
        if _is_optional(field_type):
            return None
        raise TypeError("必須項目に None は指定できません")

    origin = get_origin(field_type)
    args = get_args(field_type)
    if origin is UnionType:
        target_types = [item for item in args if item is not type(None)]
        if len(target_types) == 1:
            return _deserialize_value(target_types[0], value)
    if origin is tuple:
        item_type = args[0]
        if not isinstance(value, list):
            raise TypeError("tuple 項目は list 形式で指定してください")
        return tuple(_deserialize_value(item_type, item) for item in value)
    if isinstance(field_type, type) and issubclass(field_type, Enum):
        return field_type(value)
    if field_type is datetime:
        if not isinstance(value, str):
            raise TypeError("datetime 項目は ISO 文字列で指定してください")
        return datetime.fromisoformat(value)
    if is_dataclass(field_type):
        if not isinstance(value, Mapping):
            raise TypeError("dataclass 項目は mapping 形式で指定してください")
        return _dataclass_from_dict(field_type, value)
    return value


def _dataclass_from_dict(
    model_type: type[PayloadT], data: Mapping[str, Any]
) -> PayloadT:
    init_values: dict[str, Any] = {}
    for item in fields(model_type):
        if item.name not in data:
            raise ValueError(f"{item.name} が不足しています")
        init_values[item.name] = _deserialize_value(item.type, data[item.name])
    return model_type(**init_values)


@dataclass(frozen=True, kw_only=True)
class BaseEvent(Generic[PayloadT]):
    """共通イベントモデル。"""

    event_id: UUID = field(default_factory=uuid4)
    event_type: EventType
    timestamp: datetime
    source: EventSource
    symbol: str | None
    payload: PayloadT
    sequence_no: int = 1

    _event_classes: ClassVar[dict[EventType, type["BaseEvent[Any]"]]] = {}
    _payload_classes: ClassVar[dict[EventType, type[Any]]] = {}

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, UUID):
            raise TypeError("event_id は UUID で指定してください")
        if not isinstance(self.event_type, EventType):
            raise TypeError("event_type は EventType で指定してください")
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp は datetime で指定してください")
        if not isinstance(self.source, EventSource):
            raise TypeError("source は EventSource で指定してください")
        if self.sequence_no < 1:
            raise ValueError("sequence_no は1以上で指定してください")
        if not is_dataclass(self.payload):
            raise TypeError("payload は dataclass で指定してください")
        expected_payload_type = self._payload_classes.get(self.event_type)
        if expected_payload_type is not None and not isinstance(
            self.payload, expected_payload_type
        ):
            raise TypeError("payload の型が event_type と一致していません")

    def to_dict(self) -> dict[str, Any]:
        """永続化や通信に使える辞書へ変換する。"""

        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source.value,
            "symbol": self.symbol,
            "payload": _serialize_value(self.payload),
            "sequence_no": self.sequence_no,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BaseEvent[Any]":
        """辞書からイベントモデルを復元する。"""

        event_type = EventType(data["event_type"])
        event_class = cls._event_classes[event_type]
        payload_class = cls._payload_classes[event_type]
        payload_data = data["payload"]
        if not isinstance(payload_data, Mapping):
            raise TypeError("payload は mapping 形式で指定してください")

        return event_class(
            event_id=UUID(str(data["event_id"])),
            timestamp=datetime.fromisoformat(str(data["timestamp"])),
            source=EventSource(data["source"]),
            symbol=data["symbol"],
            payload=_dataclass_from_dict(payload_class, payload_data),
            sequence_no=int(data["sequence_no"]),
        )


@dataclass(frozen=True, kw_only=True)
class SystemStarted(BaseEvent[SystemStartedPayload]):
    event_type: EventType = field(default=EventType.SYSTEM_STARTED, init=False)
    payload: SystemStartedPayload


@dataclass(frozen=True, kw_only=True)
class MarketDataUpdated(BaseEvent[MarketDataPayload]):
    event_type: EventType = field(default=EventType.MARKET_DATA_UPDATED, init=False)
    payload: MarketDataPayload


@dataclass(frozen=True, kw_only=True)
class SignalDetected(BaseEvent[SignalPayload]):
    event_type: EventType = field(default=EventType.SIGNAL_DETECTED, init=False)
    payload: SignalPayload


@dataclass(frozen=True, kw_only=True)
class OrderRequested(BaseEvent[OrderPayload]):
    event_type: EventType = field(default=EventType.ORDER_REQUESTED, init=False)
    payload: OrderPayload


@dataclass(frozen=True, kw_only=True)
class OrderStatusUpdated(BaseEvent[OrderStatusPayload]):
    event_type: EventType = field(default=EventType.ORDER_STATUS_UPDATED, init=False)
    payload: OrderStatusPayload


@dataclass(frozen=True, kw_only=True)
class PositionUpdated(BaseEvent[PositionPayload]):
    event_type: EventType = field(default=EventType.POSITION_UPDATED, init=False)
    payload: PositionPayload


@dataclass(frozen=True, kw_only=True)
class RiskUpdated(BaseEvent[RiskPayload]):
    event_type: EventType = field(default=EventType.RISK_UPDATED, init=False)
    payload: RiskPayload


@dataclass(frozen=True, kw_only=True)
class LotUpdated(BaseEvent[LotPayload]):
    event_type: EventType = field(default=EventType.LOT_UPDATED, init=False)
    payload: LotPayload


@dataclass(frozen=True, kw_only=True)
class SnapshotRequested(BaseEvent[SnapshotRequestPayload]):
    event_type: EventType = field(default=EventType.SNAPSHOT_REQUESTED, init=False)
    payload: SnapshotRequestPayload


@dataclass(frozen=True, kw_only=True)
class SnapshotCreated(BaseEvent[SnapshotCreatedPayload]):
    event_type: EventType = field(default=EventType.SNAPSHOT_CREATED, init=False)
    payload: SnapshotCreatedPayload


@dataclass(frozen=True, kw_only=True)
class ErrorOccurred(BaseEvent[ErrorPayload]):
    event_type: EventType = field(default=EventType.ERROR_OCCURRED, init=False)
    payload: ErrorPayload


BaseEvent._event_classes = {
    EventType.SYSTEM_STARTED: SystemStarted,
    EventType.MARKET_DATA_UPDATED: MarketDataUpdated,
    EventType.SIGNAL_DETECTED: SignalDetected,
    EventType.ORDER_REQUESTED: OrderRequested,
    EventType.ORDER_STATUS_UPDATED: OrderStatusUpdated,
    EventType.POSITION_UPDATED: PositionUpdated,
    EventType.RISK_UPDATED: RiskUpdated,
    EventType.LOT_UPDATED: LotUpdated,
    EventType.SNAPSHOT_REQUESTED: SnapshotRequested,
    EventType.SNAPSHOT_CREATED: SnapshotCreated,
    EventType.ERROR_OCCURRED: ErrorOccurred,
}

BaseEvent._payload_classes = {
    EventType.SYSTEM_STARTED: SystemStartedPayload,
    EventType.MARKET_DATA_UPDATED: MarketDataPayload,
    EventType.SIGNAL_DETECTED: SignalPayload,
    EventType.ORDER_REQUESTED: OrderPayload,
    EventType.ORDER_STATUS_UPDATED: OrderStatusPayload,
    EventType.POSITION_UPDATED: PositionPayload,
    EventType.RISK_UPDATED: RiskPayload,
    EventType.LOT_UPDATED: LotPayload,
    EventType.SNAPSHOT_REQUESTED: SnapshotRequestPayload,
    EventType.SNAPSHOT_CREATED: SnapshotCreatedPayload,
    EventType.ERROR_OCCURRED: ErrorPayload,
}


@dataclass
class EventFactory:
    """プロセス単位の連番を付与してイベントを生成する。"""

    source: EventSource
    _sequence_no: int = 0

    def create(
        self,
        event_type: EventType,
        timestamp: datetime,
        symbol: str | None,
        payload: Any,
    ) -> BaseEvent[Any]:
        """次の sequence_no を付与したイベントを生成する。"""

        if not isinstance(event_type, EventType):
            raise TypeError("event_type は EventType で指定してください")
        event_class = BaseEvent._event_classes[event_type]
        event = event_class(
            timestamp=timestamp,
            source=self.source,
            symbol=symbol,
            payload=payload,
            sequence_no=self._sequence_no + 1,
        )
        self._sequence_no = event.sequence_no
        return event

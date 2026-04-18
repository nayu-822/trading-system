from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from domain.enums import EventSource, EventType


@dataclass(frozen=True)
class BaseEvent:
    """共通イベントモデル。

    Args:
        event_type: イベント種別。
        timestamp: イベント発生時刻。
        source: イベント発生元。
        symbol: 対象銘柄。システムイベントでは None。
        payload: イベント詳細。
        sequence_no: 発生順序を表す番号。
        event_id: イベント一意ID。

    Returns:
        BaseEvent インスタンス。
    """

    event_type: EventType
    timestamp: datetime
    source: EventSource
    symbol: str | None
    payload: Mapping[str, Any] = field(default_factory=dict)
    sequence_no: int = 0
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def to_dict(self) -> dict[str, Any]:
        """イベントを保存・検証しやすい辞書へ変換する。

        Args:
            なし。

        Returns:
            シリアライズ可能な辞書。
        """

        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source.value,
            "symbol": self.symbol,
            "payload": dict(self.payload),
            "sequence_no": self.sequence_no,
        }

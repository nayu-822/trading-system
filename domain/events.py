from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any
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
    sequence_no: int = 1
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, EventType):
            raise TypeError("event_type は EventType で指定してください")
        if not isinstance(self.source, EventSource):
            raise TypeError("source は EventSource で指定してください")
        if self.sequence_no < 1:
            raise ValueError("sequence_no は1以上で指定してください")
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
        payload: Mapping[str, Any] | None = None,
    ) -> BaseEvent:
        """次の sequence_no を付与したイベントを生成する。

        Args:
            event_type: イベント種別。
            timestamp: イベント発生時刻。
            symbol: 対象銘柄。システムイベントでは None。
            payload: イベント詳細。

        Returns:
            生成したイベント。
        """

        self._sequence_no += 1
        return BaseEvent(
            event_type=event_type,
            timestamp=timestamp,
            source=self.source,
            symbol=symbol,
            payload=payload or {},
            sequence_no=self._sequence_no,
        )

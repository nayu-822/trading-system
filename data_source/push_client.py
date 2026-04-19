import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from domain.enums import EventSource, EventType
from domain.events import EventFactory, MarketDataPayload, MarketDataUpdated
from domain.models import KabuApiConfig, KabuMarketData

PushConnector = Callable[["PushClient"], None]
MarketDataHandler = Callable[[MarketDataUpdated], None]


@dataclass
class PushClient:
    """Push API の受信メッセージを市場データイベントへ変換する。"""

    config: KabuApiConfig
    on_event: MarketDataHandler | None = None
    connector: PushConnector | None = None
    reconnect_attempts: int = 1
    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.EXTERNAL_DATA)
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    running: bool = False

    def start(self) -> None:
        """Push 接続を開始する。connector はテストや実装差し替え用。"""

        self.running = True
        if self.connector is None:
            self.logger.info("push connector is not configured")
            return
        for attempt in range(1, self.reconnect_attempts + 1):
            try:
                self.connector(self)
                return
            except OSError:
                self.logger.exception("push connection failed attempt=%s", attempt)
                if attempt == self.reconnect_attempts:
                    raise

    def stop(self) -> None:
        """Push 接続を停止状態にする。"""

        self.running = False

    def handle_message(self, message: str | Mapping[str, Any]) -> MarketDataUpdated:
        """受信メッセージを MarketDataUpdated に変換し、handler があれば通知する。"""

        market_data = self._to_market_data(message)
        timestamp = _to_datetime(market_data.timestamp)
        event = self.event_factory.create(
            event_type=EventType.MARKET_DATA_UPDATED,
            timestamp=timestamp,
            symbol=market_data.symbol,
            payload=MarketDataPayload(
                price=market_data.price,
                bid=market_data.bid,
                ask=market_data.ask,
                volume=market_data.volume,
                timestamp=timestamp,
            ),
        )
        if self.on_event is not None:
            self.on_event(event)
        return event

    def _to_market_data(self, message: str | Mapping[str, Any]) -> KabuMarketData:
        data = json.loads(message) if isinstance(message, str) else message
        return KabuMarketData(
            symbol=str(_pick(data, "Symbol", "symbol")),
            price=float(_pick(data, "CurrentPrice", "price")),
            bid=_optional_float(_pick_optional(data, "BidPrice", "bid")),
            ask=_optional_float(_pick_optional(data, "AskPrice", "ask")),
            volume=_optional_int(_pick_optional(data, "TradingVolume", "volume")),
            timestamp=_optional_str(
                _pick_optional(data, "CurrentPriceTime", "timestamp")
            ),
        )


def _pick(data: Mapping[str, Any], *keys: str) -> Any:
    value = _pick_optional(data, *keys)
    if value is None:
        raise ValueError(f"required push field is missing: {keys[0]}")
    return value


def _pick_optional(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _to_datetime(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

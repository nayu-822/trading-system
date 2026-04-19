import logging
from dataclasses import dataclass, field

from domain.enums import EventSource, EventType, SignalType, StrategyType
from domain.events import (
    BaseEvent,
    EventFactory,
    MarketDataUpdated,
    SignalPayload,
)
from domain.models import IndicatorValue
from infrastructure.event_bus import EventBus


@dataclass
class SignalProcess:
    """市場データから最小シグナルを生成するプロセス。"""

    event_bus: EventBus
    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.SIGNAL)
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    last_price: float | None = None

    def start(self) -> None:
        """MarketDataUpdated の購読を開始する。"""

        self.event_bus.subscribe(EventType.MARKET_DATA_UPDATED, self.handle_market_data)

    def handle_market_data(self, event: BaseEvent) -> None:
        """価格変化に応じて BUY / SELL シグナルを publish する。"""

        if not isinstance(event, MarketDataUpdated):
            return

        current_price = event.payload.price
        previous_price = self.last_price
        self.last_price = current_price

        if previous_price is None or current_price == previous_price:
            return

        signal_type = (
            SignalType.BUY if current_price > previous_price else SignalType.SELL
        )
        signal_event = self.event_factory.create(
            event_type=EventType.SIGNAL_DETECTED,
            timestamp=event.timestamp,
            symbol=event.symbol,
            payload=SignalPayload(
                signal_type=signal_type,
                strategy_type=StrategyType.AUTO,
                confidence=None,
                indicators=(
                    IndicatorValue(
                        name="price_delta",
                        value=current_price - previous_price,
                    ),
                ),
            ),
        )
        self.event_bus.publish(signal_event)
        self.logger.info(
            "signal published symbol=%s signal_type=%s sequence_no=%s",
            signal_event.symbol,
            signal_type.value,
            signal_event.sequence_no,
        )

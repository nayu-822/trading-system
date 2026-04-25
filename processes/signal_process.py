import logging
from dataclasses import dataclass, field

from domain.enums import EventSource, EventType, StrategyType
from domain.events import BaseEvent, EventFactory, MarketDataUpdated
from domain.models import SignalStrategyConfig
from infrastructure.event_bus import EventBus
from strategy.base_strategy import BaseStrategy
from strategy.range_strategy import RangeStrategy, RangeStrategyState
from strategy.trend_strategy import TrendStrategy, TrendStrategyState


@dataclass
class SignalStrategySlot:
    """銘柄ごとの戦略実行単位。"""

    symbol: str
    requested_strategy_type: StrategyType
    active_strategy_type: StrategyType
    strategy: BaseStrategy


@dataclass
class SignalProcess:
    """市場データを戦略へ委譲してシグナルを生成するプロセス。"""

    event_bus: EventBus
    strategy_configs: tuple[SignalStrategyConfig, ...] = ()
    default_strategy_type: StrategyType = StrategyType.AUTO
    range_window: int = 3
    trend_short_window: int = 5
    trend_long_window: int = 25
    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.SIGNAL)
    )
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    slots: list[SignalStrategySlot] = field(default_factory=list)
    _subscribed: bool = False

    def start(self) -> None:
        """MarketDataUpdated の購読を開始する。"""

        if self._subscribed:
            return
        self.event_bus.subscribe(EventType.MARKET_DATA_UPDATED, self.handle_market_data)
        self._subscribed = True

    def stop(self) -> None:
        """MarketDataUpdated の購読を解除する。"""

        if not self._subscribed:
            return
        self.event_bus.unsubscribe(
            EventType.MARKET_DATA_UPDATED, self.handle_market_data
        )
        self._subscribed = False

    def handle_market_data(self, event: BaseEvent) -> None:
        """市場データを銘柄別戦略へ渡し、シグナルがあれば publish する。"""

        if not isinstance(event, MarketDataUpdated):
            return
        if event.symbol is None:
            self.logger.warning("market data ignored because symbol is empty")
            return

        slot = self._get_or_create_slot(event.symbol)
        payload = slot.strategy.on_market_data(event)
        if payload is None:
            return

        signal_event = self.event_factory.create(
            event_type=EventType.SIGNAL_DETECTED,
            timestamp=event.timestamp,
            symbol=event.symbol,
            payload=payload,
        )
        self.event_bus.publish(signal_event)
        self.logger.info(
            "signal published symbol=%s signal_type=%s strategy_type=%s sequence_no=%s",
            signal_event.symbol,
            payload.signal_type.value,
            payload.strategy_type.value,
            signal_event.sequence_no,
        )

    def _get_or_create_slot(self, symbol: str) -> SignalStrategySlot:
        for slot in self.slots:
            if slot.symbol == symbol:
                return slot

        requested_strategy_type = self._resolve_requested_strategy_type(symbol)
        active_strategy_type = self._resolve_active_strategy_type(
            symbol=symbol,
            requested_strategy_type=requested_strategy_type,
        )
        slot = SignalStrategySlot(
            symbol=symbol,
            requested_strategy_type=requested_strategy_type,
            active_strategy_type=active_strategy_type,
            strategy=self._create_strategy(
                symbol=symbol,
                strategy_type=active_strategy_type,
            ),
        )
        self.slots.append(slot)
        return slot

    def _resolve_requested_strategy_type(self, symbol: str) -> StrategyType:
        strategy_config = self._find_strategy_config(symbol)
        if strategy_config is None:
            return self.default_strategy_type
        return strategy_config.strategy_type

    def _resolve_active_strategy_type(
        self,
        symbol: str,
        requested_strategy_type: StrategyType,
    ) -> StrategyType:
        if requested_strategy_type == StrategyType.AUTO:
            self.logger.warning(
                "auto strategy is not implemented; fallback to trend symbol=%s",
                symbol,
            )
            return StrategyType.TREND
        if requested_strategy_type in {StrategyType.TREND, StrategyType.RANGE}:
            return requested_strategy_type

        self.logger.warning(
            "unsupported strategy; fallback to trend symbol=%s strategy_type=%s",
            symbol,
            requested_strategy_type,
        )
        return StrategyType.TREND

    def _create_strategy(
        self,
        symbol: str,
        strategy_type: StrategyType,
    ) -> BaseStrategy:
        strategy_config = self._find_strategy_config(symbol)
        if strategy_type == StrategyType.RANGE:
            return RangeStrategy(
                state=RangeStrategyState(symbol=symbol),
                window=self._resolve_range_window(strategy_config),
            )
        short_window, long_window = self._resolve_trend_windows(strategy_config)
        return TrendStrategy(
            state=TrendStrategyState(symbol=symbol),
            short_window=short_window,
            long_window=long_window,
        )

    def _find_strategy_config(self, symbol: str) -> SignalStrategyConfig | None:
        for strategy_config in self.strategy_configs:
            if strategy_config.symbol == symbol:
                return strategy_config
        return None

    def _resolve_range_window(
        self,
        strategy_config: SignalStrategyConfig | None,
    ) -> int:
        if strategy_config is not None and strategy_config.range_window is not None:
            return strategy_config.range_window
        return self.range_window

    def _resolve_trend_windows(
        self,
        strategy_config: SignalStrategyConfig | None,
    ) -> tuple[int, int]:
        short_window = self.trend_short_window
        long_window = self.trend_long_window
        if strategy_config is not None and strategy_config.trend_short_window is not None:
            short_window = strategy_config.trend_short_window
        if strategy_config is not None and strategy_config.trend_long_window is not None:
            long_window = strategy_config.trend_long_window
        return short_window, long_window

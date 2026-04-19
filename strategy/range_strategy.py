from dataclasses import dataclass, field

from domain.enums import SignalType, StrategyType
from domain.events import MarketDataUpdated, SignalPayload
from domain.models import IndicatorValue


@dataclass
class RangeStrategyState:
    """レンジ戦略の銘柄別状態。"""

    symbol: str
    prices: list[float] = field(default_factory=list)
    last_signal_type: SignalType | None = None


@dataclass
class RangeStrategy:
    """直近レンジ上限・下限でシグナルを生成する最小レンジ戦略。"""

    state: RangeStrategyState
    window: int = 3

    def on_market_data(self, event: MarketDataUpdated) -> SignalPayload | None:
        """直近レンジの下側なら BUY、上側なら SELL を返す。"""

        current_price = event.payload.price
        if len(self.state.prices) < self.window:
            self._append_price(current_price)
            return None

        range_low = min(self.state.prices)
        range_high = max(self.state.prices)
        signal_type: SignalType | None = None
        if current_price <= range_low:
            signal_type = SignalType.BUY
        elif current_price >= range_high:
            signal_type = SignalType.SELL

        self._append_price(current_price)
        if signal_type is None or signal_type == self.state.last_signal_type:
            return None

        self.state.last_signal_type = signal_type
        return SignalPayload(
            signal_type=signal_type,
            strategy_type=StrategyType.RANGE,
            confidence=None,
            indicators=(
                IndicatorValue(name="range_low", value=range_low),
                IndicatorValue(name="range_high", value=range_high),
                IndicatorValue(name="current_price", value=current_price),
            ),
        )

    def reset(self, symbol: str) -> None:
        """指定銘柄の状態を初期化する。"""

        if self.state.symbol == symbol:
            self.state.prices.clear()
            self.state.last_signal_type = None

    def _append_price(self, price: float) -> None:
        self.state.prices.append(price)
        if len(self.state.prices) > self.window:
            self.state.prices.pop(0)

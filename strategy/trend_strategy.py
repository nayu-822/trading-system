from dataclasses import dataclass

from domain.enums import SignalType, StrategyType
from domain.events import MarketDataUpdated, SignalPayload
from domain.models import IndicatorValue


@dataclass
class TrendStrategyState:
    """トレンド戦略の銘柄別状態。"""

    symbol: str
    last_price: float | None = None
    last_signal_type: SignalType | None = None


@dataclass
class TrendStrategy:
    """直近価格の上昇・下降でシグナルを生成する最小トレンド戦略。"""

    state: TrendStrategyState

    def on_market_data(self, event: MarketDataUpdated) -> SignalPayload | None:
        """前回価格との差分から BUY / SELL を判定する。"""

        current_price = event.payload.price
        previous_price = self.state.last_price
        self.state.last_price = current_price

        if previous_price is None or current_price == previous_price:
            return None

        signal_type = (
            SignalType.BUY if current_price > previous_price else SignalType.SELL
        )
        if signal_type == self.state.last_signal_type:
            return None

        self.state.last_signal_type = signal_type
        return SignalPayload(
            signal_type=signal_type,
            strategy_type=StrategyType.TREND,
            confidence=None,
            indicators=(
                IndicatorValue(
                    name="price_delta",
                    value=current_price - previous_price,
                ),
                IndicatorValue(name="current_price", value=current_price),
            ),
        )

    def reset(self, symbol: str) -> None:
        """指定銘柄の状態を初期化する。"""

        if self.state.symbol == symbol:
            self.state.last_price = None
            self.state.last_signal_type = None

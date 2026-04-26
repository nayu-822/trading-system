from dataclasses import dataclass, field

from domain.enums import SignalType, StrategyType
from domain.events import MarketDataUpdated, SignalPayload
from domain.models import IndicatorValue


@dataclass
class TrendStrategyState:
    """トレンド戦略の銘柄別状態を保持する。"""

    symbol: str
    prices: list[float] = field(default_factory=list)
    last_price: float | None = None
    last_signal_type: SignalType | None = None


@dataclass
class TrendStrategy:
    """移動平均の大小関係で売買シグナルを判定するトレンド戦略。"""

    state: TrendStrategyState
    short_window: int = 5
    long_window: int = 25

    def __post_init__(self) -> None:
        """生成時に移動平均期間の妥当性を検証する。"""

        self._validate_windows()

    def on_market_data(self, event: MarketDataUpdated) -> SignalPayload | None:
        """価格履歴から移動平均を計算し、BUY / SELL を判定する。"""

        current_price = event.payload.price
        self.state.last_price = current_price
        self._append_price(current_price)

        if len(self.state.prices) < self.long_window:
            return None

        short_ma = self._calculate_moving_average(self.short_window)
        long_ma = self._calculate_moving_average(self.long_window)
        signal_type = self._resolve_signal_type(short_ma=short_ma, long_ma=long_ma)
        if signal_type is None:
            return None
        if signal_type == self.state.last_signal_type:
            return None

        self.state.last_signal_type = signal_type
        return SignalPayload(
            signal_type=signal_type,
            strategy_type=StrategyType.TREND,
            confidence=None,
            indicators=(
                IndicatorValue(name="short_ma", value=short_ma),
                IndicatorValue(name="long_ma", value=long_ma),
                IndicatorValue(name="ma_diff", value=short_ma - long_ma),
                IndicatorValue(name="current_price", value=current_price),
            ),
        )

    def reset(self, symbol: str) -> None:
        """指定銘柄の戦略状態を初期化する。"""

        if self.state.symbol == symbol:
            self.state.prices.clear()
            self.state.last_price = None
            self.state.last_signal_type = None

    def _validate_windows(self) -> None:
        """移動平均期間が戦略要件を満たすか検証する。"""

        if self.short_window < 1:
            raise ValueError("short_window は1以上で指定してください")
        if self.long_window < 1:
            raise ValueError("long_window は1以上で指定してください")
        if self.short_window >= self.long_window:
            raise ValueError("short_window は long_window より小さく指定してください")

    def _append_price(self, price: float) -> None:
        """長期移動平均に必要な本数まで価格履歴を保持する。"""

        self.state.prices.append(price)
        if len(self.state.prices) > self.long_window:
            self.state.prices.pop(0)

    def _calculate_moving_average(self, window: int) -> float:
        """直近 window 本の単純移動平均を返す。"""

        target_prices = self.state.prices[-window:]
        return sum(target_prices) / window

    def _resolve_signal_type(
        self,
        short_ma: float,
        long_ma: float,
    ) -> SignalType | None:
        """短期平均と長期平均の関係から売買方向を判定する。"""

        if short_ma > long_ma:
            return SignalType.BUY
        if short_ma < long_ma:
            return SignalType.SELL
        return None

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.strategies.signal import BaseSignal, SignalType


@dataclass(frozen=True)
class TrendSignal(BaseSignal):
    short_ma: float
    long_ma: float


@dataclass(frozen=True)
class TrendStrategy:
    short_window: int = 5
    long_window: int = 25
    price_column: str = "close"

    def __post_init__(self) -> None:
        """
        戦略パラメータの妥当性を検証する。

        引数:
            なし

        戻り値:
            なし
        """
        if self.short_window <= 0 or self.long_window <= 0:
            raise ValueError("invalid parameter: moving average window must be greater than zero")

        if self.short_window >= self.long_window:
            raise ValueError("invalid parameter: short_window must be smaller than long_window")

    def generate_signal(self, market_data: pd.DataFrame) -> TrendSignal:
        """
        終値データから移動平均線のクロスを判定し、売買シグナルを返す。

        引数:
            market_data: 終値列を含む市場データの DataFrame

        戻り値:
            TrendSignal: 最新の短期移動平均、長期移動平均、判定結果を持つシグナル情報
        """
        self._validate_market_data(market_data=market_data)

        price_series = market_data[self.price_column].astype(float)
        short_ma = price_series.rolling(window=self.short_window).mean()
        long_ma = price_series.rolling(window=self.long_window).mean()

        previous_short_ma = short_ma.iloc[-2]
        previous_long_ma = long_ma.iloc[-2]
        current_short_ma = short_ma.iloc[-1]
        current_long_ma = long_ma.iloc[-1]

        signal = self._detect_signal(
            previous_short_ma=previous_short_ma,
            previous_long_ma=previous_long_ma,
            current_short_ma=current_short_ma,
            current_long_ma=current_long_ma,
        )

        return TrendSignal(
            signal=signal,
            short_ma=float(current_short_ma),
            long_ma=float(current_long_ma),
        )

    def _validate_market_data(self, market_data: pd.DataFrame) -> None:
        """
        シグナル判定に必要な列とデータ件数が揃っているかを検証する。

        引数:
            market_data: シグナル判定対象の市場データ

        戻り値:
            なし
        """
        if self.price_column not in market_data.columns:
            raise ValueError(f"required column not found: {self.price_column}")

        minimum_rows = self.long_window + 1
        if len(market_data) < minimum_rows:
            raise ValueError(
                f"market_data must contain at least {minimum_rows} rows "
                "to evaluate a moving average crossover"
            )

    def _detect_signal(
        self,
        previous_short_ma: float,
        previous_long_ma: float,
        current_short_ma: float,
        current_long_ma: float,
    ) -> SignalType:
        """
        直前と現在の移動平均を比較し、クロスの種類に応じたシグナルを返す。

        引数:
            previous_short_ma: 1本前の短期移動平均
            previous_long_ma: 1本前の長期移動平均
            current_short_ma: 最新の短期移動平均
            current_long_ma: 最新の長期移動平均

        戻り値:
            SignalType: BUY、SELL、HOLD のいずれか
        """
        if previous_short_ma <= previous_long_ma and current_short_ma > current_long_ma:
            return SignalType.BUY

        if previous_short_ma >= previous_long_ma and current_short_ma < current_long_ma:
            return SignalType.SELL

        return SignalType.HOLD

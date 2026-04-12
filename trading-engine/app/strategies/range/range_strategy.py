from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.strategies.signal import BaseSignal, SignalType


@dataclass(frozen=True)
class RangeSignal(BaseSignal):
    rsi: float


@dataclass(frozen=True)
class RangeStrategy:
    """
    RSI を用いてレンジ相場向けの売買シグナルを返す戦略。

    引数:
        rsi_period: RSI を計算する期間
        lower_threshold: 買いシグナル判定に使う RSI 下限
        upper_threshold: 売りシグナル判定に使う RSI 上限
        price_column: RSI 計算に使う価格列名

    戻り値:
        なし
    """

    rsi_period: int = 14
    lower_threshold: float = 30.0
    upper_threshold: float = 70.0
    price_column: str = "close"

    def __post_init__(self) -> None:
        """
        RSI 戦略のパラメータ妥当性を検証する。

        引数:
            なし

        戻り値:
            なし
        """
        if self.rsi_period <= 0:
            raise ValueError("invalid parameter: rsi_period must be greater than zero")

        if not 0.0 <= self.lower_threshold <= 100.0:
            raise ValueError("invalid parameter: lower_threshold must be between 0 and 100")

        if not 0.0 <= self.upper_threshold <= 100.0:
            raise ValueError("invalid parameter: upper_threshold must be between 0 and 100")

        if self.lower_threshold >= self.upper_threshold:
            raise ValueError("invalid parameter: lower_threshold must be smaller than upper_threshold")

    def generate_signal(self, market_data: pd.DataFrame) -> RangeSignal:
        """
        終値データから RSI を計算し、レンジ相場向けの売買シグナルを返す。

        引数:
            market_data: 終値列を含む市場データの DataFrame

        戻り値:
            RangeSignal: 最新 RSI と判定結果を持つシグナル情報

        補足:
            データ件数が不足している場合は、ウォームアップ中とみなして HOLD を返す
        """
        if self.price_column not in market_data.columns:
            raise ValueError(f"required column not found: {self.price_column}")

        minimum_rows = self.rsi_period + 1
        if len(market_data) < minimum_rows:
            return RangeSignal(signal=SignalType.HOLD, rsi=0.0)

        price_series = market_data[self.price_column].astype(float)
        rsi_series = self._calculate_rsi(price_series=price_series)
        current_rsi = float(rsi_series.iloc[-1])
        signal = self._detect_signal(current_rsi=current_rsi)

        return RangeSignal(signal=signal, rsi=current_rsi)

    def _calculate_rsi(self, price_series: pd.Series) -> pd.Series:
        """
        終値系列から RSI を計算する。

        引数:
            price_series: 終値の時系列データ

        戻り値:
            pd.Series: RSI の時系列データ
        """
        delta = price_series.diff()
        gains = delta.clip(lower=0.0)
        losses = -delta.clip(upper=0.0)

        average_gain = gains.rolling(window=self.rsi_period, min_periods=self.rsi_period).mean()
        average_loss = losses.rolling(window=self.rsi_period, min_periods=self.rsi_period).mean()

        relative_strength = average_gain / average_loss.replace(0.0, pd.NA)
        rsi = 100.0 - (100.0 / (1.0 + relative_strength))
        return rsi.fillna(100.0)

    def _detect_signal(self, current_rsi: float) -> SignalType:
        """
        最新 RSI を閾値と比較し、レンジ戦略用のシグナルを返す。

        引数:
            current_rsi: 最新の RSI 値

        戻り値:
            SignalType: BUY、SELL、HOLD のいずれか
        """
        if current_rsi <= self.lower_threshold:
            return SignalType.BUY

        if current_rsi >= self.upper_threshold:
            return SignalType.SELL

        return SignalType.HOLD

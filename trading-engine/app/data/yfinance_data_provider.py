from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


REQUIRED_YFINANCE_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


@dataclass(frozen=True)
class YFinanceDataProvider:
    """
    yfinance から OHLCV データを取得し、既存システム形式へ変換するクラス。

    補足:
        日本株を取得する場合は `7203.T` のように `.T` を付ける必要がある。
    """

    auto_adjust: bool = False
    progress: bool = False

    def fetch(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        """
        yfinance から市場データを取得し、標準形式の DataFrame を返す。
        引数:
            symbol: 取得対象の銘柄コード
            period: 取得期間
            interval: 足種別

        戻り値:
            pd.DataFrame: 標準形式へ変換した市場データ
        """
        yfinance = self._import_yfinance()
        downloaded_data = yfinance.download(
            tickers=symbol,
            period=period,
            interval=interval,
            auto_adjust=self.auto_adjust,
            progress=self.progress,
        )

        if downloaded_data.empty:
            raise ValueError(f"yfinance data is empty: {symbol}")

        normalized_data = self._normalize_downloaded_data(
            market_data=downloaded_data,
            symbol=symbol,
        )
        validated_data = self._drop_missing_rows(market_data=normalized_data, symbol=symbol)
        self._validate_columns(market_data=validated_data)
        return validated_data.reset_index(drop=True)

    def _import_yfinance(self) -> object:
        """
        yfinance モジュールを遅延 import する。
        引数:
            なし

        戻り値:
            object: yfinance モジュール
        """
        try:
            import yfinance
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError("yfinance is required to fetch market data") from exc

        return yfinance

    def _normalize_downloaded_data(self, market_data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        yfinance の戻り値を標準形式へ変換する。
        引数:
            market_data: yfinance が返した市場データ
            symbol: 取得対象の銘柄コード

        戻り値:
            pd.DataFrame: 標準形式へ変換した市場データ
        """
        normalized_data = market_data.copy()
        normalized_data.columns = [str(column).lower() for column in normalized_data.columns]
        normalized_data = normalized_data.reset_index()
        normalized_data.columns = [str(column).lower() for column in normalized_data.columns]

        if "index" in normalized_data.columns and "timestamp" not in normalized_data.columns:
            normalized_data = normalized_data.rename(columns={"index": "timestamp"})
        if "datetime" in normalized_data.columns and "timestamp" not in normalized_data.columns:
            normalized_data = normalized_data.rename(columns={"datetime": "timestamp"})
        if "date" in normalized_data.columns and "timestamp" not in normalized_data.columns:
            normalized_data = normalized_data.rename(columns={"date": "timestamp"})

        if "timestamp" not in normalized_data.columns:
            raise ValueError("timestamp column is required")

        normalized_data["timestamp"] = pd.to_datetime(normalized_data["timestamp"], errors="coerce")
        if normalized_data["timestamp"].isna().any():
            raise ValueError("timestamp contains invalid datetime values")

        normalized_data["symbol"] = symbol
        return normalized_data

    def _drop_missing_rows(self, market_data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        必須列の欠損を含む行を除外する。
        引数:
            market_data: 標準形式へ変換した市場データ
            symbol: 取得対象の銘柄コード

        戻り値:
            pd.DataFrame: 欠損行を除外した市場データ
        """
        cleaned_data = market_data.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
        if cleaned_data.empty:
            raise ValueError(f"yfinance data contains no valid OHLCV rows: {symbol}")

        return cleaned_data

    def _validate_columns(self, market_data: pd.DataFrame) -> None:
        """
        標準形式の必須カラムがそろっているかを検証する。
        引数:
            market_data: 検証対象の市場データ

        戻り値:
            なし
        """
        missing_columns = [column for column in REQUIRED_YFINANCE_COLUMNS if column not in market_data.columns]
        if missing_columns:
            raise ValueError(f"required columns are missing: {', '.join(missing_columns)}")

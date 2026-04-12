from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


REQUIRED_YFINANCE_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "symbol",
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
        return self._reorder_columns(market_data=validated_data).reset_index(drop=True)

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
        normalized_data = self._flatten_columns(market_data=market_data.copy(), symbol=symbol)
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

    def _flatten_columns(self, market_data: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        yfinance の MultiIndex カラムを単一銘柄向けの列へ平坦化する。
        引数:
            market_data: yfinance が返した市場データ
            symbol: 取得対象の銘柄コード

        戻り値:
            pd.DataFrame: 平坦化済みの市場データ
        """
        if not isinstance(market_data.columns, pd.MultiIndex):
            market_data.columns = [str(column).lower() for column in market_data.columns]
            return market_data

        flattened_columns: list[str] = []
        normalized_symbol = symbol.lower()

        for column in market_data.columns:
            column_parts = [str(part).lower() for part in column if str(part)]
            if not column_parts:
                raise ValueError("yfinance columns are invalid")

            if normalized_symbol in column_parts:
                flattened_columns.append(next(part for part in column_parts if part != normalized_symbol))
            else:
                flattened_columns.append(column_parts[0])

        market_data.columns = flattened_columns
        return market_data

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

    def _reorder_columns(self, market_data: pd.DataFrame) -> pd.DataFrame:
        """
        標準形式のカラム順を固定する。
        引数:
            market_data: 並び替え対象の市場データ

        戻り値:
            pd.DataFrame: カラム順を固定した市場データ
        """
        ordered_columns = list(REQUIRED_YFINANCE_COLUMNS)
        extra_columns = [column for column in market_data.columns if column not in ordered_columns]
        return market_data.loc[:, ordered_columns + extra_columns]

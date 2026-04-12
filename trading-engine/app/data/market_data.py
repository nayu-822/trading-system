from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


REQUIRED_MARKET_DATA_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


def _validate_market_data_frame(market_data: pd.DataFrame) -> None:
    """
    市場データ DataFrame が必須カラムと件数要件を満たすかを検証する。

    引数:
        market_data: 検証対象の市場データ

    戻り値:
        なし
    """
    if market_data.empty:
        raise ValueError("market data is empty")

    missing_columns = [column for column in REQUIRED_MARKET_DATA_COLUMNS if column not in market_data.columns]
    if missing_columns:
        raise ValueError(f"required columns are missing: {', '.join(missing_columns)}")


def _normalize_market_data_frame(market_data: pd.DataFrame) -> pd.DataFrame:
    """
    市場データ DataFrame を timestamp 列の型変換と並び順整形を含めて正規化する。

    引数:
        market_data: 正規化対象の市場データ

    戻り値:
        pd.DataFrame: 正規化済みの市場データ
    """
    normalized_market_data = market_data.copy()

    if "timestamp" in normalized_market_data.columns:
        normalized_market_data["timestamp"] = pd.to_datetime(
            normalized_market_data["timestamp"],
            errors="coerce",
        )
        if normalized_market_data["timestamp"].isna().any():
            raise ValueError("timestamp contains invalid datetime values")

        normalized_market_data = normalized_market_data.sort_values("timestamp").reset_index(drop=True)
        return normalized_market_data

    return normalized_market_data.reset_index(drop=True)


@dataclass(frozen=True)
class CsvMarketDataProvider:
    csv_path: Path
    encoding: str = "utf-8"

    def fetch(self, symbol: str) -> pd.DataFrame:
        """
        CSV ファイルから市場データを読み込み、指定銘柄の DataFrame を返す。

        引数:
            symbol: 読み込み対象の銘柄コード

        戻り値:
            pd.DataFrame: 必須カラムを含む市場データ
        """
        if not self.csv_path.exists():
            raise FileNotFoundError(f"csv file not found: {self.csv_path}")

        market_data = pd.read_csv(self.csv_path, encoding=self.encoding)
        if market_data.empty:
            raise ValueError("csv is empty")

        if "symbol" in market_data.columns:
            market_data = market_data[market_data["symbol"].astype(str) == symbol]
            if market_data.empty:
                raise ValueError(f"symbol not found in csv: {symbol}")

        validated_market_data = _normalize_market_data_frame(market_data=market_data)
        _validate_market_data_frame(market_data=validated_market_data)
        return validated_market_data


@dataclass(frozen=True)
class DummyMarketDataProvider:
    row_count: int = 30
    start_price: float = 100.0

    def __post_init__(self) -> None:
        """
        ダミーデータ設定の妥当性を検証する。

        引数:
            なし

        戻り値:
            なし
        """
        if self.row_count <= 0:
            raise ValueError("row_count must be greater than zero")

        if self.start_price <= 0:
            raise ValueError("start_price must be greater than zero")

    def fetch(self, symbol: str) -> pd.DataFrame:
        """
        テスト用の再現性ある OHLCV ダミーデータを返す。

        引数:
            symbol: 付与対象の銘柄コード

        戻り値:
            pd.DataFrame: 必須カラムと補助カラムを含む市場データ
        """
        base_index = range(self.row_count)
        close_prices = [self.start_price + (index * 0.5) + ((index % 4) * 0.1) for index in base_index]
        market_data = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-01-05 09:00:00", periods=self.row_count, freq="min"),
                "symbol": [symbol] * self.row_count,
                "open": [price - 0.2 for price in close_prices],
                "high": [price + 0.4 for price in close_prices],
                "low": [price - 0.5 for price in close_prices],
                "close": close_prices,
                "volume": [1000 + (index * 10) for index in base_index],
            }
        )
        normalized_market_data = _normalize_market_data_frame(market_data=market_data)
        _validate_market_data_frame(market_data=normalized_market_data)
        return normalized_market_data

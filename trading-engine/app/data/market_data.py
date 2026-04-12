from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging

import pandas as pd


logger = logging.getLogger(__name__)

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
        message = "market data is empty"
        logger.error(message)
        raise ValueError(message)

    missing_columns = [column for column in REQUIRED_MARKET_DATA_COLUMNS if column not in market_data.columns]
    if missing_columns:
        message = f"required columns are missing: {', '.join(missing_columns)}"
        logger.error(message)
        raise ValueError(message)


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
            message = f"csv file not found: {self.csv_path}"
            logger.error(message)
            raise FileNotFoundError(message)

        market_data = pd.read_csv(self.csv_path, encoding=self.encoding)
        if "symbol" in market_data.columns:
            market_data = market_data[market_data["symbol"].astype(str) == symbol]

        validated_market_data = market_data.reset_index(drop=True)
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
            message = "row_count must be greater than zero"
            logger.error(message)
            raise ValueError(message)

        if self.start_price <= 0:
            message = "start_price must be greater than zero"
            logger.error(message)
            raise ValueError(message)

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
        _validate_market_data_frame(market_data=market_data)
        return market_data

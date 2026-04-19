from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from domain.enums import EventSource, EventType
from domain.events import EventFactory, MarketDataPayload, MarketDataUpdated

REQUIRED_COLUMNS = ("timestamp", "price", "bid", "ask", "volume")


class CsvLoadError(Exception):
    """CSV 読み込みに失敗したことを表す例外。"""


@dataclass
class CsvMarketDataLoader:
    """CSV の市場データを MarketDataUpdated イベントへ変換する。"""

    event_factory: EventFactory = field(
        default_factory=lambda: EventFactory(source=EventSource.EXTERNAL_DATA)
    )

    def load_events(self, csv_path: Path, symbol: str) -> Iterator[MarketDataUpdated]:
        """CSV を時系列順に読み込み、1行ずつイベントへ変換する。"""

        data_frame = pd.read_csv(csv_path)
        self._validate_columns(data_frame)
        sorted_frame = (
            data_frame.assign(timestamp=pd.to_datetime(data_frame["timestamp"]))
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

        for row in sorted_frame.itertuples(index=False):
            timestamp = self._to_datetime(row.timestamp)
            yield self.event_factory.create(
                event_type=EventType.MARKET_DATA_UPDATED,
                timestamp=timestamp,
                symbol=symbol,
                payload=MarketDataPayload(
                    price=float(row.price),
                    bid=self._optional_float(row.bid),
                    ask=self._optional_float(row.ask),
                    volume=self._optional_int(row.volume),
                    timestamp=timestamp,
                ),
            )

    def _validate_columns(self, data_frame: pd.DataFrame) -> None:
        missing_columns = [
            column for column in REQUIRED_COLUMNS if column not in data_frame.columns
        ]
        if missing_columns:
            raise CsvLoadError(
                f"CSV カラムが不足しています: {', '.join(missing_columns)}"
            )

    def _to_datetime(self, value: object) -> datetime:
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if isinstance(value, datetime):
            return value
        return pd.to_datetime(value).to_pydatetime()

    def _optional_float(self, value: object) -> float | None:
        if pd.isna(value):
            return None
        return float(value)

    def _optional_int(self, value: object) -> int | None:
        if pd.isna(value):
            return None
        return int(value)

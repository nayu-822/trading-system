from __future__ import annotations

from pathlib import Path
import sys
import types

import pandas as pd


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.data.yfinance_data_provider import YFinanceDataProvider


def test_yfinance_data_provider_returns_normalized_dataframe(monkeypatch) -> None:
    download_calls: list[dict[str, object]] = []

    def fake_download(
        *,
        tickers: str,
        period: str,
        interval: str,
        auto_adjust: bool,
        progress: bool,
    ) -> pd.DataFrame:
        download_calls.append(
            {
                "tickers": tickers,
                "period": period,
                "interval": interval,
                "auto_adjust": auto_adjust,
                "progress": progress,
            }
        )
        return pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [101.0, 102.0],
                "Low": [99.5, 100.5],
                "Close": [100.5, 101.5],
                "Volume": [1000, 1200],
            },
            index=pd.to_datetime(["2026-01-05 09:00:00", "2026-01-06 09:00:00"]),
        )

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=fake_download))
    provider = YFinanceDataProvider()

    market_data = provider.fetch(symbol="7203.T", period="1y", interval="1d")

    assert list(market_data.columns) == ["timestamp", "open", "high", "low", "close", "volume", "symbol"]
    assert pd.api.types.is_datetime64_any_dtype(market_data["timestamp"])
    assert set(market_data["symbol"]) == {"7203.T"}
    assert download_calls == [
        {
            "tickers": "7203.T",
            "period": "1y",
            "interval": "1d",
            "auto_adjust": False,
            "progress": False,
        }
    ]


def test_yfinance_data_provider_drops_missing_rows_and_keeps_valid_rows(monkeypatch) -> None:
    def fake_download(
        *,
        tickers: str,
        period: str,
        interval: str,
        auto_adjust: bool,
        progress: bool,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Open": [100.0, None],
                "High": [101.0, 102.0],
                "Low": [99.5, 100.5],
                "Close": [100.5, 101.5],
                "Volume": [1000, 1200],
            },
            index=pd.to_datetime(["2026-01-05 09:00:00", "2026-01-06 09:00:00"]),
        )

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=fake_download))
    provider = YFinanceDataProvider()

    market_data = provider.fetch(symbol="6758.T", period="6mo", interval="1d")

    assert len(market_data) == 1
    assert market_data.loc[0, "symbol"] == "6758.T"
    assert list(market_data.columns) == ["timestamp", "open", "high", "low", "close", "volume", "symbol"]

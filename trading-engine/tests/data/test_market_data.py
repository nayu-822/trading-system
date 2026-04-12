from __future__ import annotations

import shutil
import sys
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.data.market_data import CsvMarketDataProvider, DummyMarketDataProvider


def _create_workspace_temp_dir() -> Path:
    """
    ワークスペース配下にテスト用一時ディレクトリを作成する。

    引数:
        なし

    戻り値:
        Path: テスト用一時ディレクトリ
    """
    temp_dir = Path(__file__).resolve().parents[3] / ".tmp" / f"market-data-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    return temp_dir


def test_csv_market_data_provider_reads_csv_successfully() -> None:
    temp_dir = _create_workspace_temp_dir()
    csv_path = temp_dir / "market_data.csv"
    source_data = pd.DataFrame(
        {
            "symbol": ["7203", "7203"],
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.5, 100.5],
            "close": [100.5, 101.5],
            "volume": [1000, 1100],
        }
    )
    try:
        source_data.to_csv(csv_path, index=False)
        provider = CsvMarketDataProvider(csv_path=csv_path)

        market_data = provider.fetch(symbol="7203")

        assert list(market_data.columns) == ["symbol", "open", "high", "low", "close", "volume"]
        assert len(market_data) == 2
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_csv_market_data_provider_raises_when_required_columns_are_missing() -> None:
    temp_dir = _create_workspace_temp_dir()
    csv_path = temp_dir / "market_data.csv"
    source_data = pd.DataFrame(
        {
            "symbol": ["7203"],
            "open": [100.0],
            "high": [101.0],
            "low": [99.5],
            "close": [100.5],
        }
    )
    try:
        source_data.to_csv(csv_path, index=False)
        provider = CsvMarketDataProvider(csv_path=csv_path)

        with pytest.raises(ValueError):
            provider.fetch(symbol="7203")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_dummy_market_data_provider_returns_required_ohlcv_columns() -> None:
    provider = DummyMarketDataProvider()

    market_data = provider.fetch(symbol="7203")

    assert {"open", "high", "low", "close", "volume"}.issubset(set(market_data.columns))
    assert {"timestamp", "symbol"}.issubset(set(market_data.columns))
    assert len(market_data) >= 26


def test_csv_market_data_provider_raises_when_filtered_data_is_empty() -> None:
    temp_dir = _create_workspace_temp_dir()
    csv_path = temp_dir / "market_data.csv"
    source_data = pd.DataFrame(
        {
            "symbol": ["6758"],
            "open": [100.0],
            "high": [101.0],
            "low": [99.5],
            "close": [100.5],
            "volume": [1000],
        }
    )
    try:
        source_data.to_csv(csv_path, index=False)
        provider = CsvMarketDataProvider(csv_path=csv_path)

        with pytest.raises(ValueError):
            provider.fetch(symbol="7203")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

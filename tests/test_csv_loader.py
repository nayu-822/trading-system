from datetime import datetime
from pathlib import Path

from data_source.csv_loader import CsvMarketDataLoader
from domain.enums import EventSource, EventType
from domain.events import MarketDataUpdated

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_csv_loader_converts_rows_to_market_data_events_in_time_order() -> None:
    csv_path = FIXTURE_DIR / "market_data.csv"
    events = list(CsvMarketDataLoader().load_events(csv_path=csv_path, symbol="7203"))

    assert len(events) == 2
    assert all(isinstance(event, MarketDataUpdated) for event in events)
    assert events[0].event_type == EventType.MARKET_DATA_UPDATED
    assert events[0].source == EventSource.EXTERNAL_DATA
    assert events[0].symbol == "7203"
    assert events[0].payload.timestamp == datetime(2026, 4, 18, 9, 0)
    assert events[0].payload.price == 100.0
    assert events[1].payload.price == 101.0
    assert events[0].sequence_no == 1
    assert events[1].sequence_no == 2

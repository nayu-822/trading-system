import logging
from dataclasses import dataclass, field
from pathlib import Path

from data_source.csv_loader import CsvMarketDataLoader
from infrastructure.event_bus import EventBus


@dataclass
class ExternalDataProcess:
    """CSV から市場データイベントを発行する外部データプロセス。"""

    event_bus: EventBus
    csv_loader: CsvMarketDataLoader = field(default_factory=CsvMarketDataLoader)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def run_csv(self, csv_path: Path, symbol: str) -> None:
        """CSV の各行を MarketDataUpdated として publish する。"""

        for event in self.csv_loader.load_events(csv_path=csv_path, symbol=symbol):
            self.event_bus.publish(event)
            self.logger.info(
                "market data published symbol=%s sequence_no=%s",
                event.symbol,
                event.sequence_no,
            )

import logging
from dataclasses import dataclass, field
from pathlib import Path

from data_source.csv_loader import CsvMarketDataLoader
from data_source.push_client import PushClient
from data_source.rest_poller import RestPoller
from domain.enums import DataSourceMode
from infrastructure.event_bus import EventBus


@dataclass
class ExternalDataProcess:
    """CSV から市場データイベントを発行する外部データプロセス。"""

    event_bus: EventBus
    csv_loader: CsvMarketDataLoader = field(default_factory=CsvMarketDataLoader)
    push_client: PushClient | None = None
    rest_poller: RestPoller | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    _running_api: bool = False

    def run(
        self,
        data_source_mode: DataSourceMode,
        csv_path: Path | None,
        symbol: str,
    ) -> None:
        """設定された外部データモードでイベント供給を開始する。"""

        if data_source_mode == DataSourceMode.CSV:
            if csv_path is None:
                self.logger.warning("csv mode skipped because csv_path is empty")
                return
            self.run_csv(csv_path=csv_path, symbol=symbol)
            return
        if data_source_mode == DataSourceMode.API:
            self.run_api()
            return
        raise ValueError(f"unsupported data_source_mode={data_source_mode.value}")

    def run_csv(self, csv_path: Path, symbol: str) -> None:
        """CSV の各行を MarketDataUpdated として publish する。"""

        for event in self.csv_loader.load_events(csv_path=csv_path, symbol=symbol):
            self.event_bus.publish(event)
            self.logger.info(
                "market data published symbol=%s sequence_no=%s",
                event.symbol,
                event.sequence_no,
            )

    def run_api(self) -> None:
        """Push / REST 由来のイベントを publish する。"""

        if self._running_api:
            return
        self.logger.info("external data api mode starting")
        if self.push_client is not None:
            self.push_client.on_event = self.event_bus.publish
            self.push_client.start()
        if self.rest_poller is not None:
            self.rest_poller.on_event = self.event_bus.publish
            self.rest_poller.start()
        self._running_api = True

    def sync_orders_once(self, force_refresh: bool = False) -> int:
        """REST から注文状態を再取得し、イベントとして publish する。

        Args:
            force_refresh: True の場合はキャッシュを使わず再取得する。
        Returns:
            publishした注文状態イベント数。
        """

        if self.rest_poller is None:
            self.logger.warning("order resync skipped because rest_poller is empty")
            return 0
        self.logger.info("order resync started")
        events = self.rest_poller.poll_once(force_refresh=force_refresh)
        for event in events:
            self.event_bus.publish(event)
        self.logger.info("order resync completed count=%s", len(events))
        return len(events)

    def stop(self) -> None:
        """Push / REST の外部データ取得を停止する。"""

        if self.push_client is not None:
            self.push_client.stop()
        if self.rest_poller is not None:
            self.rest_poller.stop()
        self._running_api = False

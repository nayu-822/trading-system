import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

from data_source.csv_loader import CsvMarketDataLoader
from data_source.kabu_api_client import KabuApiClient, KabuApiError
from data_source.push_client import PushClient
from data_source.rest_poller import RestPoller
from domain.enums import (
    DataSourceMode,
    EventSource,
    EventType,
    StrategyType,
    TradingMode,
)
from domain.events import EventFactory, SystemStartedPayload
from domain.models import SignalStrategyConfig, SystemConfig, TradingSymbolConfig
from infrastructure.clock import RealClock
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError, validate_config
from infrastructure.event_bus import EventBus
from infrastructure.file_storage import FileStorage
from infrastructure.logger import setup_logger
from processes.external_data_process import ExternalDataProcess
from processes.persistence_process import PersistenceProcess
from processes.signal_process import SignalProcess
from processes.snapshot_process import SnapshotProcess
from processes.trading_process import TradingProcess
from trading.live_order_gateway import LiveOrderGateway
from trading.mock_order_gateway import MockOrderGateway
from trading.order_gateway import OrderGateway

CONFIG_DIR = Path("config")
DEFAULT_CSV_PATH = Path("data/market_data.csv")
DEFAULT_LOG_LEVEL = "INFO"


@dataclass
class ApplicationRuntime:
    """各プロセスの起動・停止順序を管理する。"""

    config: SystemConfig
    event_bus: EventBus
    logger: logging.Logger
    signal_process: SignalProcess
    trading_process: TradingProcess
    persistence_process: PersistenceProcess
    snapshot_process: SnapshotProcess
    external_data_process: ExternalDataProcess
    clock: RealClock
    csv_path: Path | None = None
    restored_snapshot: bool = False
    _started: bool = False
    _stop_event: Event = field(default_factory=Event)

    def start(self) -> None:
        if self._started:
            return
        self._stop_event.clear()
        enabled_symbols = tuple(
            symbol.code for symbol in self.config.symbols if symbol.enabled
        )
        self.logger.info(
            "application starting mode=%s data_source_mode=%s trading_mode=%s symbols=%s",
            self.config.app.mode.value,
            self.config.app.data_source_mode.value,
            self.config.app.trading_mode.value,
            ",".join(enabled_symbols),
        )
        if self.config.app.trading_mode == TradingMode.LIVE:
            self.logger.warning(
                "LIVE MODE starting gateway=LiveOrderGateway symbols=%s",
                ",".join(enabled_symbols),
            )
        if self.config.app.recovery_enabled:
            self.restored_snapshot = self.snapshot_process.restore()
        self.logger.info("snapshot restore restored=%s", self.restored_snapshot)

        try:
            self.persistence_process.start()
            if self.config.app.snapshot_enabled:
                self.snapshot_process.start()
            self.trading_process.start()
            self.sync_orders_once()
            self.signal_process.start()
            self._publish_started_event()
            self._started = True
        except Exception:
            self.logger.exception("application startup failed")
            self.stop()
            raise

    def run_external_data(self) -> None:
        enabled_symbol = next(
            symbol for symbol in self.config.symbols if symbol.enabled
        )
        if self.config.app.data_source_mode == DataSourceMode.CSV:
            target_csv_path = self.csv_path or DEFAULT_CSV_PATH
            if target_csv_path.exists():
                self.external_data_process.run(
                    data_source_mode=self.config.app.data_source_mode,
                    csv_path=target_csv_path,
                    symbol=enabled_symbol.code,
                )
            else:
                self.logger.warning("csv data skipped path=%s", target_csv_path)
            return

        self.logger.info("api mode external data starting")
        self.external_data_process.run(
            data_source_mode=self.config.app.data_source_mode,
            csv_path=None,
            symbol=enabled_symbol.code,
        )

    def sync_orders_once(self) -> int:
        """API モード起動時に REST 由来の注文状態を一度だけ再同期する。"""

        if self.config.app.data_source_mode != DataSourceMode.API:
            return 0
        self.logger.info(
            "startup order resync requested restored_snapshot=%s",
            self.restored_snapshot,
        )
        try:
            synced_count = self.external_data_process.sync_orders_once()
        except Exception:
            self.logger.exception("startup order resync failed")
            if self.config.app.trading_mode == TradingMode.LIVE:
                raise
            return 0
        self.logger.info("startup order resync completed count=%s", synced_count)
        return synced_count

    def wait(self, stop_event: Event | None = None) -> None:
        """API モードでは停止要求まで待機し、CSV モードでは即時に戻る。"""

        if self.config.app.data_source_mode == DataSourceMode.CSV:
            return
        wait_event = stop_event or self._stop_event
        self.logger.info("api mode runtime waiting")
        wait_event.wait()

    def flush(self) -> None:
        self.persistence_process.flush()
        if self.config.app.snapshot_enabled:
            self.snapshot_process.flush()

    def stop(self) -> None:
        self._stop_event.set()
        self.external_data_process.stop()
        self.trading_process.stop()
        self.signal_process.stop()
        self.flush()
        self.snapshot_process.stop()
        self.persistence_process.stop()
        self._started = False

    def _publish_started_event(self) -> None:
        event_factory = EventFactory(source=EventSource.MAIN)
        started_event = event_factory.create(
            event_type=EventType.SYSTEM_STARTED,
            timestamp=self.clock.now(),
            symbol=None,
            payload=SystemStartedPayload(mode=self.config.app.mode.value),
        )
        self.event_bus.publish(started_event)


def initialize_application(
    config_dir: Path = CONFIG_DIR,
    csv_path: Path | None = None,
    block_api: bool = False,
) -> tuple[EventBus, logging.Logger]:
    """アプリケーションの最小基盤を初期化する。

    Args:
        config_dir: 設定ファイルを配置したディレクトリ。

    Returns:
        event_bus と logger。
    """

    config = load_config(config_dir)
    validate_config(config)

    runtime = build_application_runtime(
        config=config,
        csv_path=csv_path,
    )
    try:
        runtime.start()
        runtime.run_external_data()
        if block_api:
            runtime.wait()
        runtime.flush()
    except (Exception, KeyboardInterrupt):
        runtime.stop()
        raise
    runtime.logger.info(
        "application initialized mode=%s data_source_mode=%s trading_mode=%s",
        config.app.mode.value,
        config.app.data_source_mode.value,
        config.app.trading_mode.value,
    )
    return runtime.event_bus, runtime.logger


def build_application_runtime(
    config: SystemConfig,
    csv_path: Path | None = None,
) -> ApplicationRuntime:
    """設定済みのプロセス群を構築する。"""

    logger = setup_logger(config.app.log_level, process_name=EventSource.MAIN.value)
    event_bus = EventBus()
    clock = RealClock()
    storage = FileStorage()
    snapshot_dir = Path(config.app.snapshot_dir)
    signal_process = SignalProcess(
        event_bus=event_bus,
        strategy_configs=tuple(
            SignalStrategyConfig(
                symbol=symbol.code,
                strategy_type=StrategyType(symbol.strategy),
            )
            for symbol in config.symbols
            if symbol.enabled
        ),
        range_window=config.strategy.range.window,
    )
    trading_process = TradingProcess(
        event_bus=event_bus,
        lot_configs=tuple(
            TradingSymbolConfig(
                symbol=symbol.code,
                lot_size=symbol.lot_min,
            )
            for symbol in config.symbols
            if symbol.enabled
        ),
        order_gateway=_build_order_gateway(config=config, logger=logger),
    )
    persistence_process = PersistenceProcess(
        event_bus=event_bus,
        storage=storage,
        event_log_path=snapshot_dir / "events.jsonl",
    )
    snapshot_process = SnapshotProcess(
        event_bus=event_bus,
        trading_process=trading_process,
        storage=storage,
        snapshot_path=snapshot_dir / "trading_snapshot.json",
        event_threshold=1,
        interval_sec=config.app.snapshot_interval_sec,
    )
    external_data_process = _build_external_data_process(
        event_bus=event_bus,
        config=config,
        logger=logger,
    )
    return ApplicationRuntime(
        config=config,
        event_bus=event_bus,
        logger=logger,
        signal_process=signal_process,
        trading_process=trading_process,
        persistence_process=persistence_process,
        snapshot_process=snapshot_process,
        external_data_process=external_data_process,
        clock=clock,
        csv_path=csv_path,
    )


def _build_external_data_process(
    event_bus: EventBus,
    config: SystemConfig,
    logger: logging.Logger,
) -> ExternalDataProcess:
    api_client = KabuApiClient(config=config.app.kabu_api)
    rest_poller = None
    push_client = None
    if config.app.data_source_mode == DataSourceMode.API:
        push_client = PushClient(config=config.app.kabu_api)
        try:
            token = api_client.get_token()
            rest_poller = RestPoller(
                api_client=api_client,
                token=token,
                interval_sec=config.app.rest_poll_interval_sec,
            )
        except KabuApiError:
            logger.exception("kabu api initialization failed")
    return ExternalDataProcess(
        event_bus=event_bus,
        csv_loader=CsvMarketDataLoader(),
        push_client=push_client,
        rest_poller=rest_poller,
    )


def _build_order_gateway(config: SystemConfig, logger: logging.Logger) -> OrderGateway:
    if config.app.trading_mode == TradingMode.PAPER:
        return MockOrderGateway()
    if config.app.trading_mode == TradingMode.LIVE:
        enabled_symbols = tuple(
            symbol.code for symbol in config.symbols if symbol.enabled
        )
        logger.warning(
            "LIVE MODE gateway selected gateway=LiveOrderGateway symbols=%s",
            ",".join(enabled_symbols),
        )
        api_client = KabuApiClient(config=config.app.kabu_api)
        token = api_client.get_token()
        return LiveOrderGateway(
            api_client=api_client,
            token=token,
            allowed_symbols=enabled_symbols,
        )
    raise ValueError(f"unsupported trading_mode={config.app.trading_mode.value}")


def main() -> int:
    """設定読込・検証・基盤初期化を実行する。

    Args:
        なし。

    Returns:
        終了コード。
    """

    try:
        config = load_config(CONFIG_DIR)
        logger = setup_logger(config.app.log_level, process_name=EventSource.MAIN.value)
        initialize_application(block_api=True)
    except KeyboardInterrupt:
        logger.info("application interrupted")
        return 0
    except ConfigLoadError:
        # 設定読込前の障害だけは、設定値を参照できないため固定レベルを使う。
        logger = setup_logger(DEFAULT_LOG_LEVEL, process_name=EventSource.MAIN.value)
        logger.exception("application initialization failed")
        return 1
    except (ConfigValidationError, ValueError, TypeError):
        logger.exception("application initialization failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

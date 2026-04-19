import logging
from pathlib import Path

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


def initialize_application(
    config_dir: Path = CONFIG_DIR,
    csv_path: Path | None = None,
) -> tuple[EventBus, logging.Logger]:
    """アプリケーションの最小基盤を初期化する。

    Args:
        config_dir: 設定ファイルを配置したディレクトリ。

    Returns:
        event_bus と logger。
    """

    config = load_config(config_dir)
    validate_config(config)

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
        order_gateway=_build_order_gateway(config.app.trading_mode),
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
        interval_sec=config.app.snapshot_interval_sec,
    )
    if config.app.recovery_enabled:
        snapshot_process.restore()
    persistence_process.start()
    signal_process.start()
    trading_process.start()
    if config.app.snapshot_enabled:
        snapshot_process.start()

    event_factory = EventFactory(source=EventSource.MAIN)
    started_event = event_factory.create(
        event_type=EventType.SYSTEM_STARTED,
        timestamp=clock.now(),
        symbol=None,
        payload=SystemStartedPayload(mode=config.app.mode.value),
    )
    event_bus.publish(started_event)

    target_csv_path = csv_path or DEFAULT_CSV_PATH
    enabled_symbol = next(symbol for symbol in config.symbols if symbol.enabled)
    external_data_process = _build_external_data_process(
        event_bus=event_bus,
        config=config,
        logger=logger,
    )
    if config.app.data_source_mode == DataSourceMode.CSV and target_csv_path.exists():
        external_data_process.run(
            data_source_mode=config.app.data_source_mode,
            csv_path=target_csv_path,
            symbol=enabled_symbol.code,
        )
    elif config.app.data_source_mode == DataSourceMode.API:
        external_data_process.run(
            data_source_mode=config.app.data_source_mode,
            csv_path=None,
            symbol=enabled_symbol.code,
        )

    persistence_process.flush()
    if config.app.snapshot_enabled:
        snapshot_process.flush()

    logger.info("application initialized mode=%s", config.app.mode.value)
    return event_bus, logger


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


def _build_order_gateway(trading_mode: TradingMode) -> OrderGateway:
    if trading_mode == TradingMode.PAPER:
        return MockOrderGateway()
    if trading_mode == TradingMode.LIVE:
        return LiveOrderGateway()
    raise ValueError(f"unsupported trading_mode={trading_mode.value}")


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
        initialize_application()
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

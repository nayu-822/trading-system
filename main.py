import logging
from pathlib import Path

from data_source.csv_loader import CsvMarketDataLoader
from domain.enums import EventSource, EventType, StrategyType
from domain.events import EventFactory, SystemStartedPayload
from domain.models import SignalStrategyConfig, TradingSymbolConfig
from infrastructure.clock import RealClock
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError, validate_config
from infrastructure.event_bus import EventBus
from infrastructure.logger import setup_logger
from processes.external_data_process import ExternalDataProcess
from processes.signal_process import SignalProcess
from processes.trading_process import TradingProcess

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
    )
    signal_process.start()
    trading_process.start()

    event_factory = EventFactory(source=EventSource.MAIN)
    started_event = event_factory.create(
        event_type=EventType.SYSTEM_STARTED,
        timestamp=clock.now(),
        symbol=None,
        payload=SystemStartedPayload(mode=config.app.mode.value),
    )
    event_bus.publish(started_event)

    target_csv_path = csv_path or DEFAULT_CSV_PATH
    if target_csv_path.exists():
        enabled_symbol = next(symbol for symbol in config.symbols if symbol.enabled)
        external_data_process = ExternalDataProcess(
            event_bus=event_bus,
            csv_loader=CsvMarketDataLoader(),
        )
        external_data_process.run_csv(
            csv_path=target_csv_path,
            symbol=enabled_symbol.code,
        )

    logger.info("application initialized mode=%s", config.app.mode.value)
    return event_bus, logger


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

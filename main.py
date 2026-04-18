import logging
from pathlib import Path

from domain.enums import EventSource, EventType
from domain.events import EventFactory
from infrastructure.clock import RealClock
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError, validate_config
from infrastructure.event_bus import EventBus
from infrastructure.logger import setup_logger

CONFIG_DIR = Path("config")
LOG_LEVEL = "INFO"


def initialize_application(
    config_dir: Path = CONFIG_DIR,
) -> tuple[EventBus, logging.Logger]:
    """アプリケーションの最小基盤を初期化する。

    Args:
        config_dir: 設定ファイルを配置したディレクトリ。

    Returns:
        event_bus と logger。
    """

    config = load_config(config_dir)
    validate_config(config)

    logger = setup_logger(LOG_LEVEL, process_name=EventSource.MAIN.value)
    event_bus = EventBus()
    clock = RealClock()
    event_factory = EventFactory(source=EventSource.MAIN)
    started_event = event_factory.create(
        event_type=EventType.SYSTEM_STARTED,
        timestamp=clock.now(),
        symbol=None,
        payload={"mode": config.app.mode.value},
    )
    event_bus.publish(started_event)
    logger.info("application initialized mode=%s", config.app.mode.value)
    return event_bus, logger


def main() -> int:
    """設定読込・検証・基盤初期化を実行する。

    Args:
        なし。

    Returns:
        終了コード。
    """

    logger = setup_logger(LOG_LEVEL, process_name=EventSource.MAIN.value)
    try:
        initialize_application()
    except (ConfigLoadError, ConfigValidationError, ValueError, TypeError):
        logger.exception("application initialization failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

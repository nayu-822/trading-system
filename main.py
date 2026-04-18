import logging
from pathlib import Path

from domain.enums import EventSource, EventType
from domain.events import BaseEvent
from infrastructure.clock import SystemClock
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError, validate_config
from infrastructure.event_bus import EventBus
from infrastructure.logger import setup_logger

CONFIG_DIR = Path("config")
LOG_LEVEL = "INFO"


def initialize_application(config_dir: Path = CONFIG_DIR) -> tuple[EventBus, logging.Logger]:
    """アプリケーションの最小基盤を初期化する。

    Args:
        config_dir: 設定ファイルを配置したディレクトリ。

    Returns:
        event_bus と logger。
    """

    config = load_config(config_dir)
    validate_config(config)

    logger = setup_logger(LOG_LEVEL)
    event_bus = EventBus()
    clock = SystemClock()
    event_bus.publish(
        BaseEvent(
            event_type=EventType.SYSTEM_STARTED,
            timestamp=clock.now(),
            source=EventSource.MAIN,
            symbol=None,
            payload={"mode": config.app.mode.value},
            sequence_no=1,
        )
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

    logger = setup_logger(LOG_LEVEL)
    try:
        initialize_application()
    except (ConfigLoadError, ConfigValidationError, ValueError) as error:
        logger.exception("application initialization failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

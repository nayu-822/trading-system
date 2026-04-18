import logging


def setup_logger(level: str) -> logging.Logger:
    """ルートロガーを初期化する。

    Args:
        level: ログレベル名。

    Returns:
        初期化済みロガー。
    """

    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger("trading_system")

import logging


class ProcessNameFilter(logging.Filter):
    """ログレコードへプロセス名を付与する。"""

    def __init__(self, process_name: str) -> None:
        super().__init__()
        self._process_name = process_name

    def filter(self, record: logging.LogRecord) -> bool:
        """process_name をログレコードへ設定する。

        Args:
            record: ログレコード。

        Returns:
            常に True。
        """

        record.process_name = self._process_name
        return True


def setup_logger(level: str, process_name: str = "main") -> logging.Logger:
    """ルートロガーを初期化する。

    Args:
        level: ログレベル名。
        process_name: ログに出力するプロセス名。

    Returns:
        初期化済みロガー。
    """

    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(process_name)s %(name)s %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(ProcessNameFilter(process_name))
    return logging.getLogger("trading_system")

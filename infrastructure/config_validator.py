from domain.models import SystemConfig


class ConfigValidationError(Exception):
    """設定検証に失敗したことを表す例外。"""


def validate_config(config: SystemConfig) -> None:
    """最小限の必須項目と値範囲を検証する。

    Args:
        config: 検証対象の統合設定。

    Returns:
        なし。
    """

    _validate_app_config(config)
    _validate_symbols(config)
    _validate_strategy(config)
    _validate_risk(config)


def _validate_app_config(config: SystemConfig) -> None:
    if config.app.rest_poll_interval_sec <= 0:
        raise ConfigValidationError("rest_poll_interval_sec は1以上で指定してください")
    if config.app.snapshot_interval_sec <= 0:
        raise ConfigValidationError("snapshot_interval_sec は1以上で指定してください")
    if config.app.snapshot_max_generations <= 0:
        raise ConfigValidationError("snapshot_max_generations は1以上で指定してください")
    if not config.app.snapshot_dir:
        raise ConfigValidationError("snapshot_dir は必須です")


def _validate_symbols(config: SystemConfig) -> None:
    if not config.symbols:
        raise ConfigValidationError("symbols は1件以上指定してください")
    for symbol in config.symbols:
        if not symbol.code:
            raise ConfigValidationError("銘柄コードは必須です")
        if not symbol.strategy:
            raise ConfigValidationError("銘柄ごとの strategy は必須です")
        if symbol.allocation_ratio <= 0:
            raise ConfigValidationError("allocation_ratio は0より大きく指定してください")
        if symbol.lot_size <= 0:
            raise ConfigValidationError("lot_size は1以上で指定してください")


def _validate_strategy(config: SystemConfig) -> None:
    if not config.strategy.default_strategy:
        raise ConfigValidationError("default_strategy は必須です")


def _validate_risk(config: SystemConfig) -> None:
    if config.risk.max_daily_loss <= 0:
        raise ConfigValidationError("max_daily_loss は0より大きく指定してください")
    if config.risk.max_consecutive_losses <= 0:
        raise ConfigValidationError("max_consecutive_losses は1以上で指定してください")
    if config.risk.order_timeout_sec <= 0:
        raise ConfigValidationError("order_timeout_sec は1以上で指定してください")

from domain.models import SystemConfig


class ConfigValidationError(Exception):
    """設定検証に失敗したことを表す例外。"""


def validate_config(config: SystemConfig) -> None:
    """必須項目と値の整合性を検証する。

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
        raise ConfigValidationError(
            "snapshot_max_generations は1以上で指定してください"
        )
    if config.app.snapshot_debounce_sec < 0:
        raise ConfigValidationError("snapshot_debounce_sec は0以上で指定してください")
    if not config.app.snapshot_dir:
        raise ConfigValidationError("snapshot_dir は必須です")


def _validate_symbols(config: SystemConfig) -> None:
    if not config.symbols:
        raise ConfigValidationError("symbols は1件以上指定してください")

    symbol_codes: set[str] = set()
    allocation_total = 0.0
    for symbol in config.symbols:
        if not symbol.code:
            raise ConfigValidationError("銘柄コードは必須です")
        if symbol.code in symbol_codes:
            raise ConfigValidationError(f"銘柄コードが重複しています: {symbol.code}")
        symbol_codes.add(symbol.code)

        if symbol.strategy not in _available_strategies():
            raise ConfigValidationError(
                f"未定義の strategy が指定されています: {symbol.strategy}"
            )
        if not 0 < symbol.allocation_ratio <= 1:
            raise ConfigValidationError(
                "allocation_ratio は0より大きく1以下で指定してください"
            )
        if symbol.lot_size <= 0:
            raise ConfigValidationError("lot_size は1以上で指定してください")

        overrides = symbol.overrides
        if (
            overrides.strategy is not None
            and overrides.strategy not in _available_strategies()
        ):
            raise ConfigValidationError(
                f"未定義の override strategy が指定されています: {overrides.strategy}"
            )
        if (
            overrides.allocation_ratio is not None
            and not 0 < overrides.allocation_ratio <= 1
        ):
            raise ConfigValidationError(
                "override allocation_ratio は0より大きく1以下で指定してください"
            )
        if overrides.lot_size is not None and overrides.lot_size <= 0:
            raise ConfigValidationError("override lot_size は1以上で指定してください")

        allocation_total += symbol.allocation_ratio

    if allocation_total > 1:
        raise ConfigValidationError("allocation_ratio の合計は1以下で指定してください")


def _validate_strategy(config: SystemConfig) -> None:
    if config.strategy.default_strategy not in _available_strategies():
        raise ConfigValidationError(
            f"未定義の default_strategy が指定されています: {config.strategy.default_strategy}"
        )
    if config.strategy.trend.short_window <= 0:
        raise ConfigValidationError("trend.short_window は1以上で指定してください")
    if config.strategy.trend.long_window <= 0:
        raise ConfigValidationError("trend.long_window は1以上で指定してください")
    if config.strategy.trend.short_window >= config.strategy.trend.long_window:
        raise ConfigValidationError(
            "trend.short_window は trend.long_window より小さく指定してください"
        )
    if config.strategy.range.window <= 0:
        raise ConfigValidationError("range.window は1以上で指定してください")


def _validate_risk(config: SystemConfig) -> None:
    if config.risk.max_daily_loss <= 0:
        raise ConfigValidationError("max_daily_loss は0より大きく指定してください")
    if config.risk.max_consecutive_losses <= 0:
        raise ConfigValidationError("max_consecutive_losses は1以上で指定してください")
    if config.risk.order_timeout_sec <= 0:
        raise ConfigValidationError("order_timeout_sec は1以上で指定してください")
    if config.risk.trading_start_time >= config.risk.trading_end_time:
        raise ConfigValidationError(
            "trading_start_time は trading_end_time より前にしてください"
        )


def _available_strategies() -> set[str]:
    return {"trend", "range"}

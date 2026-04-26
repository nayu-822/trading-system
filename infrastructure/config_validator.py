import os

from domain.enums import DataSourceMode, TradingMode
from domain.models import SystemConfig

LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
STRATEGIES = {"trend", "range", "auto"}
DATA_SOURCE_MODES = {"csv", "api"}
TRADING_MODES = {"paper", "live"}
KABU_API_ENVIRONMENTS = {"paper", "live"}


class ConfigValidationError(Exception):
    """設定検証に失敗したことを表す例外。"""


def validate_config(
    config: SystemConfig,
    allow_missing_api_password: bool = False,
) -> None:
    """構成項目と値の妥当性を検証する。
    Args:
        config: 検証対象のシステム設定。
    Returns:
        なし
    Raises:
        ConfigValidationError: 設定値が不正な場合。
    """

    _validate_app_config(
        config=config,
        allow_missing_api_password=allow_missing_api_password,
    )
    _validate_symbols(config)
    _validate_strategy(config)
    _validate_risk(config)


def _validate_app_config(
    config: SystemConfig,
    allow_missing_api_password: bool = False,
) -> None:
    """app 設定の妥当性を検証する。
    Args:
        config: 検証対象のシステム設定。
    Returns:
        なし
    Raises:
        ConfigValidationError: app 設定が不正な場合。
    """

    if config.app.log_level not in LOG_LEVELS:
        raise ConfigValidationError("log_level は有効な logging レベルで指定してください")
    if config.app.data_source_mode.value not in DATA_SOURCE_MODES:
        raise ConfigValidationError("data_source_mode は csv / api で指定してください")
    if config.app.trading_mode.value not in TRADING_MODES:
        raise ConfigValidationError("trading_mode は paper / live で指定してください")
    if config.app.kabu_api.environment.value not in KABU_API_ENVIRONMENTS:
        raise ConfigValidationError(
            "kabu_api_environment は paper または live を指定してください"
        )
    if (
        config.app.trading_mode == TradingMode.LIVE
        and config.app.data_source_mode != DataSourceMode.API
    ):
        raise ConfigValidationError("live mode requires api data_source_mode")
    if config.app.trading_mode == TradingMode.LIVE and not config.app.live_enabled:
        raise ConfigValidationError("live mode requires live_enabled=true")
    if (
        not allow_missing_api_password
        and config.app.data_source_mode == DataSourceMode.API
        and not os.environ.get(config.app.kabu_api.token_env_name)
    ):
        raise ConfigValidationError(
            f"API mode requires env: {config.app.kabu_api.token_env_name}"
        )
    if config.app.rest_poll_interval_sec <= 0:
        raise ConfigValidationError("rest_poll_interval_sec は1以上で指定してください")
    if config.app.max_order_quantity < 1:
        raise ConfigValidationError("max_order_quantity は1以上で指定してください")
    if config.app.position_average_price_tolerance < 0:
        raise ConfigValidationError(
            "position_average_price_tolerance は0以上で指定してください"
        )
    if not config.app.trade_symbols:
        raise ConfigValidationError("trade_symbols は1件以上指定してください")
    if any(not symbol for symbol in config.app.trade_symbols):
        raise ConfigValidationError("trade_symbols に空文字は指定できません")
    if config.app.kabu_api.timeout_sec < 1:
        raise ConfigValidationError("api_timeout_sec は1以上で指定してください")
    if not config.app.kabu_api.base_url:
        raise ConfigValidationError("kabu_api_base_url は空で指定できません")
    if not config.app.kabu_api.push_url:
        raise ConfigValidationError("kabu_push_url は空で指定できません")
    if not config.app.kabu_api.token_env_name:
        raise ConfigValidationError("token_env_name は空で指定できません")
    if config.app.snapshot_interval_sec <= 0:
        raise ConfigValidationError("snapshot_interval_sec は1以上で指定してください")
    if config.app.snapshot_max_generations <= 0:
        raise ConfigValidationError(
            "snapshot_max_generations は1以上で指定してください"
        )
    if config.app.snapshot_debounce_sec < 0:
        raise ConfigValidationError("snapshot_debounce_sec は0以上で指定してください")
    if not config.app.snapshot_dir:
        raise ConfigValidationError("snapshot_dir は空で指定できません")


def _validate_symbols(config: SystemConfig) -> None:
    """symbols 設定の妥当性を検証する。
    Args:
        config: 検証対象のシステム設定。
    Returns:
        なし
    Raises:
        ConfigValidationError: symbols 設定が不正な場合。
    """

    if not config.symbols:
        raise ConfigValidationError("symbols は1件以上指定してください")

    symbol_codes: set[str] = set()
    allocation_total = 0.0
    enabled_count = 0
    for symbol in config.symbols:
        if not symbol.code:
            raise ConfigValidationError("銘柄コードは必須です")
        if symbol.code in symbol_codes:
            raise ConfigValidationError(f"銘柄コードが重複しています: {symbol.code}")
        symbol_codes.add(symbol.code)

        if not symbol.enabled:
            continue
        enabled_count += 1

        if symbol.strategy not in _available_strategies():
            raise ConfigValidationError(
                f"未対応の strategy が指定されています: {symbol.strategy}"
            )
        if not 0 < symbol.allocation_ratio <= 1:
            raise ConfigValidationError("allocation_ratio は0より大きく1以下で指定してください")
        if symbol.lot_min <= 0:
            raise ConfigValidationError("lot_min は1以上で指定してください")
        if symbol.lot_max < symbol.lot_min:
            raise ConfigValidationError("lot_max は lot_min 以上で指定してください")
        if symbol.lot_multiplier <= 0:
            raise ConfigValidationError("lot_multiplier は0より大きく指定してください")

        overrides = symbol.strategy_params_override
        if (
            overrides.allocation_ratio is not None
            and not 0 < overrides.allocation_ratio <= 1
        ):
            raise ConfigValidationError(
                "override allocation_ratio は0より大きく1以下で指定してください"
            )
        if overrides.lot_min is not None and overrides.lot_min <= 0:
            raise ConfigValidationError("override lot_min は1以上で指定してください")
        if (
            overrides.lot_min is not None
            and overrides.lot_max is not None
            and overrides.lot_max < overrides.lot_min
        ):
            raise ConfigValidationError(
                "override lot_max は lot_min 以上で指定してください"
            )
        if overrides.lot_multiplier is not None and overrides.lot_multiplier <= 0:
            raise ConfigValidationError(
                "override lot_multiplier は0より大きく指定してください"
            )

        effective_range_window = overrides.strategy_params.get("range_window")
        if effective_range_window is not None and int(effective_range_window) <= 0:
            raise ConfigValidationError("override range_window は1以上で指定してください")

        effective_short_window = int(
            overrides.strategy_params.get(
                "trend_short_window",
                config.strategy.trend.short_window,
            )
        )
        effective_long_window = int(
            overrides.strategy_params.get(
                "trend_long_window",
                config.strategy.trend.long_window,
            )
        )
        if effective_short_window <= 0:
            raise ConfigValidationError(
                "override trend_short_window は1以上で指定してください"
            )
        if effective_long_window <= 0:
            raise ConfigValidationError(
                "override trend_long_window は1以上で指定してください"
            )
        if effective_short_window >= effective_long_window:
            raise ConfigValidationError(
                "override trend_short_window は trend_long_window より小さく指定してください"
            )

        allocation_total += symbol.allocation_ratio

    if enabled_count == 0:
        raise ConfigValidationError("enabled な symbols は1件以上指定してください")
    if allocation_total > 1:
        raise ConfigValidationError("allocation_ratio の合計は1以下で指定してください")
    for trade_symbol in config.app.trade_symbols:
        if trade_symbol not in symbol_codes:
            raise ConfigValidationError(
                f"trade_symbols に symbols に存在しない銘柄があります: {trade_symbol}"
            )


def _validate_strategy(config: SystemConfig) -> None:
    """strategy 設定の妥当性を検証する。
    Args:
        config: 検証対象のシステム設定。
    Returns:
        なし
    Raises:
        ConfigValidationError: strategy 設定が不正な場合。
    """

    if config.strategy.default_strategy not in _available_strategies():
        raise ConfigValidationError(
            f"未対応の default_strategy が指定されています: {config.strategy.default_strategy}"
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
    """risk 設定の妥当性を検証する。
    Args:
        config: 検証対象のシステム設定。
    Returns:
        なし
    Raises:
        ConfigValidationError: risk 設定が不正な場合。
    """

    if config.risk.max_daily_loss <= 0:
        raise ConfigValidationError("max_daily_loss は0より大きく指定してください")
    if config.risk.max_consecutive_losses <= 0:
        raise ConfigValidationError("max_consecutive_losses は1以上で指定してください")
    if config.risk.resume_consecutive_wins <= 0:
        raise ConfigValidationError("resume_consecutive_wins は1以上で指定してください")
    if config.risk.max_positions <= 0:
        raise ConfigValidationError("max_positions は1以上で指定してください")
    if config.risk.max_position_per_symbol <= 0:
        raise ConfigValidationError("max_position_per_symbol は1以上で指定してください")
    if config.risk.account_equity <= 0:
        raise ConfigValidationError("account_equity は0より大きく指定してください")
    if config.risk.max_drawdown <= 0:
        raise ConfigValidationError("max_drawdown は0より大きく指定してください")
    if config.risk.api_error_limit <= 0:
        raise ConfigValidationError("api_error_limit は1以上で指定してください")
    if config.risk.order_timeout_sec <= 0:
        raise ConfigValidationError("order_timeout_sec は1以上で指定してください")
    if config.risk.trading_start_time >= config.risk.trading_end_time:
        raise ConfigValidationError(
            "trading_start_time は trading_end_time より前に指定してください"
        )


def _available_strategies() -> set[str]:
    """利用可能な戦略名一覧を返す。
    Args:
        なし
    Returns:
        set[str]: 許可する戦略名の集合。
    """

    return STRATEGIES

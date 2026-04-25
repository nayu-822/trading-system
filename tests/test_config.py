from dataclasses import replace
from pathlib import Path

import pytest

from domain.enums import DataSourceMode, KabuApiEnvironment, RunMode, TradingMode
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError, validate_config
from main import LiveOrderNotAllowedError, _ensure_live_order_allowed


def test_load_config_returns_structured_model() -> None:
    config = load_config(Path("config"))

    assert config.app.mode == RunMode.MOCK
    assert config.app.trading_mode == TradingMode.PAPER
    assert config.app.live_enabled is False
    assert config.app.data_source_mode == DataSourceMode.CSV
    assert config.app.log_level == "INFO"
    assert config.app.kabu_api.environment == KabuApiEnvironment.PAPER
    assert config.app.kabu_api.base_url == "http://localhost:18081/kabusapi"
    assert config.app.kabu_api.push_url == "ws://localhost:18081/kabusapi/websocket"
    assert config.app.kabu_api.timeout_sec == 5
    assert config.app.kabu_api.token_env_name == "KABU_API_PASSWORD"
    assert config.symbols[0].code == "7203"
    assert config.symbols[0].enabled is True
    assert config.symbols[0].strategy == "auto"
    assert config.symbols[0].lot_min == 100
    assert config.symbols[0].lot_max == 100
    assert config.symbols[0].strategy_params_override.strategy_params == {}
    assert config.strategy.default_strategy == "auto"
    assert config.strategy.auto.enabled is True
    assert config.strategy.trend.short_window == 5
    assert config.strategy.range.window == 20
    assert config.risk.max_consecutive_losses == 3
    assert config.risk.resume_consecutive_wins == 2
    assert config.risk.max_positions == 3
    assert config.risk.account_equity == 1000000
    assert config.risk.kill_switch_enabled is True
    assert config.risk.api_error_limit == 5


def test_load_config_raises_when_file_missing() -> None:
    with pytest.raises(ConfigLoadError):
        load_config(Path("missing_config"))


def test_validate_config_accepts_sample_config() -> None:
    config = load_config(Path("config"))

    validate_config(config)


def test_validate_config_accepts_api_paper_when_token_env_exists(monkeypatch) -> None:
    config = load_config(Path("config"))
    api_app = replace(
        config.app,
        data_source_mode=DataSourceMode.API,
        trading_mode=TradingMode.PAPER,
    )
    valid_config = replace(config, app=api_app)
    monkeypatch.setenv(config.app.kabu_api.token_env_name, "password")

    validate_config(valid_config)


def test_validate_config_rejects_api_mode_without_token_env(monkeypatch) -> None:
    config = load_config(Path("config"))
    api_app = replace(
        config.app,
        data_source_mode=DataSourceMode.API,
        trading_mode=TradingMode.PAPER,
    )
    invalid_config = replace(config, app=api_app)
    monkeypatch.delenv(config.app.kabu_api.token_env_name, raising=False)

    with pytest.raises(ConfigValidationError):
        validate_config(invalid_config)


def test_validate_config_rejects_api_live_when_live_disabled(monkeypatch) -> None:
    config = load_config(Path("config"))
    api_live_app = replace(
        config.app,
        data_source_mode=DataSourceMode.API,
        trading_mode=TradingMode.LIVE,
        live_enabled=False,
    )
    invalid_config = replace(config, app=api_live_app)
    monkeypatch.setenv(config.app.kabu_api.token_env_name, "password")

    with pytest.raises(ConfigValidationError):
        validate_config(invalid_config)


def test_validate_config_rejects_csv_live_mode(monkeypatch) -> None:
    config = load_config(Path("config"))
    csv_live_app = replace(
        config.app,
        data_source_mode=DataSourceMode.CSV,
        trading_mode=TradingMode.LIVE,
        live_enabled=True,
    )
    invalid_config = replace(config, app=csv_live_app)
    monkeypatch.setenv(config.app.kabu_api.token_env_name, "password")

    with pytest.raises(ConfigValidationError):
        validate_config(invalid_config)


def test_validate_config_accepts_api_live_when_enabled(monkeypatch) -> None:
    config = load_config(Path("config"))
    api_live_app = replace(
        config.app,
        data_source_mode=DataSourceMode.API,
        trading_mode=TradingMode.LIVE,
        live_enabled=True,
    )
    valid_config = replace(config, app=api_live_app)
    monkeypatch.setenv(config.app.kabu_api.token_env_name, "password")

    validate_config(valid_config)


def test_validate_config_rejects_empty_symbols() -> None:
    config = load_config(Path("config"))
    invalid_config = replace(config, symbols=[])

    with pytest.raises(ConfigValidationError):
        validate_config(invalid_config)


def test_validate_config_rejects_duplicate_symbol_code() -> None:
    config = load_config(Path("config"))
    invalid_config = replace(config, symbols=[config.symbols[0], config.symbols[0]])

    with pytest.raises(ConfigValidationError):
        validate_config(invalid_config)


def test_validate_config_rejects_invalid_strategy_window() -> None:
    config = load_config(Path("config"))
    invalid_strategy = replace(
        config.strategy, trend=replace(config.strategy.trend, short_window=30)
    )
    invalid_config = replace(config, strategy=invalid_strategy)

    with pytest.raises(ConfigValidationError):
        validate_config(invalid_config)


def test_validate_config_accepts_auto_strategy() -> None:
    config = load_config(Path("config"))
    auto_symbol = replace(config.symbols[0], strategy="auto")
    auto_strategy = replace(config.strategy, default_strategy="auto")
    valid_config = replace(config, symbols=[auto_symbol], strategy=auto_strategy)

    validate_config(valid_config)


def test_validate_config_rejects_invalid_log_level() -> None:
    config = load_config(Path("config"))
    invalid_config = replace(config, app=replace(config.app, log_level="NOTICE"))

    with pytest.raises(ConfigValidationError):
        validate_config(invalid_config)


def test_validate_config_rejects_invalid_lot_range() -> None:
    config = load_config(Path("config"))
    invalid_symbol = replace(config.symbols[0], lot_min=200, lot_max=100)
    invalid_config = replace(config, symbols=[invalid_symbol])

    with pytest.raises(ConfigValidationError):
        validate_config(invalid_config)


def test_validate_config_rejects_allocation_total_over_one() -> None:
    config = load_config(Path("config"))
    first_symbol = config.symbols[0]
    second_symbol = replace(first_symbol, code="6758", allocation_ratio=0.5)
    invalid_config = replace(config, symbols=[first_symbol, second_symbol])

    with pytest.raises(ConfigValidationError):
        validate_config(invalid_config)


def test_load_config_defaults_environment_to_paper_when_trading_mode_is_paper() -> None:
    app_config = _build_test_app_config(
        trading_mode=TradingMode.PAPER,
        kabu_api_environment=None,
    )

    assert app_config.kabu_api.environment == KabuApiEnvironment.PAPER


def test_load_config_defaults_environment_to_live_when_trading_mode_is_live() -> None:
    app_config = _build_test_app_config(
        trading_mode=TradingMode.LIVE,
        kabu_api_environment=None,
    )

    assert app_config.kabu_api.environment == KabuApiEnvironment.LIVE


def test_load_config_sets_paper_port_when_environment_is_paper() -> None:
    app_config = _build_test_app_config(kabu_api_environment="paper")

    assert app_config.kabu_api.base_url == "http://localhost:18081/kabusapi"
    assert app_config.kabu_api.push_url == "ws://localhost:18081/kabusapi/websocket"


def test_load_config_sets_live_port_when_environment_is_live() -> None:
    app_config = _build_test_app_config(kabu_api_environment="live")

    assert app_config.kabu_api.base_url == "http://localhost:18080/kabusapi"
    assert app_config.kabu_api.push_url == "ws://localhost:18080/kabusapi/websocket"


def test_load_config_prioritizes_explicit_base_url() -> None:
    app_config = _build_test_app_config(
        kabu_api_environment="paper",
        kabu_api_base_url="http://example.local/custom",
    )

    assert app_config.kabu_api.base_url == "http://example.local/custom"


@pytest.mark.parametrize(
    ("trading_mode", "environment", "allowed"),
    [
        (TradingMode.LIVE, KabuApiEnvironment.LIVE, True),
        (TradingMode.LIVE, KabuApiEnvironment.PAPER, False),
        (TradingMode.PAPER, KabuApiEnvironment.LIVE, False),
        (TradingMode.PAPER, KabuApiEnvironment.PAPER, False),
    ],
)
def test_live_order_permission_matrix(
    trading_mode: TradingMode,
    environment: KabuApiEnvironment,
    allowed: bool,
) -> None:
    config = load_config(Path("config"))
    target_config = replace(
        config,
        app=replace(
            config.app,
            trading_mode=trading_mode,
            kabu_api=replace(config.app.kabu_api, environment=environment),
        ),
    )

    if allowed:
        _ensure_live_order_allowed(target_config)
        return

    with pytest.raises(LiveOrderNotAllowedError):
        _ensure_live_order_allowed(target_config)


def _build_test_app_config(
    trading_mode: TradingMode = TradingMode.PAPER,
    kabu_api_environment: str | None = None,
    kabu_api_base_url: str | None = None,
):
    from infrastructure.config_loader import _build_app_config

    data = {
        "mode": "mock",
        "trading_mode": trading_mode.value,
        "live_enabled": False,
        "data_source_mode": "csv",
        "log_level": "INFO",
        "rest_poll_interval_sec": 5,
        "push_enabled": False,
        "api_timeout_sec": 5,
        "token_env_name": "KABU_API_PASSWORD",
        "snapshot_enabled": False,
        "snapshot_dir": "snapshots",
        "snapshot_interval_sec": 60,
        "snapshot_max_generations": 3,
        "snapshot_debounce_sec": 5,
        "recovery_enabled": False,
        "recovery_mode": "manual",
        "startup_reconcile_enabled": False,
    }
    if kabu_api_environment is not None:
        data["kabu_api_environment"] = kabu_api_environment
    if kabu_api_base_url is not None:
        data["kabu_api_base_url"] = kabu_api_base_url
    return _build_app_config(data)

from dataclasses import replace
from pathlib import Path

import pytest

from domain.enums import RunMode
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError, validate_config


def test_load_config_returns_structured_model() -> None:
    config = load_config(Path("config"))

    assert config.app.mode == RunMode.MOCK
    assert config.app.log_level == "INFO"
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


def test_load_config_raises_when_file_missing() -> None:
    with pytest.raises(ConfigLoadError):
        load_config(Path("missing_config"))


def test_validate_config_accepts_sample_config() -> None:
    config = load_config(Path("config"))

    validate_config(config)


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

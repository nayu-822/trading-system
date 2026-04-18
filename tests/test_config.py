from dataclasses import replace
from pathlib import Path

import pytest

from domain.enums import RunMode
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError, validate_config


def test_load_config_returns_structured_model() -> None:
    config = load_config(Path("config"))

    assert config.app.mode == RunMode.MOCK
    assert config.symbols[0].code == "7203"
    assert config.strategy.default_strategy == "trend"
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

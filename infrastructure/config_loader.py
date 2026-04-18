from pathlib import Path
from typing import Any

from domain.enums import RunMode
from domain.models import (
    AppConfig,
    RangeStrategyConfig,
    RiskConfig,
    StrategyConfig,
    SymbolConfig,
    SymbolOverrideConfig,
    SystemConfig,
    TrendStrategyConfig,
)


class ConfigLoadError(Exception):
    """設定読込に失敗したことを表す例外。"""


def load_config(config_dir: Path) -> SystemConfig:
    """設定ディレクトリから全設定を読み込む。

    Args:
        config_dir: 設定ファイルを配置したディレクトリ。

    Returns:
        統合済み設定モデル。
    """

    app_data = _load_yaml(config_dir / "app.yaml")
    symbols_data = _load_yaml(config_dir / "symbols.yaml")
    strategy_data = _load_yaml(config_dir / "strategy.yaml")
    risk_data = _load_yaml(config_dir / "risk.yaml")

    return SystemConfig(
        app=_build_app_config(app_data),
        symbols=_build_symbol_configs(symbols_data),
        strategy=_build_strategy_config(strategy_data),
        risk=_build_risk_config(risk_data),
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    """YAMLファイルを最小構文で読み込む。

    Args:
        path: 読込対象ファイル。

    Returns:
        読み込んだ辞書。
    """

    if not path.exists():
        raise ConfigLoadError(f"設定ファイルが見つかりません: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigLoadError(f"設定ファイルを読み込めません: {path}") from error

    return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """サンプル設定で使うYAMLサブセットを辞書化する。

    Args:
        text: YAML文字列。

    Returns:
        辞書化した設定。
    """

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any] | list[Any]]] = [(-1, root)]

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        while indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]
        if stripped.startswith("- "):
            if not isinstance(parent, list):
                raise ConfigLoadError(f"リストの位置が不正です: line={line_no}")
            item = _parse_list_item(stripped[2:])
            parent.append(item)
            if isinstance(item, dict):
                stack.append((indent, item))
            continue

        key, value = _split_key_value(stripped, line_no)
        if not isinstance(parent, dict):
            raise ConfigLoadError(f"マッピングの位置が不正です: line={line_no}")

        if value == "":
            next_container = _guess_container(text, line_no, indent)
            parent[key] = next_container
            stack.append((indent, next_container))
        else:
            parent[key] = _parse_scalar(value)

    return root


def _parse_list_item(
    item_text: str,
) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
    if ": " in item_text or item_text.endswith(":"):
        key, value = _split_key_value(item_text, 0)
        return {key: _parse_scalar(value) if value else {}}
    return _parse_scalar(item_text)


def _split_key_value(text: str, line_no: int) -> tuple[str, str]:
    if ":" not in text:
        raise ConfigLoadError(f"key: value 形式ではありません: line={line_no}")
    key, value = text.split(":", 1)
    return key.strip(), value.strip()


def _guess_container(
    text: str, current_line_no: int, current_indent: int
) -> dict[str, Any] | list[Any]:
    for raw_line in text.splitlines()[current_line_no:]:
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= current_indent:
            return {}
        return [] if line.strip().startswith("- ") else {}
    return {}


def _parse_scalar(
    value: str,
) -> dict[str, Any] | list[Any] | str | int | float | bool | None:
    if value == "{}":
        return {}
    if value == "[]":
        return []
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _build_app_config(data: dict[str, Any]) -> AppConfig:
    return AppConfig(
        mode=RunMode(str(data["mode"])),
        rest_poll_interval_sec=int(data["rest_poll_interval_sec"]),
        push_enabled=bool(data["push_enabled"]),
        snapshot_enabled=bool(data["snapshot_enabled"]),
        snapshot_dir=str(data["snapshot_dir"]),
        snapshot_interval_sec=int(data["snapshot_interval_sec"]),
        snapshot_max_generations=int(data["snapshot_max_generations"]),
        snapshot_debounce_sec=int(data["snapshot_debounce_sec"]),
        recovery_enabled=bool(data["recovery_enabled"]),
        recovery_mode=str(data["recovery_mode"]),
        startup_reconcile_enabled=bool(data["startup_reconcile_enabled"]),
    )


def _build_symbol_configs(data: dict[str, Any]) -> list[SymbolConfig]:
    symbols = data["symbols"]
    if not isinstance(symbols, list):
        raise ConfigLoadError("symbols はリストで指定してください")
    return [
        SymbolConfig(
            code=str(item["code"]),
            name=str(item["name"]),
            market=str(item["market"]),
            strategy=str(item["strategy"]),
            allocation_ratio=float(item["allocation_ratio"]),
            lot_size=int(item["lot_size"]),
            overrides=_build_symbol_override_config(dict(item.get("overrides", {}))),
        )
        for item in symbols
    ]


def _build_symbol_override_config(data: dict[str, Any]) -> SymbolOverrideConfig:
    return SymbolOverrideConfig(
        strategy=str(data["strategy"]) if data.get("strategy") is not None else None,
        allocation_ratio=(
            float(data["allocation_ratio"])
            if data.get("allocation_ratio") is not None
            else None
        ),
        lot_size=int(data["lot_size"]) if data.get("lot_size") is not None else None,
    )


def _build_strategy_config(data: dict[str, Any]) -> StrategyConfig:
    parameters = data["parameters"]
    trend = dict(parameters["trend"])
    range_config = dict(parameters["range"])
    return StrategyConfig(
        default_strategy=str(data["default_strategy"]),
        trend=TrendStrategyConfig(
            short_window=int(trend["short_window"]),
            long_window=int(trend["long_window"]),
        ),
        range=RangeStrategyConfig(window=int(range_config["window"])),
    )


def _build_risk_config(data: dict[str, Any]) -> RiskConfig:
    return RiskConfig(
        max_daily_loss=float(data["max_daily_loss"]),
        max_consecutive_losses=int(data["max_consecutive_losses"]),
        trading_start_time=str(data["trading_start_time"]),
        trading_end_time=str(data["trading_end_time"]),
        order_timeout_sec=int(data["order_timeout_sec"]),
    )

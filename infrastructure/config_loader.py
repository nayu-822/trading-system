from pathlib import Path
from typing import Any

from domain.enums import DataSourceMode, KabuApiEnvironment, RunMode, TradingMode
from domain.models import (
    AppConfig,
    AutoStrategyConfig,
    KabuApiConfig,
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
    trading_mode = TradingMode(str(data.get("trading_mode", "paper")))
    environment = _resolve_kabu_api_environment(
        data.get("kabu_api_environment"),
        trading_mode=trading_mode,
    )
    base_url = str(data.get("kabu_api_base_url") or _build_kabu_api_base_url(environment))
    push_url = str(data.get("kabu_push_url") or _build_kabu_push_url(environment))

    return AppConfig(
        mode=RunMode(str(data["mode"])),
        trading_mode=trading_mode,
        live_enabled=bool(data.get("live_enabled", False)),
        data_source_mode=DataSourceMode(str(data.get("data_source_mode", "csv"))),
        log_level=str(data["log_level"]).upper(),
        rest_poll_interval_sec=int(data["rest_poll_interval_sec"]),
        push_enabled=bool(data["push_enabled"]),
        max_order_quantity=int(data.get("max_order_quantity", 1)),
        trade_symbols=tuple(str(symbol) for symbol in data.get("trade_symbols", [])),
        position_reconciliation_enabled=bool(
            data.get("position_reconciliation_enabled", True)
        ),
        position_average_price_tolerance=float(
            data.get("position_average_price_tolerance", 0.01)
        ),
        kabu_api=KabuApiConfig(
            environment=environment,
            base_url=base_url,
            push_url=push_url,
            timeout_sec=int(data.get("api_timeout_sec", 5)),
            token_env_name=str(data.get("token_env_name", "KABU_API_PASSWORD")),
        ),
        snapshot_enabled=bool(data["snapshot_enabled"]),
        snapshot_dir=str(data["snapshot_dir"]),
        snapshot_interval_sec=int(data["snapshot_interval_sec"]),
        snapshot_max_generations=int(data["snapshot_max_generations"]),
        snapshot_debounce_sec=int(data["snapshot_debounce_sec"]),
        recovery_enabled=bool(data["recovery_enabled"]),
        recovery_mode=str(data["recovery_mode"]),
        startup_reconcile_enabled=bool(data["startup_reconcile_enabled"]),
    )


def _resolve_kabu_api_environment(
    value: Any,
    trading_mode: TradingMode,
) -> KabuApiEnvironment:
    if value is not None:
        return KabuApiEnvironment(str(value))
    if trading_mode == TradingMode.LIVE:
        return KabuApiEnvironment.LIVE
    return KabuApiEnvironment.PAPER


def _build_kabu_api_base_url(environment: KabuApiEnvironment) -> str:
    port = _resolve_kabu_api_port(environment)
    return f"http://localhost:{port}/kabusapi"


def _build_kabu_push_url(environment: KabuApiEnvironment) -> str:
    port = _resolve_kabu_api_port(environment)
    return f"ws://localhost:{port}/kabusapi/websocket"


def _resolve_kabu_api_port(environment: KabuApiEnvironment) -> int:
    if environment == KabuApiEnvironment.LIVE:
        return 18080
    return 18081


def _build_symbol_configs(data: dict[str, Any]) -> list[SymbolConfig]:
    symbols = data["symbols"]
    if not isinstance(symbols, list):
        raise ConfigLoadError("symbols はリストで指定してください")
    return [
        SymbolConfig(
            code=str(item["code"]),
            name=str(item["name"]),
            enabled=bool(item["enabled"]),
            market=str(item.get("market", "")),
            strategy=str(item["strategy"]),
            allocation_ratio=float(item["allocation_ratio"]),
            lot_min=int(item["lot_min"]),
            lot_max=int(item["lot_max"]),
            lot_multiplier=float(item["lot_multiplier"]),
            strategy_params_override=_build_symbol_override_config(
                dict(item.get("strategy_params_override", {}))
            ),
            note=str(item.get("note", "")),
        )
        for item in symbols
    ]


def _build_symbol_override_config(data: dict[str, Any]) -> SymbolOverrideConfig:
    return SymbolOverrideConfig(
        strategy_params=dict(data.get("params", {})),
        allocation_ratio=(
            float(data["allocation_ratio"])
            if data.get("allocation_ratio") is not None
            else None
        ),
        lot_min=int(data["lot_min"]) if data.get("lot_min") is not None else None,
        lot_max=int(data["lot_max"]) if data.get("lot_max") is not None else None,
        lot_multiplier=(
            float(data["lot_multiplier"])
            if data.get("lot_multiplier") is not None
            else None
        ),
    )


def _build_strategy_config(data: dict[str, Any]) -> StrategyConfig:
    parameters = data["parameters"]
    trend = dict(parameters["trend"])
    range_config = dict(parameters["range"])
    auto = dict(parameters.get("auto", {}))
    return StrategyConfig(
        default_strategy=str(data["default_strategy"]),
        trend=TrendStrategyConfig(
            short_window=int(trend["short_window"]),
            long_window=int(trend["long_window"]),
        ),
        range=RangeStrategyConfig(window=int(range_config["window"])),
        auto=AutoStrategyConfig(enabled=bool(auto.get("enabled", True))),
    )


def _build_risk_config(data: dict[str, Any]) -> RiskConfig:
    return RiskConfig(
        max_daily_loss=float(data["max_daily_loss"]),
        max_consecutive_losses=int(data["max_consecutive_losses"]),
        resume_consecutive_wins=int(data.get("resume_consecutive_wins", 2)),
        max_positions=int(data.get("max_positions", 1)),
        max_position_per_symbol=int(data.get("max_position_per_symbol", 100)),
        account_equity=float(data.get("account_equity", 0)),
        max_drawdown=float(data.get("max_drawdown", data["max_daily_loss"])),
        kill_switch_enabled=bool(data.get("kill_switch_enabled", True)),
        api_error_limit=int(data.get("api_error_limit", 5)),
        trading_start_time=str(data["trading_start_time"]),
        trading_end_time=str(data["trading_end_time"]),
        order_timeout_sec=int(data["order_timeout_sec"]),
    )

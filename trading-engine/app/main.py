from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
from typing import Any

from app.core.engine import DefaultMarketTimeChecker, Engine, EngineConfig, MarketTimeChecker, Strategy
from app.data.market_data import DummyMarketDataProvider
from app.execution.executor import Executor
from app.execution.paper_executor import PaperExecutor
from app.logging.trade_logger import TradeLogger
from app.strategies.range.range_strategy import RangeStrategy
from app.strategies.trend.trend_strategy import TrendStrategy


DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parent / "config" / "settings.yaml"
DEFAULT_TRADE_LOG_PATH = Path("logs/trades/trade_log.csv")


@dataclass(frozen=True)
class AppSettings:
    """
    trading-engine の起動に必要な設定を表すモデル。
    """

    symbol: str = "1306"
    mode: str = "paper"
    strategy: str = "trend"
    log_path: str | None = None
    quantity: int = 100


def load_settings(settings_path: Path | None = None) -> AppSettings:
    """
    settings.yaml を読み込み、起動設定を返す。
    引数:
        settings_path: 読み込む設定ファイルのパス。未指定時は既定パスを使用する

    戻り値:
        AppSettings: 起動設定
    """
    resolved_path = settings_path or DEFAULT_SETTINGS_PATH
    if not resolved_path.exists():
        return AppSettings()

    raw_settings = _load_yaml_like_settings(settings_path=resolved_path)
    return AppSettings(
        symbol=str(raw_settings.get("symbol", AppSettings.symbol)),
        mode=str(raw_settings.get("mode", AppSettings.mode)),
        strategy=str(raw_settings.get("strategy", AppSettings.strategy)),
        log_path=_to_optional_string(raw_settings.get("log_path")),
        quantity=int(raw_settings.get("quantity", AppSettings.quantity)),
    )


def create_system_logger() -> logging.Logger:
    """
    trading-engine 用の system logger を生成する。
    引数:
        なし

    戻り値:
        logging.Logger: system logger
    """
    system_logger = logging.getLogger("trading_engine")
    system_logger.setLevel(logging.INFO)
    system_logger.propagate = False

    if not system_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        system_logger.addHandler(handler)

    return system_logger


def create_executor(mode: str) -> Executor:
    """
    実行モードに応じた executor を生成する。
    引数:
        mode: 売買モード

    戻り値:
        Executor: 実行モードに対応する executor
    """
    if mode == "paper":
        return PaperExecutor()

    if mode == "live":
        raise NotImplementedError("live mode is not implemented")

    raise ValueError(f"unsupported mode: {mode}")


def create_strategy(strategy_name: str) -> Strategy:
    """
    戦略名に応じた strategy を生成する。
    引数:
        strategy_name: 使用する戦略名

    戻り値:
        Strategy: 戦略オブジェクト
    """
    if strategy_name == "trend":
        return TrendStrategy()

    if strategy_name == "range":
        return RangeStrategy()

    raise ValueError(f"unsupported strategy: {strategy_name}")


def validate_settings(settings: AppSettings) -> None:
    """
    起動前に設定値の妥当性を検証する。
    引数:
        settings: 検証対象の起動設定

    戻り値:
        なし
    """
    if settings.quantity <= 0:
        raise ValueError("quantity must be greater than zero")

    create_executor(mode=settings.mode)
    create_strategy(strategy_name=settings.strategy)


def build_engine(
    settings: AppSettings,
    system_logger: logging.Logger | None = None,
    market_time_checker: MarketTimeChecker | None = None,
) -> Engine:
    """
    設定に基づいて Engine を組み立てる。
    引数:
        settings: 起動設定
        system_logger: 使用する system logger
        market_time_checker: 市場時間判定処理

    戻り値:
        Engine: 実行可能な engine
    """
    validate_settings(settings=settings)

    logger_instance = system_logger or create_system_logger()
    trade_log_path = Path(settings.log_path) if settings.log_path else DEFAULT_TRADE_LOG_PATH
    trade_logger = TradeLogger(log_file_path=trade_log_path)

    return Engine(
        config=EngineConfig(
            symbol=settings.symbol,
            strategy_name=settings.strategy,
            quantity=settings.quantity,
        ),
        data_provider=DummyMarketDataProvider(),
        strategy=create_strategy(strategy_name=settings.strategy),
        executor=create_executor(mode=settings.mode),
        market_time_checker=market_time_checker or DefaultMarketTimeChecker(),
        system_logger=logger_instance,
        trade_logger=trade_logger,
    )


def main(
    settings_path: Path | None = None,
    current_datetime: datetime | None = None,
    market_time_checker: MarketTimeChecker | None = None,
) -> int:
    """
    設定読み込みから Engine 実行までの起動フローを実行する。
    引数:
        settings_path: 読み込む設定ファイルのパス
        current_datetime: engine 実行時刻として使用する日時
        market_time_checker: 市場時間判定処理

    戻り値:
        int: 正常終了時は 0、異常終了時は 1
    """
    system_logger = create_system_logger()

    try:
        settings = load_settings(settings_path=settings_path)
        engine = build_engine(
            settings=settings,
            system_logger=system_logger,
            market_time_checker=market_time_checker,
        )
        engine.run_once(current_datetime=current_datetime)
        return 0
    except Exception:
        system_logger.exception("main failed")
        return 1


def _load_yaml_like_settings(settings_path: Path) -> dict[str, Any]:
    """
    設定ファイルを読み込み、辞書形式へ変換する。
    引数:
        settings_path: 読み込む設定ファイルのパス

    戻り値:
        dict[str, Any]: 設定値の辞書
    """
    try:
        import yaml  # type: ignore[import-not-found]

        loaded = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError("settings.yaml must contain a mapping")
        return loaded
    except ModuleNotFoundError:
        return _parse_simple_yaml(settings_path=settings_path)


def _parse_simple_yaml(settings_path: Path) -> dict[str, Any]:
    """
    フラットな key: value 形式の settings.yaml を簡易解析する。
    引数:
        settings_path: 読み込む設定ファイルのパス

    戻り値:
        dict[str, Any]: 設定値の辞書
    """
    parsed_settings: dict[str, Any] = {}

    for raw_line in settings_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" not in line:
            raise ValueError(f"invalid settings line: {raw_line}")

        key, value = line.split(":", 1)
        parsed_settings[key.strip()] = _parse_scalar(value.strip())

    return parsed_settings


def _parse_scalar(value: str) -> Any:
    """
    文字列から簡易的な設定値へ変換する。
    引数:
        value: 設定ファイル上の値

    戻り値:
        Any: 変換後の値
    """
    if value in {"", "null", "None"}:
        return None

    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]

    if value.isdigit():
        return int(value)

    return value


def _to_optional_string(value: Any) -> str | None:
    """
    任意文字列設定を安全に文字列へ変換する。
    引数:
        value: 設定値

    戻り値:
        str | None: 文字列化した値、または None
    """
    if value is None:
        return None

    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())

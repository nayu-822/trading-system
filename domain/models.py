from dataclasses import dataclass, field
from typing import Any

from domain.enums import RunMode


@dataclass(frozen=True)
class AppConfig:
    """システム全体の設定モデル。

    Args:
        mode: 実行モード。
        rest_poll_interval_sec: RESTポーリング間隔。
        push_enabled: Push受信を有効にするか。
        snapshot_enabled: snapshot保存を有効にするか。
        snapshot_dir: snapshot保存先。
        snapshot_interval_sec: snapshot保存間隔。
        snapshot_max_generations: snapshot世代数。
        snapshot_debounce_sec: snapshot抑制秒数。
        recovery_enabled: 復旧を有効にするか。
        recovery_mode: 復旧モード。
        startup_reconcile_enabled: 起動時再同期を有効にするか。

    Returns:
        AppConfig インスタンス。
    """

    mode: RunMode
    rest_poll_interval_sec: int
    push_enabled: bool
    snapshot_enabled: bool
    snapshot_dir: str
    snapshot_interval_sec: int
    snapshot_max_generations: int
    snapshot_debounce_sec: int
    recovery_enabled: bool
    recovery_mode: str
    startup_reconcile_enabled: bool


@dataclass(frozen=True)
class SymbolConfig:
    """銘柄ごとの設定モデル。

    Args:
        code: 銘柄コード。
        name: 銘柄名。
        market: 市場区分。
        strategy: 使用戦略名。
        allocation_ratio: 資金配分比率。
        lot_size: 最小ロット。
        overrides: 銘柄別上書き設定。

    Returns:
        SymbolConfig インスタンス。
    """

    code: str
    name: str
    market: str
    strategy: str
    allocation_ratio: float
    lot_size: int
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategyConfig:
    """戦略設定モデル。

    Args:
        default_strategy: デフォルト戦略名。
        parameters: 戦略パラメータ。

    Returns:
        StrategyConfig インスタンス。
    """

    default_strategy: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class RiskConfig:
    """リスク設定モデル。

    Args:
        max_daily_loss: 日次最大損失。
        max_consecutive_losses: 最大連敗数。
        trading_start_time: 取引開始時刻。
        trading_end_time: 取引終了時刻。
        order_timeout_sec: 注文タイムアウト秒数。

    Returns:
        RiskConfig インスタンス。
    """

    max_daily_loss: float
    max_consecutive_losses: int
    trading_start_time: str
    trading_end_time: str
    order_timeout_sec: int


@dataclass(frozen=True)
class SystemConfig:
    """全設定ファイルを統合した設定モデル。

    Args:
        app: システム設定。
        symbols: 銘柄設定一覧。
        strategy: 戦略設定。
        risk: リスク設定。

    Returns:
        SystemConfig インスタンス。
    """

    app: AppConfig
    symbols: list[SymbolConfig]
    strategy: StrategyConfig
    risk: RiskConfig

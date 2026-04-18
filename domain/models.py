from dataclasses import dataclass

from domain.enums import RunMode


@dataclass(frozen=True)
class AppConfig:
    """システム全体の設定モデル。"""

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
class SymbolOverrideConfig:
    """銘柄別上書き設定モデル。"""

    strategy: str | None = None
    allocation_ratio: float | None = None
    lot_size: int | None = None


@dataclass(frozen=True)
class SymbolConfig:
    """銘柄ごとの設定モデル。"""

    code: str
    name: str
    market: str
    strategy: str
    allocation_ratio: float
    lot_size: int
    overrides: SymbolOverrideConfig


@dataclass(frozen=True)
class TrendStrategyConfig:
    """トレンド戦略の最小設定モデル。"""

    short_window: int
    long_window: int


@dataclass(frozen=True)
class RangeStrategyConfig:
    """レンジ戦略の最小設定モデル。"""

    window: int


@dataclass(frozen=True)
class StrategyConfig:
    """戦略設定モデル。"""

    default_strategy: str
    trend: TrendStrategyConfig
    range: RangeStrategyConfig


@dataclass(frozen=True)
class RiskConfig:
    """リスク設定モデル。"""

    max_daily_loss: float
    max_consecutive_losses: int
    trading_start_time: str
    trading_end_time: str
    order_timeout_sec: int


@dataclass(frozen=True)
class SystemConfig:
    """全設定ファイルを統合した設定モデル。"""

    app: AppConfig
    symbols: list[SymbolConfig]
    strategy: StrategyConfig
    risk: RiskConfig

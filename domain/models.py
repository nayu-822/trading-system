from dataclasses import dataclass, field

from domain.enums import RunMode, StrategyType


@dataclass(frozen=True)
class IndicatorValue:
    """シグナル判定に使った指標値。"""

    name: str
    value: float


@dataclass(frozen=True)
class SignalStrategyConfig:
    """銘柄ごとのシグナル戦略設定。"""

    symbol: str
    strategy_type: StrategyType


@dataclass(frozen=True)
class AppConfig:
    """システム全体の設定モデル。"""

    mode: RunMode
    log_level: str
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

    strategy_params: dict[str, int | float | str | bool | None] = field(
        default_factory=dict
    )
    allocation_ratio: float | None = None
    lot_min: int | None = None
    lot_max: int | None = None
    lot_multiplier: float | None = None


@dataclass(frozen=True)
class SymbolConfig:
    """銘柄ごとの設定モデル。"""

    code: str
    name: str
    enabled: bool
    market: str
    strategy: str
    allocation_ratio: float
    lot_min: int
    lot_max: int
    lot_multiplier: float
    strategy_params_override: SymbolOverrideConfig
    note: str


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
class AutoStrategyConfig:
    """自動戦略選択の最小設定モデル。"""

    enabled: bool


@dataclass(frozen=True)
class StrategyConfig:
    """戦略設定モデル。"""

    default_strategy: str
    trend: TrendStrategyConfig
    range: RangeStrategyConfig
    auto: AutoStrategyConfig


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

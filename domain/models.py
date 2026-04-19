from dataclasses import dataclass, field

from domain.enums import (
    DataSourceMode,
    OrderSide,
    OrderStatus,
    RunMode,
    StrategyType,
    TradingMode,
)


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
class TradingSymbolConfig:
    """銘柄ごとの売買設定。"""

    symbol: str
    lot_size: int


@dataclass
class Order:
    """trading_process 内で管理する注文状態。"""

    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    order_type: str
    status: OrderStatus = OrderStatus.NEW
    price: float | None = None
    filled_quantity: int = 0
    remaining_quantity: int = 0
    avg_price: float | None = None


@dataclass
class Position:
    """銘柄単位の最小建玉数量と平均価格。"""

    symbol: str
    quantity: int = 0
    average_price: float = 0.0


@dataclass
class TradingSymbolState:
    """trading_process が銘柄ごとに保持する状態。"""

    symbol: str
    lot_size: int
    orders: list[Order] = field(default_factory=list)
    position: Position | None = None


@dataclass(frozen=True)
class KabuApiConfig:
    """kabuステーション API 接続設定。"""

    base_url: str
    push_url: str
    timeout_sec: int
    token_env_name: str


@dataclass(frozen=True)
class KabuOrderStatus:
    """REST API から取得した最小注文状態。"""

    order_id: str
    symbol: str | None
    status: OrderStatus
    filled_quantity: int
    remaining_quantity: int
    avg_price: float | None


@dataclass(frozen=True)
class KabuMarketData:
    """Push API から受信した最小市場データ。"""

    symbol: str
    price: float
    bid: float | None
    ask: float | None
    volume: int | None
    timestamp: str | None


@dataclass(frozen=True)
class KabuOrderRequest:
    """kabu API へ送信する最小注文構造。"""

    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    order_type: str
    price: float | None


@dataclass(frozen=True)
class KabuOrderResult:
    """kabu API の注文送信結果を表す構造化モデル。"""

    order_id: str
    symbol: str
    status: OrderStatus
    filled_quantity: int
    remaining_quantity: int
    avg_price: float | None


@dataclass(frozen=True)
class AppConfig:
    """システム全体の設定モデル。"""

    mode: RunMode
    trading_mode: TradingMode
    live_enabled: bool
    data_source_mode: DataSourceMode
    log_level: str
    rest_poll_interval_sec: int
    push_enabled: bool
    kabu_api: KabuApiConfig
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

from dataclasses import dataclass
from datetime import datetime

from domain.enums import SignalType, StrategyType


@dataclass(frozen=True)
class BaseSnapshot:
    """スナップショット共通メタ情報。"""

    version: int
    created_at: datetime
    sequence_no: int


@dataclass(frozen=True)
class SignalSnapshot(BaseSnapshot):
    """signal_process の復元に必要な最小状態。"""

    symbol: str
    last_signal_type: SignalType | None
    strategy_type: StrategyType
    updated_at: datetime


@dataclass(frozen=True)
class TradingSnapshot(BaseSnapshot):
    """trading_process の復元に必要な最小状態。"""

    symbol: str
    position_quantity: int
    avg_price: float
    current_lot: int
    updated_at: datetime

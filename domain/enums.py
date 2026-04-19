from enum import Enum


class RunMode(str, Enum):
    """実行モードを表す列挙型。"""

    LIVE = "live"
    PAPER = "paper"
    MOCK = "mock"
    BACKTEST = "backtest"


class EventType(str, Enum):
    """システム内で扱うイベント種別。"""

    SYSTEM_STARTED = "SystemStarted"
    MARKET_DATA_UPDATED = "MarketDataUpdated"
    SIGNAL_DETECTED = "SignalDetected"
    ORDER_REQUESTED = "OrderRequested"
    ORDER_STATUS_UPDATED = "OrderStatusUpdated"
    POSITION_UPDATED = "PositionUpdated"
    RISK_UPDATED = "RiskUpdated"
    LOT_UPDATED = "LotUpdated"
    SNAPSHOT_REQUESTED = "SnapshotRequested"
    SNAPSHOT_CREATED = "SnapshotCreated"
    ERROR_OCCURRED = "ErrorOccurred"


class EventSource(str, Enum):
    """イベント発生元の責務境界。"""

    MAIN = "main"
    EXTERNAL_DATA = "external_data"
    SIGNAL = "signal"
    TRADING = "trading"
    PERSISTENCE = "persistence"
    SNAPSHOT = "snapshot"


class SignalType(str, Enum):
    """売買意図を表すシグナル種別。"""

    BUY = "BUY"
    SELL = "SELL"
    EXIT = "EXIT"


class OrderSide(str, Enum):
    """注文の売買方向。"""

    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    """注文状態。"""

    NEW = "NEW"
    REQUESTED = "REQUESTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class StrategyType(str, Enum):
    """戦略種別。"""

    TREND = "trend"
    RANGE = "range"
    AUTO = "auto"

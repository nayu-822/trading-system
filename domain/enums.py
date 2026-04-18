from enum import Enum


class RunMode(str, Enum):
    """実行モードを表す列挙型。"""

    LIVE = "live"
    MOCK = "mock"
    BACKTEST = "backtest"


class EventType(str, Enum):
    """システム内で扱うイベント種別。"""

    SYSTEM_STARTED = "SystemStarted"
    MARKET_DATA_UPDATED = "MarketDataUpdated"
    SIGNAL_DETECTED = "SignalDetected"
    ORDER_REQUESTED = "OrderRequested"
    ORDER_UPDATED = "OrderUpdated"
    FILL_UPDATED = "FillUpdated"
    POSITION_UPDATED = "PositionUpdated"
    LOT_UPDATED = "LotUpdated"
    ERROR_OCCURRED = "ErrorOccurred"


class EventSource(str, Enum):
    """イベント発生元の責務境界。"""

    MAIN = "main"
    EXTERNAL_DATA = "external_data"
    SIGNAL = "signal"
    TRADING = "trading"
    PERSISTENCE = "persistence"
    SNAPSHOT = "snapshot"

from typing import Protocol

from domain.events import MarketDataUpdated, SignalPayload


class BaseStrategy(Protocol):
    """シグナル生成戦略の共通インターフェース。"""

    def on_market_data(self, event: MarketDataUpdated) -> SignalPayload | None:
        """市場データからシグナル payload を生成する。"""

    def reset(self, symbol: str) -> None:
        """指定銘柄の戦略状態を初期化する。"""

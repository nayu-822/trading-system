from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import csv
from pathlib import Path


TRADE_LOG_HEADERS: tuple[str, ...] = (
    "timestamp",
    "symbol",
    "side",
    "price",
    "quantity",
    "success",
    "message",
    "strategy",
)


@dataclass(frozen=True)
class TradeLog:
    """
    売買結果を永続化するためのログ情報を表すモデル。
    """

    timestamp: datetime
    symbol: str
    side: str
    price: float
    quantity: float
    success: bool
    message: str | None
    strategy: str | None


@dataclass(frozen=True)
class TradeLogger:
    """
    売買ログを CSV へ追記保存するクラス。
    """

    log_file_path: Path = Path("logs/trades/trade_log.csv")
    encoding: str = "utf-8"

    def log(self, log: TradeLog) -> None:
        """
        売買ログを CSV へ追記保存する。
        引数:
            log: 保存対象の売買ログ

        戻り値:
            なし
        """
        self._validate_log(log=log)
        self.log_file_path.parent.mkdir(parents=True, exist_ok=True)

        write_header = not self.log_file_path.exists()
        with self.log_file_path.open("a", encoding=self.encoding, newline="") as csv_file:
            writer = csv.writer(csv_file)
            if write_header:
                writer.writerow(TRADE_LOG_HEADERS)

            writer.writerow(
                [
                    log.timestamp.isoformat(),
                    log.symbol,
                    log.side,
                    log.price,
                    log.quantity,
                    log.success,
                    log.message,
                    log.strategy,
                ]
            )

    def _validate_log(self, log: TradeLog) -> None:
        """
        売買ログの保存前バリデーションを行う。
        引数:
            log: 検証対象の売買ログ

        戻り値:
            なし
        """
        if not log.symbol:
            raise ValueError("symbol is required")

        if log.side not in {"buy", "sell"}:
            raise ValueError("side must be either 'buy' or 'sell'")

        if log.price <= 0:
            raise ValueError("price must be greater than zero")

        if log.quantity <= 0:
            raise ValueError("quantity must be greater than zero")

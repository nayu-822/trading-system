import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from domain.enums import OrderSide, TradingHaltReason
from domain.models import (
    AccountState,
    RiskConfig,
    RiskControlState,
    RiskSymbolConfig,
    TradeResult,
    TradingSymbolState,
)


@dataclass
class RiskManager:
    """売買可否とロットを一元管理する安全制御。"""

    config: RiskConfig
    symbol_configs: tuple[RiskSymbolConfig, ...]
    state: RiskControlState = field(default_factory=RiskControlState)
    today_provider: Callable[[], date] = date.today
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def __post_init__(self) -> None:
        if self.state.current_equity <= 0:
            self.state.current_equity = self.config.account_equity
        if self.state.max_equity <= 0:
            self.state.max_equity = self.state.current_equity
        self._ensure_business_date()

    def can_enter(
        self,
        symbol: str,
        side: OrderSide,
        current_state: tuple[TradingSymbolState, ...],
    ) -> bool:
        """新規エントリー可否を判定する。"""

        if self.state.kill_switch_active:
            self.logger.warning("entry rejected symbol=%s reason=kill_switch", symbol)
            return False
        if self.state.stopped_by_losses:
            self.logger.warning("entry rejected symbol=%s reason=loss_streak", symbol)
            return False
        if self._drawdown_exceeded():
            self.activate_kill_switch(TradingHaltReason.DRAWDOWN)
            self.logger.warning("entry rejected symbol=%s reason=drawdown", symbol)
            return False
        if self._open_position_count(current_state) >= self.config.max_positions:
            self.logger.warning("entry rejected symbol=%s reason=max_positions", symbol)
            return False
        if (
            abs(self._position_quantity(symbol, current_state))
            >= self.config.max_position_per_symbol
        ):
            self.logger.warning(
                "entry rejected symbol=%s side=%s reason=max_position_per_symbol",
                symbol,
                side.value,
            )
            return False
        return True

    def calculate_lot(self, symbol: str, account_state: AccountState) -> int:
        """資金配分に基づく注文ロットを計算する。"""

        symbol_config = self._symbol_config(symbol)
        if symbol_config is None:
            self.logger.warning(
                "lot calculated symbol=%s lot=0 reason=unknown_symbol", symbol
            )
            return 0
        budget = account_state.available_equity * symbol_config.allocation_ratio
        if budget < symbol_config.lot_min:
            self.logger.warning(
                "lot calculated symbol=%s lot=0 reason=below_min_lot budget=%s",
                symbol,
                budget,
            )
            return 0
        if account_state.reference_price is None or account_state.reference_price <= 0:
            self.logger.warning(
                "lot calculated symbol=%s lot=0 reason=missing_reference_price",
                symbol,
            )
            return 0
        raw_quantity = int(budget / account_state.reference_price)
        lot = min(symbol_config.lot_max, raw_quantity)
        lot -= lot % symbol_config.lot_min
        self.logger.info(
            "lot calculated symbol=%s lot=%s budget=%s reference_price=%s",
            symbol,
            lot,
            budget,
            account_state.reference_price,
        )
        return lot

    def on_trade_result(self, trade_result: TradeResult) -> None:
        """取引結果から連勝・連敗状態を更新する。"""

        self._ensure_business_date()
        if trade_result.realized_pnl < 0:
            self.state.consecutive_losses += 1
            self.state.consecutive_wins = 0
            self.state.daily_realized_loss += abs(trade_result.realized_pnl)
            if self.state.consecutive_losses >= self.config.max_consecutive_losses:
                self.state.stopped_by_losses = True
                self.logger.warning(
                    "entry stopped reason=loss_streak losses=%s",
                    self.state.consecutive_losses,
                )
            if self.state.daily_realized_loss >= self.config.max_daily_loss:
                self.activate_kill_switch(TradingHaltReason.MAX_DAILY_LOSS)
        elif trade_result.realized_pnl > 0:
            self.state.consecutive_wins += 1
            self.state.consecutive_losses = 0
            if (
                self.state.stopped_by_losses
                and self.state.consecutive_wins >= self.config.resume_consecutive_wins
            ):
                self.state.stopped_by_losses = False
                self.logger.info(
                    "entry resumed reason=win_streak wins=%s",
                    self.state.consecutive_wins,
                )
        self.logger.info(
            "trade result updated symbol=%s pnl=%s wins=%s losses=%s stopped=%s",
            trade_result.symbol,
            trade_result.realized_pnl,
            self.state.consecutive_wins,
            self.state.consecutive_losses,
            self.state.stopped_by_losses,
        )

    def on_api_error(self) -> None:
        """API エラー連発時の kill switch を判定する。"""

        self.state.api_error_count += 1
        self.logger.warning("api error counted count=%s", self.state.api_error_count)
        if self.state.api_error_count >= self.config.api_error_limit:
            self.activate_kill_switch(TradingHaltReason.API_ERROR_LIMIT)

    def update_equity(self, current_equity: float) -> None:
        """資産状態と最大ドローダウンを更新する。"""

        self.state.current_equity = current_equity
        self.state.max_equity = max(self.state.max_equity, current_equity)
        self.logger.info(
            "drawdown updated current_equity=%s max_equity=%s drawdown=%s",
            self.state.current_equity,
            self.state.max_equity,
            self._drawdown(),
        )
        if self._drawdown_exceeded():
            self.activate_kill_switch(TradingHaltReason.DRAWDOWN)

    def activate_kill_switch(self, reason: TradingHaltReason) -> None:
        """強制停止を有効化する。"""

        if not self.config.kill_switch_enabled:
            self.logger.warning(
                "kill switch skipped reason=%s disabled=true", reason.value
            )
            return
        self.state.trading_halt_reason = reason
        if not self.state.kill_switch_active:
            self.state.kill_switch_active = True
            self.logger.error("kill switch activated reason=%s", reason.value)

    def restore_state(self, state: RiskControlState) -> None:
        """snapshot 由来のリスク状態を復元する。"""

        self.state = state
        self.__post_init__()

    def _ensure_business_date(self) -> None:
        current_date = self.today_provider().isoformat()
        if self.state.business_date is None:
            self.state.business_date = current_date
            return
        if self.state.business_date != current_date:
            self.logger.info(
                "daily risk state reset previous_date=%s current_date=%s previous_loss=%s",
                self.state.business_date,
                current_date,
                self.state.daily_realized_loss,
            )
            self.state.business_date = current_date
            self.state.daily_realized_loss = 0.0

    def _open_position_count(
        self,
        current_state: tuple[TradingSymbolState, ...],
    ) -> int:
        return sum(
            1
            for state in current_state
            if state.position is not None and state.position.quantity != 0
        )

    def _position_quantity(
        self,
        symbol: str,
        current_state: tuple[TradingSymbolState, ...],
    ) -> int:
        for state in current_state:
            if state.symbol == symbol and state.position is not None:
                return state.position.quantity
        return 0

    def _symbol_config(self, symbol: str) -> RiskSymbolConfig | None:
        for symbol_config in self.symbol_configs:
            if symbol_config.symbol == symbol:
                return symbol_config
        return None

    def _drawdown(self) -> float:
        if self.state.max_equity <= 0:
            return 0.0
        return self.state.max_equity - self.state.current_equity

    def _drawdown_exceeded(self) -> bool:
        return self._drawdown() >= self.config.max_drawdown

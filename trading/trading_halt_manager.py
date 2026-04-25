import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from domain.enums import TradingHaltReason
from domain.models import RiskControlState, TradingHaltState


TimestampProvider = Callable[[], datetime]


def _default_timestamp_provider() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TradingHaltManager:
    """取引停止状態の遷移とログ出力を管理する。"""

    state: RiskControlState
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))
    timestamp_provider: TimestampProvider = _default_timestamp_provider

    def halt(
        self,
        reason: TradingHaltReason,
        message: str,
        requires_manual_resume: bool = True,
    ) -> TradingHaltState:
        """取引停止状態へ遷移する。

        Args:
            reason: 停止理由。
            message: 停止メッセージ。
            requires_manual_resume: 明示解除が必要かどうか。

        Returns:
            TradingHaltState: 更新後の停止状態。
        """

        halted_at = self.state.trading_halt_halted_at or self.timestamp_provider()
        self.state.kill_switch_active = True
        self.state.trading_halt_reason = reason
        self.state.trading_halt_message = message
        self.state.trading_halt_halted_at = halted_at
        self.state.trading_halt_resolved_at = None
        self.state.requires_manual_resume = requires_manual_resume
        halt_state = self.current_state()
        self._log(action="halt", halt_state=halt_state)
        return halt_state

    def resume(self, message: str) -> TradingHaltState:
        """取引停止状態を解除する。

        Args:
            message: 解除メッセージ。

        Returns:
            TradingHaltState: 更新後の停止状態。
        """

        resolved_at = self.timestamp_provider()
        self.state.kill_switch_active = False
        self.state.trading_halt_message = message
        self.state.trading_halt_resolved_at = resolved_at
        self.state.requires_manual_resume = False
        halt_state = self.current_state()
        self._log(action="resume", halt_state=halt_state)
        return halt_state

    def current_state(self) -> TradingHaltState:
        """現在の停止状態を返す。

        Returns:
            TradingHaltState: 現在の停止状態。
        """

        return TradingHaltState(
            is_halted=self.state.kill_switch_active,
            reason=self.state.trading_halt_reason,
            message=self.state.trading_halt_message,
            halted_at=self.state.trading_halt_halted_at,
            resolved_at=self.state.trading_halt_resolved_at,
            requires_manual_resume=self.state.requires_manual_resume,
        )

    def _log(self, action: str, halt_state: TradingHaltState) -> None:
        """停止状態の遷移ログを出力する。

        Args:
            action: 実施した操作。
            halt_state: 出力対象の停止状態。

        Returns:
            なし。
        """

        self.logger.warning(
            "trading halt action=%s reason=%s message=%s halted_at=%s resolved_at=%s requires_manual_resume=%s",
            action,
            halt_state.reason.value if halt_state.reason is not None else "",
            halt_state.message,
            halt_state.halted_at,
            halt_state.resolved_at,
            halt_state.requires_manual_resume,
        )

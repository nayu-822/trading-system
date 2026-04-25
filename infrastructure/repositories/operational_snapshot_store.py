from dataclasses import dataclass, replace
from pathlib import Path

from domain.models import SystemConfig, TradingHaltState
from domain.snapshots import RiskControlSnapshot, TradingStateSnapshot
from infrastructure.clock import RealClock
from infrastructure.file_storage import FileStorage


@dataclass
class OperationalSnapshotStore:
    """運用コマンド用に停止状態だけをスナップショットへ読み書きする。"""

    config: SystemConfig
    storage: FileStorage
    clock: RealClock

    def load_halt_state(self) -> TradingHaltState:
        """停止状態をスナップショットから復元する。
        Args:
            なし
        Returns:
            TradingHaltState: 復元した取引停止状態。スナップショット未作成時は初期状態。
        """

        snapshot = self._load_snapshot()
        if snapshot is None or snapshot.risk_state is None:
            return TradingHaltState()
        risk_state = snapshot.risk_state
        return TradingHaltState(
            is_halted=risk_state.kill_switch_active,
            reason=risk_state.trading_halt_reason,
            message=risk_state.trading_halt_message,
            halted_at=risk_state.trading_halt_halted_at,
            resolved_at=risk_state.trading_halt_resolved_at,
            requires_manual_resume=risk_state.requires_manual_resume,
        )

    def save_halt_state(self, halt_state: TradingHaltState) -> None:
        """停止状態をスナップショットへ保存する。
        Args:
            halt_state: 保存対象の取引停止状態。
        Returns:
            なし
        """

        timestamp = self.clock.now()
        snapshot = self._load_snapshot()
        if snapshot is None:
            snapshot = self._create_empty_snapshot(timestamp)
        risk_state = snapshot.risk_state or self._create_empty_risk_state()
        updated_snapshot = replace(
            snapshot,
            updated_at=timestamp,
            sequence_no=snapshot.sequence_no + 1,
            risk_state=replace(
                risk_state,
                kill_switch_active=halt_state.is_halted,
                trading_halt_reason=halt_state.reason,
                trading_halt_message=halt_state.message,
                trading_halt_halted_at=halt_state.halted_at,
                trading_halt_resolved_at=halt_state.resolved_at,
                requires_manual_resume=halt_state.requires_manual_resume,
            ),
        )
        self.storage.overwrite_json(self.snapshot_path, updated_snapshot.to_dict())

    @property
    def snapshot_path(self) -> Path:
        """停止状態を保存するスナップショットパスを返す。
        Args:
            なし
        Returns:
            Path: 停止状態を含むスナップショットファイルパス。
        """

        return Path(self.config.app.snapshot_dir) / "trading_snapshot.json"

    def _load_snapshot(self) -> TradingStateSnapshot | None:
        """スナップショット全体を読み込む。
        Args:
            なし
        Returns:
            TradingStateSnapshot | None: 読み込んだスナップショット。未作成時は None。
        """

        snapshot_data = self.storage.read_json(self.snapshot_path)
        if snapshot_data is None:
            return None
        return TradingStateSnapshot.from_dict(snapshot_data)

    def _create_empty_snapshot(self, timestamp) -> TradingStateSnapshot:
        """停止状態だけを保存できる空スナップショットを生成する。
        Args:
            timestamp: スナップショット作成時刻。
        Returns:
            TradingStateSnapshot: 初期化済みの空スナップショット。
        """

        return TradingStateSnapshot(
            version=1,
            created_at=timestamp,
            updated_at=timestamp,
            sequence_no=0,
            symbols=(),
            risk_state=self._create_empty_risk_state(),
        )

    def _create_empty_risk_state(self) -> RiskControlSnapshot:
        """停止状態保存用の初期リスク状態を生成する。
        Args:
            なし
        Returns:
            RiskControlSnapshot: 初期化済みのリスク状態。
        """

        return RiskControlSnapshot(
            consecutive_losses=0,
            consecutive_wins=0,
            max_equity=0.0,
            current_equity=0.0,
            kill_switch_active=False,
            stopped_by_losses=False,
            daily_realized_loss=0.0,
            api_error_count=0,
            business_date=None,
        )

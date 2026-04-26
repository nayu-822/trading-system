import logging
import os
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

from data_source.csv_loader import CsvMarketDataLoader
from data_source.kabu_api_client import KabuApiClient, KabuApiError
from data_source.push_client import PushClient
from data_source.rest_poller import RestPoller
from domain.enums import (
    DataSourceMode,
    EventSource,
    EventType,
    KabuApiEnvironment,
    OrderSide,
    OrderStatus,
    StrategyType,
    TradingHaltReason,
    TradingMode,
)
from domain.events import EventFactory, OrderStatusPayload, SystemStartedPayload
from domain.models import (
    Order,
    RiskSymbolConfig,
    SignalStrategyConfig,
    SystemConfig,
    TradingHaltState,
    TradingSymbolConfig,
)
from infrastructure.clock import RealClock
from infrastructure.cli import api_order_precheck as api_order_precheck_cli
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError, validate_config
from infrastructure.event_bus import EventBus
from infrastructure.file_storage import FileStorage
from infrastructure.logger import setup_logger
from infrastructure.repositories.operational_snapshot_store import (
    OperationalSnapshotStore,
)
from infrastructure.repositories.order_status_repository import OrderStatusRepository
from infrastructure.repositories.position_repository import PositionRepository
from processes.external_data_process import ExternalDataProcess
from processes.persistence_process import PersistenceProcess
from processes.signal_process import SignalProcess
from processes.snapshot_process import SnapshotProcess
from processes.trading_process import TradingProcess
from trading.live_order_gateway import LiveOrderGateway
from trading.mock_order_gateway import MockOrderGateway
from trading.order_gateway import OrderGateway
from trading.order_safety_validator import OrderSafetyState, OrderSafetyValidator
from trading.position_reconciliation_service import (
    PositionReconciliationError,
    PositionReconciliationService,
)
from trading.risk_manager import RiskManager

CONFIG_DIR = Path("config")
DEFAULT_CSV_PATH = Path("data/market_data.csv")
DEFAULT_LOG_LEVEL = "INFO"


class LiveOrderNotAllowedError(ValueError):
    """実注文が許可されていない組み合わせであることを示す例外。"""


@dataclass(frozen=True)
class ApiOrderDryRunResources:
    """api-order-dry-run で利用する API 依存リソース。"""

    order_status_repository: OrderStatusRepository
    position_repository: PositionRepository
    position_reconciliation_service: PositionReconciliationService
    order_safety_validator: OrderSafetyValidator
    order_gateway: LiveOrderGateway


@dataclass
class ApplicationRuntime:
    """各プロセスの起動・停止順序を管理する。"""

    config: SystemConfig
    event_bus: EventBus
    logger: logging.Logger
    signal_process: SignalProcess
    trading_process: TradingProcess
    persistence_process: PersistenceProcess
    snapshot_process: SnapshotProcess
    external_data_process: ExternalDataProcess
    clock: RealClock
    position_reconciliation_service: PositionReconciliationService | None = None
    csv_path: Path | None = None
    restored_snapshot: bool = False
    _started: bool = False
    _stop_event: Event = field(default_factory=Event)

    def start(self) -> None:
        if self._started:
            return
        self._stop_event.clear()
        enabled_symbols = tuple(
            symbol.code for symbol in self.config.symbols if symbol.enabled
        )
        self.logger.info(
            "application starting mode=%s data_source_mode=%s trading_mode=%s kabu_api_environment=%s base_url=%s symbols=%s",
            self.config.app.mode.value,
            self.config.app.data_source_mode.value,
            self.config.app.trading_mode.value,
            self.config.app.kabu_api.environment.value,
            self.config.app.kabu_api.base_url,
            ",".join(enabled_symbols),
        )
        _log_kabu_api_environment_warnings(self.logger, self.config)
        if self.config.app.trading_mode == TradingMode.LIVE:
            self.logger.warning(
                "LIVE MODE starting gateway=LiveOrderGateway symbols=%s",
                ",".join(enabled_symbols),
            )
        if self.config.app.recovery_enabled:
            self.restored_snapshot = self.snapshot_process.restore()
        self.logger.info("snapshot restore restored=%s", self.restored_snapshot)
        self._log_halt_state_on_startup()

        try:
            self.persistence_process.start()
            if self.config.app.snapshot_enabled:
                self.snapshot_process.start()
            self.trading_process.start()
            self.sync_orders_once(force_refresh=False)
            self.signal_process.start()
            self._publish_started_event()
            self._started = True
        except Exception:
            self.logger.exception("application startup failed")
            self.stop()
            raise

    def restore_snapshot_state(self) -> bool:
        """スナップショットから状態を復元する。"""

        self.restored_snapshot = self.snapshot_process.restore()
        return self.restored_snapshot

    def run_external_data(self) -> None:
        enabled_symbol = next(
            symbol for symbol in self.config.symbols if symbol.enabled
        )
        if self.config.app.data_source_mode == DataSourceMode.CSV:
            target_csv_path = self.csv_path or DEFAULT_CSV_PATH
            if target_csv_path.exists():
                self.external_data_process.run(
                    data_source_mode=self.config.app.data_source_mode,
                    csv_path=target_csv_path,
                    symbol=enabled_symbol.code,
                )
            else:
                self.logger.warning("csv data skipped path=%s", target_csv_path)
            return

        self.logger.info("api mode external data starting")
        self.external_data_process.run(
            data_source_mode=self.config.app.data_source_mode,
            csv_path=None,
            symbol=enabled_symbol.code,
        )

    def sync_orders_once(self, force_refresh: bool = False) -> int:
        """REST由来の注文状態を一度だけ再同期する。

        Args:
            force_refresh: True の場合はキャッシュを使わず再取得する。
        Returns:
            再同期できた注文状態イベント数。
        """

        if self.config.app.data_source_mode != DataSourceMode.API:
            return 0
        self.logger.info(
            "startup order resync requested restored_snapshot=%s",
            self.restored_snapshot,
        )
        try:
            synced_count = self.external_data_process.sync_orders_once(
                force_refresh=force_refresh
            )
        except Exception:
            self.logger.exception("startup order resync failed")
            if self.config.app.trading_mode == TradingMode.LIVE:
                raise
            return 0
        self.logger.info("startup order resync completed count=%s", synced_count)
        self.reconcile_positions()
        return synced_count

    def reconcile_positions(self, symbols: tuple[str, ...] | None = None) -> int:
        """内部建玉とAPI実建玉を突合する。

        Args:
            symbols: 対象銘柄一覧。省略時は有効銘柄すべてを対象にする。

        Returns:
            int: 突合を実行した銘柄数。
        """

        if (
            self.position_reconciliation_service is None
            or not self.config.app.position_reconciliation_enabled
            or self.config.app.data_source_mode != DataSourceMode.API
        ):
            return 0

        target_symbols = symbols or tuple(
            symbol.code for symbol in self.config.symbols if symbol.enabled
        )
        reconciled_count = 0
        for symbol in target_symbols:
            state = self.trading_process.get_state(symbol)
            try:
                self.position_reconciliation_service.reconcile(
                    symbol=symbol,
                    internal_position=state.position,
                )
            except PositionReconciliationError as error:
                self.logger.exception(
                    "position reconciliation failed symbol=%s",
                    symbol,
                )
                if self.trading_process.risk_manager is not None:
                    self.trading_process.risk_manager.halt_trading(
                        reason=_resolve_position_reconciliation_reason(error),
                        message=str(error),
                    )
                continue
            reconciled_count += 1
        return reconciled_count

    def resume_trading(self) -> bool:
        """停止状態の再確認と明示解除を行う。

        Returns:
            bool: 解除に成功した場合は True。
        """

        risk_manager = self.trading_process.risk_manager
        if risk_manager is None:
            self.logger.warning("trading halt action=resume_failed reason=unknown message=risk_manager is empty")
            return False
        if not risk_manager.state.kill_switch_active:
            risk_manager.resume_trading("resume requested while not halted")
            return True
        if (
            self.config.app.data_source_mode != DataSourceMode.API
            or getattr(self.external_data_process, "rest_poller", None) is None
        ):
            self.logger.warning(
                "trading halt action=resume_failed reason=%s message=%s halted_at=%s resolved_at=%s requires_manual_resume=%s",
                risk_manager.state.trading_halt_reason.value
                if risk_manager.state.trading_halt_reason is not None
                else TradingHaltReason.UNKNOWN_ERROR.value,
                "api resume checks are unavailable",
                risk_manager.state.trading_halt_halted_at,
                risk_manager.state.trading_halt_resolved_at,
                risk_manager.state.requires_manual_resume,
            )
            return False
        started_temporarily = False
        if not self.trading_process._subscribed:
            self.trading_process.start()
            started_temporarily = True
        try:
            self.external_data_process.sync_orders_once(force_refresh=True)
            self.reconcile_positions()
            if not self._open_orders_match_api():
                risk_manager.halt_trading(
                    reason=TradingHaltReason.ORDER_SYNC_FAILED,
                    message="open orders are not synchronized with api",
                )
                self._log_resume_failed()
                return False
            enabled_symbols = tuple(
                symbol.code for symbol in self.config.symbols if symbol.enabled
            )
            if self.reconcile_positions(symbols=enabled_symbols) != len(enabled_symbols):
                self._log_resume_failed()
                return False
            risk_manager.resume_trading("manual resume completed")
            return True
        except Exception as error:
            risk_manager.halt_trading(
                reason=TradingHaltReason.ORDER_SYNC_FAILED,
                message=f"order sync failed during resume: {error}",
            )
            self._log_resume_failed()
            return False
        finally:
            if started_temporarily:
                self.trading_process.stop()

    def wait(self, stop_event: Event | None = None) -> None:
        """API モードでは停止要求まで待機し、CSV モードでは即時に戻る。"""

        if self.config.app.data_source_mode == DataSourceMode.CSV:
            return
        wait_event = stop_event or self._stop_event
        self.logger.info("api mode runtime waiting")
        wait_event.wait()

    def flush(self) -> None:
        self.persistence_process.flush()
        if self.config.app.snapshot_enabled:
            self.snapshot_process.flush()

    def stop(self) -> None:
        self._stop_event.set()
        self.external_data_process.stop()
        self.trading_process.stop()
        self.signal_process.stop()
        self.flush()
        self.snapshot_process.stop()
        self.persistence_process.stop()
        self._started = False

    def _publish_started_event(self) -> None:
        event_factory = EventFactory(source=EventSource.MAIN)
        started_event = event_factory.create(
            event_type=EventType.SYSTEM_STARTED,
            timestamp=self.clock.now(),
            symbol=None,
            payload=SystemStartedPayload(mode=self.config.app.mode.value),
        )
        self.event_bus.publish(started_event)

    def _open_orders_match_api(self) -> bool:
        """未完了注文が API と内部状態で一致しているか確認する。"""

        rest_poller = getattr(self.external_data_process, "rest_poller", None)
        if rest_poller is None:
            return False
        return self._open_orders_match_repository(
            order_status_repository=rest_poller.order_status_repository,
            symbols=tuple(symbol.code for symbol in self.config.symbols if symbol.enabled),
        )

    def _open_orders_match_repository(
        self,
        order_status_repository: OrderStatusRepository,
        symbols: tuple[str, ...],
    ) -> bool:
        for symbol in symbols:
            try:
                api_orders = {
                    self._order_identity(order)
                    for order in order_status_repository.get_open_orders(
                        symbol=symbol,
                        force_refresh=True,
                    )
                }
            except Exception as error:
                self.logger.exception(
                    "open order reconciliation failed symbol=%s error=%s",
                    symbol,
                    error,
                )
                return False
            internal_orders = {
                self._order_identity(order)
                for order in self.trading_process.get_state(symbol).orders
                if order.status
                in {
                    OrderStatus.NEW,
                    OrderStatus.REQUESTED,
                    OrderStatus.PARTIALLY_FILLED,
                }
            }
            if api_orders != internal_orders:
                return False
        return True

    def _order_identity(self, order: Order) -> tuple[str, str, str, int, str, int, int, bool]:
        """未完了注文比較用の識別子を返す。"""

        return (
            order.external_order_id or order.order_id,
            order.symbol,
            order.side.value,
            order.quantity,
            order.status.value,
            order.filled_quantity,
            order.remaining_quantity,
            order.is_exit,
        )

    def _log_halt_state_on_startup(self) -> None:
        """起動時に停止状態をログ出力する。"""

        risk_manager = self.trading_process.risk_manager
        if risk_manager is None or not risk_manager.state.kill_switch_active:
            return
        self.logger.warning(
            "trading halt action=halt reason=%s message=%s halted_at=%s resolved_at=%s requires_manual_resume=%s",
            risk_manager.state.trading_halt_reason.value
            if risk_manager.state.trading_halt_reason is not None
            else "",
            risk_manager.state.trading_halt_message,
            risk_manager.state.trading_halt_halted_at,
            risk_manager.state.trading_halt_resolved_at,
            risk_manager.state.requires_manual_resume,
        )

    def _log_resume_failed(self) -> None:
        """解除失敗ログを出力する。"""

        risk_manager = self.trading_process.risk_manager
        if risk_manager is None:
            return
        self.logger.warning(
            "trading halt action=resume_failed reason=%s message=%s halted_at=%s resolved_at=%s requires_manual_resume=%s",
            risk_manager.state.trading_halt_reason.value
            if risk_manager.state.trading_halt_reason is not None
            else "",
            risk_manager.state.trading_halt_message,
            risk_manager.state.trading_halt_halted_at,
            risk_manager.state.trading_halt_resolved_at,
            risk_manager.state.requires_manual_resume,
        )

    def get_trading_halt_state(self) -> TradingHaltState:
        """現在の取引停止状態を返す。"""

        risk_manager = self.trading_process.risk_manager
        if risk_manager is None:
            return TradingHaltState()
        return TradingHaltState(
            is_halted=risk_manager.state.kill_switch_active,
            reason=risk_manager.state.trading_halt_reason,
            message=risk_manager.state.trading_halt_message,
            halted_at=risk_manager.state.trading_halt_halted_at,
            resolved_at=risk_manager.state.trading_halt_resolved_at,
            requires_manual_resume=risk_manager.state.requires_manual_resume,
        )

    def halt_trading_manually(
        self,
        reason: TradingHaltReason,
        message: str,
    ) -> TradingHaltState:
        """手動で取引停止状態にする。"""

        risk_manager = self.trading_process.risk_manager
        if risk_manager is None:
            return TradingHaltState()
        risk_manager.halt_trading(
            reason=reason,
            message=message,
            requires_manual_resume=True,
        )
        return self.get_trading_halt_state()

    def save_snapshot_now(self) -> None:
        """現在状態を即時にスナップショット保存する。"""

        self.snapshot_process.start()
        self.snapshot_process.save_snapshot(self.clock.now())
        self.snapshot_process.flush()
        self.snapshot_process.stop()

    def run_preflight_check(self) -> dict[str, Any]:
        """取引再開前の確認だけを実行する。"""

        checks: list[dict[str, Any]] = []
        errors: list[str] = []
        rest_poller = getattr(self.external_data_process, "rest_poller", None)
        order_sync_ok = False
        if self.config.app.data_source_mode != DataSourceMode.API or rest_poller is None:
            message = "api order sync is unavailable"
            checks.append({"name": "order_status_sync", "ok": False, "message": message})
            errors.append(message)
        else:
            try:
                rest_poller.order_status_repository.list_orders(force_refresh=True)
                order_sync_ok = True
                checks.append(
                    {"name": "order_status_sync", "ok": True, "message": "ok"}
                )
            except Exception as error:
                message = f"order status sync failed: {error}"
                checks.append(
                    {"name": "order_status_sync", "ok": False, "message": message}
                )
                errors.append(message)

        open_orders_ok = order_sync_ok and self._open_orders_match_api()
        checks.append(
            {
                "name": "open_orders_match",
                "ok": open_orders_ok,
                "message": "ok" if open_orders_ok else "open orders are not synchronized with api",
            }
        )
        if not open_orders_ok:
            errors.append("open orders are not synchronized with api")

        positions_ok = True
        if (
            self.position_reconciliation_service is None
            or self.config.app.data_source_mode != DataSourceMode.API
        ):
            positions_ok = False
            checks.append(
                {
                    "name": "position_reconciliation",
                    "ok": False,
                    "message": "position reconciliation is unavailable",
                }
            )
            errors.append("position reconciliation is unavailable")
        else:
            for symbol in (symbol.code for symbol in self.config.symbols if symbol.enabled):
                try:
                    self.position_reconciliation_service.reconcile(
                        symbol=symbol,
                        internal_position=self.trading_process.get_state(symbol).position,
                    )
                except Exception as error:
                    positions_ok = False
                    message = f"position reconciliation failed: {error}"
                    checks.append(
                        {
                            "name": "position_reconciliation",
                            "ok": False,
                            "message": message,
                        }
                    )
                    errors.append(message)
                    break
            else:
                checks.append(
                    {
                        "name": "position_reconciliation",
                        "ok": True,
                        "message": "ok",
                    }
                )

        try:
            _ensure_live_order_allowed(self.config)
            order_safety_ok = True
            order_safety_message = "ok"
        except Exception as error:
            order_safety_ok = False
            order_safety_message = str(error)
            errors.append(order_safety_message)
        checks.append(
            {
                "name": "order_safety",
                "ok": order_safety_ok,
                "message": order_safety_message,
            }
        )
        checks.append(
            {
                "name": "trading_configuration",
                "ok": order_safety_ok,
                "message": f"trading_mode={self.config.app.trading_mode.value} kabu_api_environment={self.config.app.kabu_api.environment.value}",
            }
        )

        halt_state = self.get_trading_halt_state()
        return {
            "ok": all(check["ok"] for check in checks),
            "is_halted": halt_state.is_halted,
            "reason": halt_state.reason.value if halt_state.reason is not None else "",
            "message": halt_state.message,
            "checks": checks,
            "errors": errors,
        }

    def run_api_order_precheck(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
    ) -> dict[str, Any]:
        """api-order-dry-run 前の読み取り専用チェックを実行する。

        Args:
            symbol: 確認対象銘柄コード。
            side: 想定売買方向。
            quantity: 想定数量。

        Returns:
            dict[str, Any]: CLI 出力向けの事前確認結果。
        """

        halt_state = self.get_trading_halt_state()
        payload = _api_order_precheck_payload(
            config=self.config,
            symbol=symbol,
            side=side.value,
            quantity=quantity,
            halt_state=halt_state,
        )
        is_paper_environment = (
            self.config.app.kabu_api.environment == KabuApiEnvironment.PAPER
        )
        self._append_check(
            payload=payload,
            name="environment_is_paper",
            ok=is_paper_environment,
            message="ok" if is_paper_environment else "kabu_api_environment must be paper",
        )
        token_env_name_is_paper = (
            self.config.app.kabu_api.token_env_name == "KABU_API_PASSWORD_PAPER"
        )
        self._append_check(
            payload=payload,
            name="token_env_name_is_paper",
            ok=token_env_name_is_paper,
            message=(
                "ok"
                if token_env_name_is_paper
                else "paper検証APIでは token_env_name に KABU_API_PASSWORD_PAPER を指定してください"
            ),
        )
        token_env_exists = bool(
            os.environ.get(self.config.app.kabu_api.token_env_name, "")
        )
        self._append_check(
            payload=payload,
            name="token_env_exists",
            ok=token_env_exists,
            message=(
                "ok"
                if token_env_exists
                else f"環境変数 {self.config.app.kabu_api.token_env_name} が設定されていません"
            ),
        )
        trading_mode_is_live = self.config.app.trading_mode == TradingMode.LIVE
        self._append_check(
            payload=payload,
            name="trading_mode_is_live",
            ok=trading_mode_is_live,
            message="ok" if trading_mode_is_live else "trading_mode must be live",
        )
        live_enabled_is_true = self.config.app.live_enabled
        self._append_check(
            payload=payload,
            name="live_enabled_is_true",
            ok=live_enabled_is_true,
            message="ok" if live_enabled_is_true else "live_enabled must be true",
        )
        data_source_mode_is_api = self.config.app.data_source_mode == DataSourceMode.API
        self._append_check(
            payload=payload,
            name="data_source_mode_is_api",
            ok=data_source_mode_is_api,
            message="ok" if data_source_mode_is_api else "data_source_mode must be api",
        )
        is_paper_port = _is_paper_api_base_url(self.config.app.kabu_api.base_url)
        self._append_check(
            payload=payload,
            name="base_url_is_paper_port",
            ok=is_paper_port,
            message="ok" if is_paper_port else "base_url must point to paper port 18081",
        )
        symbol_allowed = symbol in self.config.app.trade_symbols
        self._append_check(
            payload=payload,
            name="symbol_allowed",
            ok=symbol_allowed,
            message="ok" if symbol_allowed else "symbol is not in trade_symbols",
        )
        quantity_valid = 1 <= quantity <= self.config.app.max_order_quantity
        self._append_check(
            payload=payload,
            name="quantity_valid",
            ok=quantity_valid,
            message=(
                "ok"
                if quantity_valid
                else "quantity must be between 1 and max_order_quantity"
            ),
        )
        lot_unit = self._resolve_api_order_precheck_lot_unit(symbol)
        quantity_matches_lot_unit = lot_unit is None or (
            quantity > 0 and quantity % lot_unit == 0
        )
        self._append_check(
            payload=payload,
            name="quantity_matches_lot_unit",
            ok=quantity_matches_lot_unit,
            message=(
                "ok"
                if quantity_matches_lot_unit
                else f"{symbol} の売買単位は {lot_unit} です"
            ),
        )
        safe_quantity = self._resolve_api_order_precheck_safe_quantity(symbol)
        quantity_is_safe_for_symbol = (
            safe_quantity is None or (quantity > 0 and quantity <= safe_quantity)
        )
        self._append_check(
            payload=payload,
            name="quantity_is_safe_for_symbol",
            ok=quantity_is_safe_for_symbol,
            message=(
                "ok"
                if quantity_is_safe_for_symbol
                else f"{symbol} の検証数量は {safe_quantity} を推奨しています"
            ),
        )
        not_halted = not halt_state.is_halted
        self._append_check(
            payload=payload,
            name="not_halted",
            ok=not_halted,
            message="ok" if not_halted else "trading is halted",
        )
        if not all(check["ok"] for check in payload["checks"]):
            return _finalize_api_order_precheck_payload(payload)

        try:
            resources = _build_api_order_dry_run_resources(
                config=self.config,
                logger=self.logger,
                trading_process=self.trading_process,
            )
        except Exception as error:
            self._append_check(
                payload=payload,
                name="api_connectivity",
                ok=False,
                message=f"api connectivity failed: {error}",
            )
            return _finalize_api_order_precheck_payload(payload)
        self._append_check(
            payload=payload,
            name="api_connectivity",
            ok=True,
            message="ok",
        )

        try:
            resources.position_repository.get_position(
                symbol=symbol,
                force_refresh=True,
            )
        except Exception as error:
            self._append_check(
                payload=payload,
                name="position_fetch",
                ok=False,
                message=f"position fetch failed: {error}",
            )
            return _finalize_api_order_precheck_payload(payload)
        self._append_check(
            payload=payload,
            name="position_fetch",
            ok=True,
            message="ok",
        )

        try:
            resources.order_status_repository.list_orders(
                symbol=symbol,
                force_refresh=True,
            )
        except Exception as error:
            self._append_check(
                payload=payload,
                name="order_status_fetch",
                ok=False,
                message=f"order status fetch failed: {error}",
            )
            return _finalize_api_order_precheck_payload(payload)
        self._append_check(
            payload=payload,
            name="order_status_fetch",
            ok=True,
            message="ok",
        )

        preflight_ok, preflight_message = self._run_api_order_precheck_validation(
            symbol=symbol,
            resources=resources,
        )
        self._append_check(
            payload=payload,
            name="preflight_check",
            ok=preflight_ok,
            message=preflight_message,
        )
        payload["ok"] = all(check["ok"] for check in payload["checks"])
        if payload["ok"]:
            payload["message"] = "ok"
            payload["errors"] = []
        return _finalize_api_order_precheck_payload(payload)

    def _run_api_order_precheck_validation(
        self,
        symbol: str,
        resources: ApiOrderDryRunResources,
    ) -> tuple[bool, str]:
        """api-order-precheck 用の読み取り専用妥当性確認を実行する。

        Args:
            symbol: 確認対象銘柄コード。
            resources: API 接続用リソース。

        Returns:
            tuple[bool, str]: 成否と結果メッセージ。
        """

        try:
            if not self._open_orders_match_repository(
                order_status_repository=resources.order_status_repository,
                symbols=(symbol,),
            ):
                return False, "open orders are not synchronized with api"
            resources.position_reconciliation_service.reconcile(
                symbol=symbol,
                internal_position=self.trading_process.get_state(symbol).position,
            )
        except Exception as error:
            return False, f"preflight check failed: {error}"
        return True, "ok"

    def _resolve_api_order_precheck_lot_unit(self, symbol: str) -> int | None:
        """api-order-precheck 用の売買単位を返す。

        Args:
            symbol: 確認対象銘柄コード。

        Returns:
            int | None: 判定に使う売買単位。未設定時は None。
        """

        symbol_config = next(
            (
                item
                for item in self.config.symbols
                if item.code == symbol and item.enabled and item.lot_min > 0
            ),
            None,
        )
        return symbol_config.lot_min if symbol_config is not None else None

    def _resolve_api_order_precheck_safe_quantity(self, symbol: str) -> int | None:
        """api-order-precheck 用の推奨検証数量を返す。

        Args:
            symbol: 確認対象銘柄コード。

        Returns:
            int | None: 推奨検証数量。未設定時は None。
        """

        return self._resolve_api_order_precheck_lot_unit(symbol)

    def run_api_order_dry_run(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        price: float | None = None,
    ) -> dict[str, Any]:
        """検証 API 向けの単発注文フロー確認を実行する。

        Args:
            symbol: 対象銘柄コード。
            side: 注文売買区分。
            quantity: 注文数量。
            price: 注文価格。未指定時は成行扱い。

        Returns:
            dict[str, Any]: CLI 出力向けの実行結果。
        """

        payload = self._api_order_dry_run_payload(
            symbol=symbol,
            side=side,
            quantity=quantity,
        )
        halt_state = self.get_trading_halt_state()
        payload["is_halted"] = halt_state.is_halted
        payload["reason"] = (
            halt_state.reason.value if halt_state.reason is not None else ""
        )
        payload["message"] = halt_state.message

        if self.config.app.kabu_api.environment != KabuApiEnvironment.PAPER:
            self._append_check(
                payload=payload,
                name="kabu_api_environment",
                ok=False,
                message="kabu_api_environment must be paper",
            )
            self._finalize_api_order_dry_run_log(payload)
            return payload
        self._append_check(
            payload=payload,
            name="kabu_api_environment",
            ok=True,
            message="ok",
        )
        if halt_state.is_halted:
            self._append_check(
                payload=payload,
                name="halt_status",
                ok=False,
                message="trading is halted",
            )
            self._finalize_api_order_dry_run_log(payload)
            return payload
        self._append_check(
            payload=payload,
            name="halt_status",
            ok=True,
            message="ok",
        )
        if symbol not in self.config.app.trade_symbols:
            self._append_check(
                payload=payload,
                name="symbol",
                ok=False,
                message="symbol is not in trade_symbols",
            )
            self._finalize_api_order_dry_run_log(payload)
            return payload
        self._append_check(
            payload=payload,
            name="symbol",
            ok=True,
            message="ok",
        )
        if quantity < 1:
            self._append_check(
                payload=payload,
                name="quantity",
                ok=False,
                message="quantity must be >= 1",
            )
            self._finalize_api_order_dry_run_log(payload)
            return payload
        if quantity > self.config.app.max_order_quantity:
            self._append_check(
                payload=payload,
                name="quantity",
                ok=False,
                message="quantity exceeds max_order_quantity",
            )
            self._finalize_api_order_dry_run_log(payload)
            return payload
        self._append_check(
            payload=payload,
            name="quantity",
            ok=True,
            message="ok",
        )

        try:
            resources = _build_api_order_dry_run_resources(
                config=self.config,
                logger=self.logger,
                trading_process=self.trading_process,
            )
        except Exception as error:
            self._append_check(
                payload=payload,
                name="api_resources",
                ok=False,
                message=f"api resource initialization failed: {error}",
            )
            self._finalize_api_order_dry_run_log(payload)
            return payload

        if not self._run_api_order_dry_run_preflight(
            symbol=symbol,
            payload=payload,
            resources=resources,
        ):
            self._finalize_api_order_dry_run_log(payload)
            return payload

        order = Order(
            order_id=str(uuid4()),
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=self.trading_process.order_type,
            is_exit=False,
            status=OrderStatus.REQUESTED,
            price=price,
            remaining_quantity=quantity,
        )
        decision = resources.order_safety_validator.evaluate_order(order)
        self._append_check(
            payload=payload,
            name="order_safety",
            ok=decision.allowed,
            message=decision.reason or "ok",
        )
        if not decision.allowed:
            self._finalize_api_order_dry_run_log(payload)
            return payload

        state = self.trading_process.get_state(symbol)
        started_temporarily = False
        if not self.trading_process._subscribed:
            self.trading_process.start()
            started_temporarily = True
        try:
            status_events = resources.order_gateway.place_order(
                order=order,
                timestamp=self.clock.now(),
            )
            for status_event in status_events:
                self.event_bus.publish(status_event)

            payload["order_id"] = order.external_order_id or order.order_id
            synced_order = resources.order_status_repository.get_order_status(
                order_id=payload["order_id"],
                force_refresh=True,
            )
            if synced_order is None:
                self._append_check(
                    payload=payload,
                    name="order_status_sync",
                    ok=False,
                    message="order status is not found after order",
                )
                return payload
            self._append_check(
                payload=payload,
                name="order_status_sync",
                ok=True,
                message="ok",
            )
            self.event_bus.publish(
                self._build_order_status_event_from_repository(
                    order=order,
                    synced_order=synced_order,
                )
            )
            payload["order_id"] = synced_order.external_order_id or synced_order.order_id
            payload["order_status"] = synced_order.status.value
            payload["filled_quantity"] = synced_order.filled_quantity
            payload["remaining_quantity"] = synced_order.remaining_quantity

            try:
                resources.position_reconciliation_service.reconcile(
                    symbol=symbol,
                    internal_position=self.trading_process.get_state(symbol).position,
                )
            except Exception as error:
                self._append_check(
                    payload=payload,
                    name="position_reconciliation",
                    ok=False,
                    message=f"position reconciliation failed: {error}",
                )
                return payload
            self._append_check(
                payload=payload,
                name="position_reconciliation",
                ok=True,
                message="ok",
            )
            api_position = resources.position_repository.get_position(
                symbol=symbol,
                force_refresh=True,
            )
            internal_position = self.trading_process.get_state(symbol).position
            payload["position"] = {
                "symbol": symbol,
                "quantity": api_position.quantity,
                "average_price": api_position.average_price,
            }
            payload["position_quantity"] = (
                internal_position.quantity if internal_position is not None else 0
            )
            payload["ok"] = True
            payload["message"] = "ok"
            payload["errors"] = []
            return payload
        except Exception as error:
            payload["message"] = str(error)
            payload["errors"] = [str(error)]
            return payload
        finally:
            self._finalize_api_order_dry_run_log(payload)
            if started_temporarily:
                self.trading_process.stop()

    def _run_api_order_dry_run_preflight(
        self,
        symbol: str,
        payload: dict[str, Any],
        resources: ApiOrderDryRunResources,
    ) -> bool:
        """api-order-dry-run の事前確認を実行する。"""

        try:
            resources.order_status_repository.list_orders(force_refresh=True)
        except Exception as error:
            self._append_check(
                payload=payload,
                name="preflight_order_status_sync",
                ok=False,
                message=f"order status sync failed: {error}",
            )
            return False
        self._append_check(
            payload=payload,
            name="preflight_order_status_sync",
            ok=True,
            message="ok",
        )
        if not self._open_orders_match_repository(
            order_status_repository=resources.order_status_repository,
            symbols=(symbol,),
        ):
            self._append_check(
                payload=payload,
                name="preflight_open_orders_match",
                ok=False,
                message="open orders are not synchronized with api",
            )
            return False
        self._append_check(
            payload=payload,
            name="preflight_open_orders_match",
            ok=True,
            message="ok",
        )
        try:
            resources.position_reconciliation_service.reconcile(
                symbol=symbol,
                internal_position=self.trading_process.get_state(symbol).position,
            )
        except Exception as error:
            self._append_check(
                payload=payload,
                name="preflight_position_reconciliation",
                ok=False,
                message=f"position reconciliation failed: {error}",
            )
            return False
        self._append_check(
            payload=payload,
            name="preflight_position_reconciliation",
            ok=True,
            message="ok",
        )
        return True

    def _build_order_status_event_from_repository(
        self,
        order: Order,
        synced_order: Order,
    ):
        """注文状態リポジトリの結果をイベントへ変換する。"""

        return self.trading_process.event_factory.create(
            event_type=EventType.ORDER_STATUS_UPDATED,
            timestamp=self.clock.now(),
            symbol=synced_order.symbol,
            payload=OrderStatusPayload(
                order_id=order.order_id,
                status=synced_order.status,
                filled_quantity=synced_order.filled_quantity,
                remaining_quantity=synced_order.remaining_quantity,
                avg_price=synced_order.avg_price,
                side=synced_order.side,
                order_quantity=synced_order.quantity,
                is_exit=synced_order.is_exit,
                external_order_id=synced_order.external_order_id,
            ),
        )

    def _api_order_dry_run_payload(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
    ) -> dict[str, Any]:
        """api-order-dry-run の初期レスポンスを生成する。"""

        return {
            "ok": False,
            "command": "api-order-dry-run",
            "symbol": symbol,
            "side": side.value,
            "quantity": quantity,
            "order_id": "",
            "order_status": "",
            "filled_quantity": 0,
            "remaining_quantity": quantity,
            "position_quantity": 0,
            "position_side": "NONE",
            "reconciliation_result": "UNKNOWN",
            "position": {
                "symbol": symbol,
                "quantity": 0,
                "average_price": 0.0,
            },
            "trading_mode": self.config.app.trading_mode.value,
            "kabu_api_environment": self.config.app.kabu_api.environment.value,
            "base_url": self.config.app.kabu_api.base_url,
            "is_halted": False,
            "reason": "",
            "halt_reason": "",
            "message": "",
            "next_action": [],
            "log_hint": [],
            "checks": [],
            "errors": [],
        }

    def _append_check(
        self,
        payload: dict[str, Any],
        name: str,
        ok: bool,
        message: str,
    ) -> None:
        """チェック結果を payload に追加する。"""

        payload["checks"].append({"name": name, "ok": ok, "message": message})
        if not ok:
            payload["message"] = message
            payload["errors"].append(message)

    def _finalize_api_order_dry_run_log(self, payload: dict[str, Any]) -> None:
        """api-order-dry-run の結果をログ出力する。"""

        _finalize_api_order_dry_run_payload(payload)
        self.logger.info(
            "operational command command=api-order-dry-run symbol=%s side=%s quantity=%s order_id=%s order_status=%s result=%s error_reason=%s",
            payload["symbol"],
            payload["side"],
            payload["quantity"],
            payload["order_id"],
            payload["order_status"],
            "ok" if payload["ok"] else "ng",
            payload["errors"][0] if payload["errors"] else "",
        )

def initialize_application(
    config_dir: Path = CONFIG_DIR,
    csv_path: Path | None = None,
    block_api: bool = False,
) -> tuple[EventBus, logging.Logger]:
    """アプリケーションの最小基盤を初期化する。

    Args:
        config_dir: 設定ファイルを配置したディレクトリ。

    Returns:
        event_bus と logger。
    """

    config = load_config(config_dir)
    validate_config(config)

    runtime = build_application_runtime(
        config=config,
        csv_path=csv_path,
    )
    try:
        runtime.start()
        runtime.run_external_data()
        if block_api:
            runtime.wait()
        runtime.flush()
    except (Exception, KeyboardInterrupt):
        runtime.stop()
        raise
    runtime.logger.info(
        "application initialized mode=%s data_source_mode=%s trading_mode=%s kabu_api_environment=%s base_url=%s",
        config.app.mode.value,
        config.app.data_source_mode.value,
        config.app.trading_mode.value,
        config.app.kabu_api.environment.value,
        config.app.kabu_api.base_url,
    )
    return runtime.event_bus, runtime.logger


def build_application_runtime(
    config: SystemConfig,
    csv_path: Path | None = None,
    allow_real_order_disabled: bool = False,
) -> ApplicationRuntime:
    """設定済みのプロセス群を構築する。"""

    logger = setup_logger(config.app.log_level, process_name=EventSource.MAIN.value)
    event_bus = EventBus()
    clock = RealClock()
    storage = FileStorage()
    snapshot_dir = Path(config.app.snapshot_dir)
    signal_process = SignalProcess(
        event_bus=event_bus,
        strategy_configs=tuple(
            SignalStrategyConfig(
                symbol=symbol.code,
                strategy_type=StrategyType(symbol.strategy),
                range_window=_resolve_symbol_int_parameter(
                    symbol=symbol,
                    parameter_name="range_window",
                ),
                trend_short_window=_resolve_symbol_int_parameter(
                    symbol=symbol,
                    parameter_name="trend_short_window",
                ),
                trend_long_window=_resolve_symbol_int_parameter(
                    symbol=symbol,
                    parameter_name="trend_long_window",
                ),
            )
            for symbol in config.symbols
            if symbol.enabled
        ),
        range_window=config.strategy.range.window,
        trend_short_window=config.strategy.trend.short_window,
        trend_long_window=config.strategy.trend.long_window,
    )
    trading_process = TradingProcess(
        event_bus=event_bus,
        lot_configs=tuple(
            TradingSymbolConfig(
                symbol=symbol.code,
                lot_size=symbol.lot_min,
            )
            for symbol in config.symbols
            if symbol.enabled
        ),
        risk_manager=_build_risk_manager(config=config),
    )
    external_data_process = _build_external_data_process(
        event_bus=event_bus,
        config=config,
        logger=logger,
    )
    position_reconciliation_service = _build_position_reconciliation_service(
        config=config,
        logger=logger,
    )
    if (
        config.app.data_source_mode == DataSourceMode.API
        and config.app.trading_mode == TradingMode.LIVE
    ):
        trading_process.order_status_syncer = (
            lambda: external_data_process.sync_orders_once(force_refresh=True)
        )
    trading_process.order_gateway = _build_order_gateway(
        config=config,
        logger=logger,
        trading_process=trading_process,
        allow_real_order_disabled=allow_real_order_disabled,
    )
    persistence_process = PersistenceProcess(
        event_bus=event_bus,
        storage=storage,
        event_log_path=snapshot_dir / "events.jsonl",
    )
    snapshot_process = SnapshotProcess(
        event_bus=event_bus,
        trading_process=trading_process,
        storage=storage,
        snapshot_path=snapshot_dir / "trading_snapshot.json",
        event_threshold=1,
        interval_sec=config.app.snapshot_interval_sec,
    )
    runtime = ApplicationRuntime(
        config=config,
        event_bus=event_bus,
        logger=logger,
        signal_process=signal_process,
        trading_process=trading_process,
        persistence_process=persistence_process,
        snapshot_process=snapshot_process,
        external_data_process=external_data_process,
        clock=clock,
        position_reconciliation_service=position_reconciliation_service,
        csv_path=csv_path,
    )
    trading_process.position_reconciliation_handler = (
        lambda symbol: runtime.reconcile_positions(symbols=(symbol,))
    )
    rest_poller = getattr(external_data_process, "rest_poller", None)
    if rest_poller is not None:
        rest_poller.on_cycle_completed = (
            lambda: runtime.reconcile_positions()
        )
    return runtime


def _build_external_data_process(
    event_bus: EventBus,
    config: SystemConfig,
    logger: logging.Logger,
) -> ExternalDataProcess:
    api_client = KabuApiClient(config=config.app.kabu_api)
    rest_poller = None
    push_client = None
    if config.app.data_source_mode == DataSourceMode.API:
        push_client = PushClient(config=config.app.kabu_api)
        try:
            token = api_client.get_token()
            order_status_repository = OrderStatusRepository(
                api_client=api_client,
                token=token,
                logger=logger,
            )
            rest_poller = RestPoller(
                order_status_repository=order_status_repository,
                interval_sec=config.app.rest_poll_interval_sec,
            )
        except KabuApiError:
            logger.exception("kabu api initialization failed")
    return ExternalDataProcess(
        event_bus=event_bus,
        csv_loader=CsvMarketDataLoader(),
        push_client=push_client,
        rest_poller=rest_poller,
    )


def _build_order_gateway(
    config: SystemConfig,
    logger: logging.Logger,
    trading_process: TradingProcess | None = None,
    allow_real_order_disabled: bool = False,
) -> OrderGateway:
    if config.app.trading_mode == TradingMode.PAPER:
        return MockOrderGateway()
    if config.app.trading_mode == TradingMode.LIVE:
        if allow_real_order_disabled:
            logger.warning(
                "real order gateway disabled trading_mode=%s kabu_api_environment=%s base_url=%s",
                config.app.trading_mode.value,
                config.app.kabu_api.environment.value,
                config.app.kabu_api.base_url,
            )
            return MockOrderGateway()
        _ensure_live_order_allowed(config)
        enabled_symbols = tuple(
            symbol.code for symbol in config.symbols if symbol.enabled
        )
        logger.warning(
            "LIVE MODE gateway selected gateway=LiveOrderGateway symbols=%s",
            ",".join(enabled_symbols),
        )
        api_client = KabuApiClient(config=config.app.kabu_api)
        token = api_client.get_token()
        position_repository = PositionRepository(
            api_client=api_client,
            token=token,
            logger=logger,
        )
        order_status_repository = OrderStatusRepository(
            api_client=api_client,
            token=token,
            logger=logger,
        )
        return LiveOrderGateway(
            api_client=api_client,
            token=token,
            allowed_symbols=enabled_symbols,
            safety_validator=OrderSafetyValidator(
                trading_mode=config.app.trading_mode,
                kabu_api_environment=config.app.kabu_api.environment,
                max_order_quantity=config.app.max_order_quantity,
                trade_symbols=_resolve_enabled_trade_symbols(config),
                enabled_symbols=enabled_symbols,
                state_provider=_build_order_safety_state_provider(
                    position_repository=position_repository,
                    order_status_repository=order_status_repository,
                    risk_manager=(
                        trading_process.risk_manager
                        if trading_process is not None
                        else None
                    ),
                ),
                logger=logger,
            ),
        )
    raise ValueError(f"unsupported trading_mode={config.app.trading_mode.value}")


def _build_api_order_dry_run_resources(
    config: SystemConfig,
    logger: logging.Logger,
    trading_process: TradingProcess,
) -> ApiOrderDryRunResources:
    """api-order-dry-run 用の API リソースを構築する。"""

    api_client = KabuApiClient(config=config.app.kabu_api)
    token = api_client.get_token()
    enabled_symbols = tuple(symbol.code for symbol in config.symbols if symbol.enabled)
    position_repository = PositionRepository(
        api_client=api_client,
        token=token,
        logger=logger,
    )
    order_status_repository = OrderStatusRepository(
        api_client=api_client,
        token=token,
        logger=logger,
    )
    order_safety_validator = OrderSafetyValidator(
        trading_mode=config.app.trading_mode,
        kabu_api_environment=config.app.kabu_api.environment,
        max_order_quantity=config.app.max_order_quantity,
        trade_symbols=_resolve_enabled_trade_symbols(config),
        enabled_symbols=enabled_symbols,
        allow_api_paper_orders=True,
        state_provider=_build_order_safety_state_provider(
            position_repository=position_repository,
            order_status_repository=order_status_repository,
            risk_manager=trading_process.risk_manager,
        ),
        logger=logger,
    )
    return ApiOrderDryRunResources(
        order_status_repository=order_status_repository,
        position_repository=position_repository,
        position_reconciliation_service=PositionReconciliationService(
            position_repository=position_repository,
            average_price_tolerance=config.app.position_average_price_tolerance,
            logger=logger,
        ),
        order_safety_validator=order_safety_validator,
        order_gateway=LiveOrderGateway(
            api_client=api_client,
            token=token,
            allowed_symbols=enabled_symbols,
            safety_validator=order_safety_validator,
            logger=logger,
        ),
    )


def _build_order_safety_state_provider(
    position_repository: PositionRepository,
    order_status_repository: OrderStatusRepository,
    risk_manager: RiskManager | None = None,
):
    """注文前ガード用の状態取得関数を組み立てる。

    Args:
        position_repository: API建玉を取得するリポジトリ。
        order_status_repository: API注文状態を取得するリポジトリ。
    Returns:
        銘柄ごとの建玉と未完了注文を返す関数。
    """

    def provide(symbol: str) -> OrderSafetyState:
        if risk_manager is not None and risk_manager.state.kill_switch_active:
            reason = (
                risk_manager.state.trading_halt_reason.value
                if risk_manager.state.trading_halt_reason is not None
                else "kill_switch"
            )
            raise ValueError(f"trading is halted: {reason}")
        return OrderSafetyState(
            position=position_repository.get_position(symbol),
            open_orders=tuple(order_status_repository.get_open_orders(symbol=symbol)),
        )

    return provide


def _build_position_reconciliation_service(
    config: SystemConfig,
    logger: logging.Logger,
) -> PositionReconciliationService | None:
    """API実建玉突合サービスを構築する。

    Args:
        config: システム設定。
        logger: 利用するロガー。

    Returns:
        PositionReconciliationService | None: 利用可能な場合は突合サービス。
    """

    if (
        not config.app.position_reconciliation_enabled
        or config.app.data_source_mode != DataSourceMode.API
    ):
        return None
    try:
        api_client = KabuApiClient(config=config.app.kabu_api)
        token = api_client.get_token()
    except KabuApiError:
        logger.exception("position reconciliation initialization failed")
        if config.app.trading_mode == TradingMode.LIVE:
            raise
        return None
    return PositionReconciliationService(
        position_repository=PositionRepository(
            api_client=api_client,
            token=token,
            logger=logger,
        ),
        average_price_tolerance=config.app.position_average_price_tolerance,
        logger=logger,
    )


def _resolve_position_reconciliation_reason(
    error: PositionReconciliationError,
) -> TradingHaltReason:
    """建玉突合エラーから停止理由を決定する。"""

    if "failed to fetch api position" in str(error):
        return TradingHaltReason.POSITION_FETCH_FAILED
    return TradingHaltReason.POSITION_MISMATCH


def _ensure_live_order_allowed(config: SystemConfig) -> None:
    if (
        config.app.trading_mode == TradingMode.LIVE
        and config.app.kabu_api.environment == KabuApiEnvironment.LIVE
    ):
        return
    raise LiveOrderNotAllowedError(
        "live order requires trading_mode=live and kabu_api_environment=live"
    )


def _log_kabu_api_environment_warnings(
    logger: logging.Logger,
    config: SystemConfig,
) -> None:
    if (
        config.app.trading_mode == TradingMode.PAPER
        and config.app.kabu_api.environment == KabuApiEnvironment.LIVE
    ):
        logger.warning(
            "kabu api environment mismatch trading_mode=paper kabu_api_environment=live base_url=%s real orders are disabled",
            config.app.kabu_api.base_url,
        )
    if (
        config.app.trading_mode == TradingMode.LIVE
        and config.app.kabu_api.environment == KabuApiEnvironment.PAPER
    ):
        logger.warning(
            "kabu api environment mismatch trading_mode=live kabu_api_environment=paper base_url=%s real orders are disabled",
            config.app.kabu_api.base_url,
        )


def _build_risk_manager(config: SystemConfig) -> RiskManager:
    return RiskManager(
        config=config.risk,
        symbol_configs=tuple(
            RiskSymbolConfig(
                symbol=symbol.code,
                lot_min=symbol.lot_min,
                lot_max=symbol.lot_max,
                allocation_ratio=symbol.allocation_ratio,
            )
            for symbol in config.symbols
            if symbol.enabled
        ),
    )


def _resolve_symbol_int_parameter(
    symbol: Any,
    parameter_name: str,
) -> int | None:
    """銘柄別の戦略上書き値を整数として取得する。
    Args:
        symbol: 上書き設定を持つ銘柄設定。
        parameter_name: 取得するパラメータ名。
    Returns:
        int | None: 指定時は整数値、未指定時は None。
    """

    strategy_params = getattr(symbol.strategy_params_override, "strategy_params", {})
    value = strategy_params.get(parameter_name)
    if value is None:
        return None
    return int(value)


def _resolve_enabled_trade_symbols(config: SystemConfig) -> tuple[str, ...]:
    """有効化されている取引対象銘柄だけを返す。
    Args:
        config: システム設定。
    Returns:
        tuple[str, ...]: enabled=true の symbols に存在する取引対象銘柄。
    """

    enabled_symbols = {symbol.code for symbol in config.symbols if symbol.enabled}
    return tuple(
        symbol for symbol in config.app.trade_symbols if symbol in enabled_symbols
    )


def main(argv: list[str] | None = None) -> int:
    """設定読込・検証・基盤初期化または運用コマンドを実行する。"""

    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config(CONFIG_DIR)
        logger = setup_logger(config.app.log_level, process_name=EventSource.MAIN.value)
        if arguments:
            return _run_operational_command(arguments, config=config, logger=logger)
        initialize_application(block_api=True)
    except KeyboardInterrupt:
        logger.info("application interrupted")
        return 0
    except ConfigLoadError:
        # 設定読込前の障害だけは、設定値を参照できないため固定レベルを使う。
        logger = setup_logger(DEFAULT_LOG_LEVEL, process_name=EventSource.MAIN.value)
        logger.exception("application initialization failed")
        return 1
    except (ConfigValidationError, ValueError, TypeError):
        logger.exception("application initialization failed")
        return 1
    return 0


def _run_operational_command(
    arguments: list[str],
    config: SystemConfig,
    logger: logging.Logger,
) -> int:
    """運用コマンドを実行する。"""

    command = arguments[0]
    if command not in {
        "halt-status",
        "halt",
        "resume",
        "preflight-check",
        "api-order-precheck",
        "api-order-dry-run",
    }:
        raise ValueError(f"unsupported command: {command}")
    json_output = "--json" in arguments
    snapshot_store = OperationalSnapshotStore(
        config=config,
        storage=FileStorage(),
        clock=RealClock(),
    )

    if command == "halt-status":
        payload = _halt_state_payload(snapshot_store.load_halt_state())
        _log_operational_command(
            logger=logger,
            command=command,
            result="ok",
            reason=payload["reason"],
            message=payload["message"],
        )
        _write_cli_output(payload, json_output=json_output)
        return 0

    if command == "halt":
        reason = _parse_halt_reason(arguments)
        message = _parse_option(arguments, "--message") or ""
        halt_state = TradingHaltState(
            is_halted=True,
            reason=reason,
            message=message,
            halted_at=snapshot_store.clock.now(),
            resolved_at=None,
            requires_manual_resume=True,
        )
        snapshot_store.save_halt_state(halt_state)
        payload = _halt_state_payload(snapshot_store.load_halt_state())
        _log_operational_command(
            logger=logger,
            command=command,
            result="ok",
            reason=payload["reason"],
            message=payload["message"],
        )
        _write_cli_output(payload, json_output=json_output)
        return 0

    try:
        runtime = build_application_runtime(
            config=config,
            allow_real_order_disabled=True,
        )
    except Exception as error:
        if command == "api-order-dry-run":
            payload = _api_order_dry_run_error_payload(
                config=config,
                message=f"runtime initialization failed: {error}",
                halt_state=snapshot_store.load_halt_state(),
                symbol=_parse_option(arguments, "--symbol") or "",
                side=(_parse_option(arguments, "--side") or "").upper(),
                quantity=_parse_api_order_dry_run_quantity_for_error_payload(
                    arguments
                ),
            )
        elif command == "api-order-precheck":
            payload = _api_order_precheck_error_payload(
                config=config,
                message=f"runtime initialization failed: {error}",
                halt_state=snapshot_store.load_halt_state(),
                symbol=_parse_option(arguments, "--symbol") or "",
                side=(_parse_option(arguments, "--side") or "BUY").upper(),
                quantity=_parse_api_order_dry_run_quantity_for_error_payload(
                    arguments
                ),
            )
        else:
            payload = _error_payload(
                message=f"runtime initialization failed: {error}",
                halt_state=snapshot_store.load_halt_state(),
            )
        _log_operational_command(
            logger=logger,
            command=command,
            result="ng",
            reason=payload["reason"],
            message=payload["message"],
        )
        _write_cli_output(payload, json_output=json_output)
        return 1
    runtime.restore_snapshot_state()

    if command == "api-order-precheck":
        try:
            payload = runtime.run_api_order_precheck(
                symbol=_parse_required_option(arguments, "--symbol"),
                side=OrderSide((_parse_option(arguments, "--side") or "BUY").upper()),
                quantity=_parse_required_int_option(arguments, "--quantity"),
            )
        except Exception as error:
            payload = _api_order_precheck_error_payload(
                config=config,
                message=str(error),
                halt_state=runtime.get_trading_halt_state(),
                symbol=_parse_option(arguments, "--symbol") or "",
                side=(_parse_option(arguments, "--side") or "BUY").upper(),
                quantity=_parse_api_order_dry_run_quantity_for_error_payload(
                    arguments
                ),
            )
        _log_operational_command(
            logger=logger,
            command=command,
            result="ok" if payload["ok"] else "ng",
            reason=payload["reason"],
            message=payload["message"],
        )
        _write_cli_output(payload, json_output=json_output)
        return 0 if payload["ok"] else 1

    if command == "api-order-dry-run":
        try:
            payload = runtime.run_api_order_dry_run(
                symbol=_parse_required_option(arguments, "--symbol"),
                side=_parse_order_side(arguments),
                quantity=_parse_required_int_option(arguments, "--quantity"),
                price=_parse_optional_float_option(arguments, "--price"),
            )
        except Exception as error:
            payload = _api_order_dry_run_error_payload(
                config=config,
                message=str(error),
                halt_state=runtime.get_trading_halt_state(),
                symbol=_parse_option(arguments, "--symbol") or "",
                side=(_parse_option(arguments, "--side") or "").upper(),
                quantity=_parse_api_order_dry_run_quantity_for_error_payload(
                    arguments
                ),
            )
        _write_cli_output(payload, json_output=json_output)
        return 0 if payload["ok"] else 1

    if command == "resume":
        ok = runtime.resume_trading()
        runtime.save_snapshot_now()
        payload = _halt_state_payload(runtime.get_trading_halt_state())
        payload["ok"] = ok
        _log_operational_command(
            logger=logger,
            command=command,
            result="ok" if ok else "ng",
            reason=payload["reason"],
            message=payload["message"],
        )
        _write_cli_output(payload, json_output=json_output)
        return 0 if ok else 1

    payload = runtime.run_preflight_check()
    _log_operational_command(
        logger=logger,
        command=command,
        result="ok" if payload["ok"] else "ng",
        reason=payload["reason"],
        message=payload["message"],
    )
    _write_cli_output(payload, json_output=json_output)
    return 0 if payload["ok"] else 1


def _parse_halt_reason(arguments: list[str]) -> TradingHaltReason:
    """halt コマンドの停止理由を解釈する。"""

    value = _parse_option(arguments, "--reason")
    if value is None:
        return TradingHaltReason.MANUAL
    return TradingHaltReason(value.lower())


def _parse_option(arguments: list[str], name: str) -> str | None:
    """単純なオプション値を取得する。"""

    if name not in arguments:
        return None
    index = arguments.index(name)
    if index + 1 >= len(arguments):
        raise ValueError(f"missing option value: {name}")
    return arguments[index + 1]


def _parse_required_option(arguments: list[str], name: str) -> str:
    """必須オプション値を取得する。"""

    value = _parse_option(arguments, name)
    if value is None:
        raise ValueError(f"missing required option: {name}")
    return value


def _parse_required_int_option(arguments: list[str], name: str) -> int:
    """必須整数オプション値を取得する。"""

    return int(_parse_required_option(arguments, name))


def _parse_optional_int_option_safe(arguments: list[str], name: str) -> int:
    """整数オプションを安全に取得する。

    Args:
        arguments: CLI 引数一覧。
        name: 取得対象のオプション名。

    Returns:
        int: 数値へ変換できた場合はその値。未指定または不正値は 0。
    """

    value = _parse_option(arguments, name)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_api_order_dry_run_quantity_for_error_payload(arguments: list[str]) -> int:
    """api-order-dry-run のエラーペイロード用数量を安全に取得する。

    Args:
        arguments: CLI 引数一覧。

    Returns:
        int: エラーペイロードへ埋め込む数量。不正値は 0。
    """

    return _parse_optional_int_option_safe(arguments, "--quantity")


def _parse_optional_float_option(arguments: list[str], name: str) -> float | None:
    """任意の浮動小数点オプション値を取得する。"""

    value = _parse_option(arguments, name)
    return float(value) if value is not None else None


def _parse_order_side(arguments: list[str]) -> OrderSide:
    """注文 side オプションを解釈する。"""

    return OrderSide(_parse_required_option(arguments, "--side").upper())


def _halt_state_payload(halt_state: TradingHaltState) -> dict[str, Any]:
    """停止状態を出力用辞書へ変換する。"""

    return {
        "ok": True,
        "is_halted": halt_state.is_halted,
        "reason": halt_state.reason.value if halt_state.reason is not None else "",
        "message": halt_state.message,
        "halted_at": (
            halt_state.halted_at.isoformat() if halt_state.halted_at is not None else ""
        ),
        "resolved_at": (
            halt_state.resolved_at.isoformat()
            if halt_state.resolved_at is not None
            else ""
        ),
        "requires_manual_resume": halt_state.requires_manual_resume,
        "checks": [],
        "errors": [],
    }


def _error_payload(
    message: str,
    halt_state: TradingHaltState | None = None,
) -> dict[str, Any]:
    """CLI 異常終了時の出力形式を生成する。
    Args:
        message: 利用者へ返すエラー理由。
        halt_state: 併せて返す取引停止状態。未指定時は初期状態。
    Returns:
        dict[str, Any]: CLI 出力用のエラーペイロード。
    """

    payload = _halt_state_payload(halt_state or TradingHaltState())
    payload["ok"] = False
    payload["message"] = message
    payload["errors"] = [message]
    return payload


def _api_order_dry_run_error_payload(
    config: SystemConfig,
    message: str,
    halt_state: TradingHaltState | None = None,
    symbol: str = "",
    side: str = "",
    quantity: int = 0,
) -> dict[str, Any]:
    """api-order-dry-run の異常終了用 payload を生成する。"""

    payload = {
        "ok": False,
        "command": "api-order-dry-run",
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "order_id": "",
        "order_status": "",
        "filled_quantity": 0,
        "remaining_quantity": quantity,
        "position_quantity": 0,
        "position_side": "NONE",
        "reconciliation_result": "UNKNOWN",
        "position": {
            "symbol": symbol,
            "quantity": 0,
            "average_price": 0.0,
        },
        "trading_mode": config.app.trading_mode.value,
        "kabu_api_environment": config.app.kabu_api.environment.value,
        "base_url": config.app.kabu_api.base_url,
        "is_halted": halt_state.is_halted if halt_state is not None else False,
        "reason": (
            halt_state.reason.value if halt_state is not None and halt_state.reason else ""
        ),
        "halt_reason": (
            halt_state.reason.value if halt_state is not None and halt_state.reason else ""
        ),
        "message": message,
        "next_action": [],
        "log_hint": [],
        "checks": [],
        "errors": [message],
    }
    return _finalize_api_order_dry_run_payload(payload)


def _api_order_precheck_payload(
    config: SystemConfig,
    symbol: str,
    side: str,
    quantity: int,
    halt_state: TradingHaltState | None = None,
) -> dict[str, Any]:
    """api-order-precheck の初期 payload を生成する。

    Args:
        config: システム設定。
        symbol: 対象銘柄コード。
        side: 想定売買方向。
        quantity: 想定数量。
        halt_state: 現在の停止状態。

    Returns:
        dict[str, Any]: 事前確認結果用 payload。
    """

    return api_order_precheck_cli._api_order_precheck_payload(
        config=config,
        symbol=symbol,
        side=side,
        quantity=quantity,
        halt_state=halt_state,
    )


def _api_order_precheck_error_payload(
    config: SystemConfig,
    message: str,
    halt_state: TradingHaltState | None = None,
    symbol: str = "",
    side: str = "BUY",
    quantity: int = 0,
) -> dict[str, Any]:
    """api-order-precheck の異常終了用 payload を生成する。

    Args:
        config: システム設定。
        message: エラーメッセージ。
        halt_state: 現在の停止状態。
        symbol: 対象銘柄コード。
        side: 想定売買方向。
        quantity: 想定数量。

    Returns:
        dict[str, Any]: 整形済みのエラーペイロード。
    """

    return api_order_precheck_cli._api_order_precheck_error_payload(
        config=config,
        message=message,
        halt_state=halt_state,
        symbol=symbol,
        side=side,
        quantity=quantity,
    )


def _finalize_api_order_dry_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """api-order-dry-run の表示用項目を補完する。"""

    payload["halt_reason"] = payload.get("halt_reason") or payload.get("reason", "")
    payload["position_side"] = _resolve_position_side_label(
        int(payload.get("position_quantity", 0))
    )
    payload["reconciliation_result"] = _resolve_reconciliation_result(payload)
    payload["next_action"] = _resolve_api_order_dry_run_next_action(payload)
    payload["log_hint"] = _resolve_api_order_dry_run_log_hint(payload)
    return payload


def _finalize_api_order_precheck_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """api-order-precheck の表示用項目を補完する。

    Args:
        payload: 補完対象の payload。

    Returns:
        dict[str, Any]: 補完後の payload。
    """

    return api_order_precheck_cli._finalize_api_order_precheck_payload(payload)


def _resolve_position_side_label(position_quantity: int) -> str:
    """建玉数量から売買方向表示を返す。"""

    if position_quantity > 0:
        return "BUY"
    if position_quantity < 0:
        return "SELL"
    return "NONE"


def _resolve_reconciliation_result(payload: dict[str, Any]) -> str:
    """建玉突合結果を表示用に整形する。"""

    target_checks = [
        check
        for check in payload.get("checks", [])
        if check["name"] in {"position_reconciliation", "preflight_position_reconciliation"}
    ]
    if not target_checks:
        return payload.get("reconciliation_result", "UNKNOWN")
    return "OK" if all(check["ok"] for check in target_checks) else "NG"


def _resolve_api_order_dry_run_next_action(payload: dict[str, Any]) -> list[str]:
    """api-order-dry-run の次アクションを返す。"""

    errors = [str(error) for error in payload.get("errors", [])]
    error_text = " ".join(errors)
    order_status = str(payload.get("order_status", "")).upper()
    remaining_quantity = int(payload.get("remaining_quantity", 0))
    reconciliation_result = str(payload.get("reconciliation_result", "UNKNOWN")).upper()

    if payload.get("ok"):
        if order_status in {"NEW", "REQUESTED", "PARTIALLY_FILLED"} or remaining_quantity > 0:
            return [
                "時間を置いて注文状態同期を確認してください",
                "未完了注文が残っているため、同一銘柄の再実行は避けてください",
            ]
        return [
            "preflight-check を実行して状態を再確認してください",
            "halt-status で取引停止状態でないことを確認してください",
        ]
    if reconciliation_result == "NG" or "position reconciliation failed" in error_text:
        return [
            "取引停止状態を確認してください",
            "API建玉と内部建玉を確認し、原因調査後に resume を実行してください",
        ]
    if "kabu" in error_text.lower() or "api" in error_text.lower():
        return [
            "kabuステーションが検証モードで起動しているか確認してください",
            "kabu_api_environment が paper であることを確認してください",
        ]
    if payload.get("is_halted"):
        return [
            "halt-status で停止理由を確認してください",
            "原因調査後、必要なら resume を実行してください",
        ]
    return [
        "errors を確認し、log_hint に従ってログを調査してください",
        "halt-status で取引停止状態を確認してください",
    ]


def _resolve_api_order_dry_run_log_hint(payload: dict[str, Any]) -> list[str]:
    """api-order-dry-run のログ確認ヒントを返す。"""

    hints = [
        "アプリケーションログ出力を確認してください",
        "command=api-order-dry-run で検索してください",
    ]
    if payload.get("order_id"):
        hints.append(
            f"order_id={payload['order_id']} で検索してください"
        )
    return hints


def _resolve_api_order_precheck_next_action(payload: dict[str, Any]) -> list[str]:
    """api-order-precheck の次アクションを返す。

    Args:
        payload: 事前確認結果 payload。

    Returns:
        list[str]: 次に取るべき行動。
    """

    return api_order_precheck_cli._resolve_api_order_precheck_next_action(payload)


def _is_paper_api_base_url(base_url: str) -> bool:
    """検証 API 用 base_url かを判定する。

    Args:
        base_url: 判定対象 URL。

    Returns:
        bool: 18081 ポートを指していれば True。
    """

    return api_order_precheck_cli._is_paper_api_base_url(base_url)


def _write_cli_output(payload: dict[str, Any], json_output: bool) -> None:
    """CLI 出力を標準出力へ書き出す。"""

    if payload.get("command") == "api-order-dry-run":
        _finalize_api_order_dry_run_payload(payload)
    if payload.get("command") == "api-order-precheck":
        _finalize_api_order_precheck_payload(payload)
    if json_output:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
        sys.stdout.write("\n")
        return
    if payload.get("command") == "api-order-precheck":
        sys.stdout.write(f"command: {payload['command']}\n")
        sys.stdout.write(f"ok: {payload['ok']}\n")
        sys.stdout.write(f"trading_mode: {payload['trading_mode']}\n")
        sys.stdout.write(f"kabu_api_environment: {payload['kabu_api_environment']}\n")
        sys.stdout.write(f"base_url: {payload['base_url']}\n")
        sys.stdout.write(f"symbol: {payload['symbol']}\n")
        sys.stdout.write(f"side: {payload['side']}\n")
        sys.stdout.write(f"quantity: {payload['quantity']}\n")
        if payload.get("checks"):
            sys.stdout.write("checks:\n")
            for check in payload["checks"]:
                sys.stdout.write(
                    f"  - {check['name']}: {'OK' if check['ok'] else 'NG'} {check['message']}\n"
                )
        if payload.get("errors"):
            sys.stdout.write("errors:\n")
            for error in payload["errors"]:
                sys.stdout.write(f"  - {error}\n")
        if payload.get("next_action"):
            sys.stdout.write("next_action:\n")
            for action in payload["next_action"]:
                sys.stdout.write(f"  - {action}\n")
        return
    if payload.get("command") == "api-order-dry-run":
        sys.stdout.write(f"command: {payload['command']}\n")
        sys.stdout.write(f"ok: {payload['ok']}\n")
        sys.stdout.write(f"trading_mode: {payload['trading_mode']}\n")
        sys.stdout.write(f"kabu_api_environment: {payload['kabu_api_environment']}\n")
        sys.stdout.write(f"base_url: {payload['base_url']}\n")
        sys.stdout.write(f"symbol: {payload['symbol']}\n")
        sys.stdout.write(f"side: {payload['side']}\n")
        sys.stdout.write(f"quantity: {payload['quantity']}\n")
        sys.stdout.write(f"order_id: {payload['order_id']}\n")
        sys.stdout.write(f"order_status: {payload['order_status']}\n")
        sys.stdout.write(f"filled_quantity: {payload['filled_quantity']}\n")
        sys.stdout.write(f"remaining_quantity: {payload['remaining_quantity']}\n")
        sys.stdout.write(f"position_quantity: {payload['position_quantity']}\n")
        sys.stdout.write(f"position_side: {payload['position_side']}\n")
        sys.stdout.write(
            f"reconciliation_result: {payload['reconciliation_result']}\n"
        )
        sys.stdout.write(f"is_halted: {payload['is_halted']}\n")
        sys.stdout.write(f"halt_reason: {payload['halt_reason']}\n")
        if payload.get("checks"):
            sys.stdout.write("checks:\n")
            for check in payload["checks"]:
                sys.stdout.write(
                    f"  - {check['name']}: {'OK' if check['ok'] else 'NG'} {check['message']}\n"
                )
        if payload.get("errors"):
            sys.stdout.write("errors:\n")
            for error in payload["errors"]:
                sys.stdout.write(f"  - {error}\n")
        if payload.get("next_action"):
            sys.stdout.write("next_action:\n")
            for action in payload["next_action"]:
                sys.stdout.write(f"  - {action}\n")
        if payload.get("log_hint"):
            sys.stdout.write("log_hint:\n")
            for hint in payload["log_hint"]:
                sys.stdout.write(f"  - {hint}\n")
        return
    sys.stdout.write(f"ok: {payload['ok']}\n")
    sys.stdout.write(f"is_halted: {payload['is_halted']}\n")
    sys.stdout.write(f"reason: {payload['reason']}\n")
    sys.stdout.write(f"message: {payload['message']}\n")
    sys.stdout.write(f"halted_at: {payload.get('halted_at', '')}\n")
    sys.stdout.write(f"resolved_at: {payload.get('resolved_at', '')}\n")
    sys.stdout.write(
        f"requires_manual_resume: {payload.get('requires_manual_resume', False)}\n"
    )
    if payload.get("checks"):
        sys.stdout.write("checks:\n")
        for check in payload["checks"]:
            sys.stdout.write(
                f"  - {check['name']}: {'OK' if check['ok'] else 'NG'} {check['message']}\n"
            )
    if payload.get("errors"):
        sys.stdout.write("errors:\n")
        for error in payload["errors"]:
            sys.stdout.write(f"  - {error}\n")


def _log_operational_command(
    logger: logging.Logger,
    command: str,
    result: str,
    reason: str,
    message: str,
) -> None:
    """運用コマンド実行結果をログ出力する。"""

    logger.info(
        "operational command command=%s result=%s reason=%s message=%s",
        command,
        result,
        reason,
        message,
    )


if __name__ == "__main__":
    raise SystemExit(main())

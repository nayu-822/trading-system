import logging
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

from data_source.csv_loader import CsvMarketDataLoader
from data_source.kabu_api_client import KabuApiClient, KabuApiError
from data_source.push_client import PushClient
from data_source.rest_poller import RestPoller
from domain.enums import (
    DataSourceMode,
    EventSource,
    EventType,
    KabuApiEnvironment,
    OrderStatus,
    StrategyType,
    TradingHaltReason,
    TradingMode,
)
from domain.events import EventFactory, SystemStartedPayload
from domain.models import (
    Order,
    RiskSymbolConfig,
    SignalStrategyConfig,
    SystemConfig,
    TradingHaltState,
    TradingSymbolConfig,
)
from infrastructure.clock import RealClock
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
        for symbol in (symbol.code for symbol in self.config.symbols if symbol.enabled):
            try:
                api_orders = {
                    self._order_identity(order)
                    for order in rest_poller.order_status_repository.get_open_orders(
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
            )
            for symbol in config.symbols
            if symbol.enabled
        ),
        range_window=config.strategy.range.window,
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
) -> OrderGateway:
    if config.app.trading_mode == TradingMode.PAPER:
        return MockOrderGateway()
    if config.app.trading_mode == TradingMode.LIVE:
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
                trade_symbols=config.app.trade_symbols,
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
    if command not in {"halt-status", "halt", "resume", "preflight-check"}:
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
        runtime = build_application_runtime(config=config)
    except Exception as error:
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


def _write_cli_output(payload: dict[str, Any], json_output: bool) -> None:
    """CLI 出力を標準出力へ書き出す。"""

    if json_output:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
        sys.stdout.write("\n")
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

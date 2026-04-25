import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

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
    TradingSymbolConfig,
)
from infrastructure.clock import RealClock
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError, validate_config
from infrastructure.event_bus import EventBus
from infrastructure.file_storage import FileStorage
from infrastructure.logger import setup_logger
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
        try:
            self.external_data_process.sync_orders_once(force_refresh=True)
        except Exception as error:
            risk_manager.halt_trading(
                reason=TradingHaltReason.ORDER_SYNC_FAILED,
                message=f"order sync failed during resume: {error}",
            )
            self._log_resume_failed()
            return False
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


def main() -> int:
    """設定読込・検証・基盤初期化を実行する。

    Args:
        なし。

    Returns:
        終了コード。
    """

    try:
        config = load_config(CONFIG_DIR)
        logger = setup_logger(config.app.log_level, process_name=EventSource.MAIN.value)
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


if __name__ == "__main__":
    raise SystemExit(main())

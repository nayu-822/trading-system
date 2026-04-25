from datetime import date, datetime, timezone

from domain.enums import (
    EventSource,
    EventType,
    KabuApiEnvironment,
    OrderSide,
    SignalType,
    StrategyType,
    TradingMode,
)
from domain.events import BaseEvent, EventFactory, MarketDataPayload, SignalPayload
from domain.models import (
    AccountState,
    IndicatorValue,
    Position,
    RiskConfig,
    RiskControlState,
    RiskSymbolConfig,
    TradeResult,
    TradingSymbolState,
)
from infrastructure.event_bus import EventBus
from processes.trading_process import TradingProcess
from trading.live_order_gateway import LiveOrderGateway
from trading.order_safety_validator import OrderSafetyState, OrderSafetyValidator
from trading.risk_manager import RiskManager


def test_risk_manager_rejects_entry_when_position_limit_is_reached() -> None:
    risk_manager = _risk_manager(max_positions=1)
    states = (
        TradingSymbolState(
            symbol="6758",
            lot_size=100,
            position=Position(symbol="6758", quantity=100, average_price=1000.0),
        ),
    )

    assert risk_manager.can_enter("7203", OrderSide.BUY, states) is False


def test_risk_manager_calculates_zero_lot_when_budget_is_too_small() -> None:
    risk_manager = _risk_manager(account_equity=50.0)

    lot = risk_manager.calculate_lot(
        "7203", AccountState(available_equity=50.0, reference_price=1000.0)
    )

    assert lot == 0


def test_risk_manager_calculates_smaller_lot_when_price_is_higher() -> None:
    low_price_manager = _risk_manager(account_equity=1000000.0, lot_max=1000)
    high_price_manager = _risk_manager(account_equity=1000000.0, lot_max=1000)

    low_price_lot = low_price_manager.calculate_lot(
        "7203", AccountState(available_equity=1000000.0, reference_price=1000.0)
    )
    high_price_lot = high_price_manager.calculate_lot(
        "7203", AccountState(available_equity=1000000.0, reference_price=10000.0)
    )

    assert low_price_lot == 1000
    assert high_price_lot == 100


def test_risk_manager_stops_after_losses_and_resumes_after_wins() -> None:
    risk_manager = _risk_manager(max_consecutive_losses=2, resume_consecutive_wins=2)

    risk_manager.on_trade_result(TradeResult(symbol="7203", realized_pnl=-100.0))
    risk_manager.on_trade_result(TradeResult(symbol="7203", realized_pnl=-100.0))

    assert risk_manager.state.stopped_by_losses is True
    assert risk_manager.can_enter("7203", OrderSide.BUY, ()) is False

    risk_manager.on_trade_result(TradeResult(symbol="7203", realized_pnl=100.0))
    risk_manager.on_trade_result(TradeResult(symbol="7203", realized_pnl=100.0))

    assert risk_manager.state.stopped_by_losses is False
    assert risk_manager.can_enter("7203", OrderSide.BUY, ()) is True


def test_risk_manager_stops_when_drawdown_exceeds_limit() -> None:
    risk_manager = _risk_manager(max_drawdown=100.0)

    risk_manager.update_equity(1000.0)
    risk_manager.update_equity(900.0)

    assert risk_manager.state.kill_switch_active is True
    assert risk_manager.can_enter("7203", OrderSide.BUY, ()) is False


def test_risk_manager_kill_switch_rejects_entry() -> None:
    risk_manager = _risk_manager()

    risk_manager.activate_kill_switch("test")

    assert risk_manager.can_enter("7203", OrderSide.BUY, ()) is False


def test_risk_manager_api_errors_activate_kill_switch() -> None:
    risk_manager = _risk_manager(api_error_limit=2)

    risk_manager.on_api_error()
    risk_manager.on_api_error()

    assert risk_manager.state.kill_switch_active is True


def test_risk_manager_resets_daily_loss_when_business_date_changes() -> None:
    business_dates = [date(2026, 4, 18), date(2026, 4, 19)]
    risk_manager = _risk_manager(
        max_daily_loss=100.0,
        today_provider=lambda: business_dates[0],
    )

    risk_manager.on_trade_result(TradeResult(symbol="7203", realized_pnl=-50.0))
    business_dates[0] = business_dates[1]
    risk_manager.on_trade_result(TradeResult(symbol="7203", realized_pnl=-10.0))

    assert risk_manager.state.business_date == "2026-04-19"
    assert risk_manager.state.daily_realized_loss == 10.0


def test_trading_process_restores_risk_state_from_snapshot() -> None:
    original = TradingProcess(
        event_bus=EventBus(),
        risk_manager=_risk_manager(),
        auto_fill_orders=False,
    )
    assert original.risk_manager is not None
    original.risk_manager.on_trade_result(TradeResult(symbol="7203", realized_pnl=-1.0))
    snapshot = original.get_snapshot(_timestamp())
    restored = TradingProcess(
        event_bus=EventBus(),
        risk_manager=_risk_manager(),
        auto_fill_orders=False,
    )

    restored.restore_snapshot(snapshot)

    assert restored.risk_manager is not None
    assert restored.risk_manager.state.consecutive_losses == 1


def test_trading_process_applies_risk_manager_in_paper_mode() -> None:
    event_bus = EventBus()
    risk_manager = _risk_manager(account_equity=50.0)
    trading_process = TradingProcess(event_bus=event_bus, risk_manager=risk_manager)

    trading_process.start()
    event_bus.publish(_signal_event())

    assert trading_process.get_state("7203").orders == []


def test_trading_process_applies_risk_manager_in_live_mode() -> None:
    class FakeApiClient:
        called = False

        def send_order(self, token: str, order_request: object) -> None:
            self.called = True
            raise AssertionError("live order should not be sent")

    event_bus = EventBus()
    api_client = FakeApiClient()
    trading_process = TradingProcess(
        event_bus=event_bus,
        risk_manager=_risk_manager(account_equity=50.0),
        order_gateway=LiveOrderGateway(
            api_client=api_client,  # type: ignore[arg-type]
            token="token-1",
            allowed_symbols=("7203",),
            safety_validator=OrderSafetyValidator(
                trading_mode=TradingMode.LIVE,
                kabu_api_environment=KabuApiEnvironment.LIVE,
                max_order_quantity=100,
                trade_symbols=("7203",),
                enabled_symbols=("7203",),
                state_provider=lambda symbol: OrderSafetyState(
                    position=Position(symbol=symbol)
                ),
            ),
        ),
    )

    trading_process.start()
    event_bus.publish(_signal_event())

    assert api_client.called is False
    assert trading_process.get_state("7203").orders == []


def test_trading_process_counts_gateway_error_for_kill_switch() -> None:
    class FailingGateway:
        def place_order(
            self, order: object, timestamp: datetime
        ) -> tuple[BaseEvent, ...]:
            raise RuntimeError("gateway failed")

        def cancel_order(
            self, order: object, timestamp: datetime
        ) -> tuple[BaseEvent, ...]:
            return ()

    risk_manager = _risk_manager(api_error_limit=1)
    trading_process = TradingProcess(
        event_bus=EventBus(),
        risk_manager=risk_manager,
        order_gateway=FailingGateway(),  # type: ignore[arg-type]
    )

    trading_process.start()
    trading_process.event_bus.publish(_market_data_event())
    trading_process.event_bus.publish(_signal_event())

    assert risk_manager.state.kill_switch_active is True


def _risk_manager(
    account_equity: float = 1000000.0,
    max_positions: int = 3,
    max_consecutive_losses: int = 5,
    resume_consecutive_wins: int = 2,
    max_drawdown: float = 50000.0,
    api_error_limit: int = 5,
    max_daily_loss: float = 10000.0,
    lot_max: int = 100,
    today_provider=lambda: date(2026, 4, 18),
) -> RiskManager:
    return RiskManager(
        config=RiskConfig(
            max_daily_loss=max_daily_loss,
            max_consecutive_losses=max_consecutive_losses,
            resume_consecutive_wins=resume_consecutive_wins,
            max_positions=max_positions,
            max_position_per_symbol=1000,
            account_equity=account_equity,
            max_drawdown=max_drawdown,
            kill_switch_enabled=True,
            api_error_limit=api_error_limit,
            trading_start_time="09:00",
            trading_end_time="15:00",
            order_timeout_sec=30,
        ),
        symbol_configs=(
            RiskSymbolConfig(
                symbol="7203",
                lot_min=100,
                lot_max=lot_max,
                allocation_ratio=1.0,
            ),
        ),
        state=RiskControlState(
            current_equity=account_equity, max_equity=account_equity
        ),
        today_provider=today_provider,
    )


def _signal_event() -> BaseEvent:
    return EventFactory(source=EventSource.SIGNAL).create(
        event_type=EventType.SIGNAL_DETECTED,
        timestamp=_timestamp(),
        symbol="7203",
        payload=SignalPayload(
            signal_type=SignalType.BUY,
            strategy_type=StrategyType.TREND,
            indicators=(IndicatorValue(name="current_price", value=1000.0),),
        ),
    )


def _market_data_event() -> BaseEvent:
    return EventFactory(source=EventSource.EXTERNAL_DATA).create(
        event_type=EventType.MARKET_DATA_UPDATED,
        timestamp=_timestamp(),
        symbol="7203",
        payload=MarketDataPayload(
            timestamp=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
            price=1000.0,
            bid=999.0,
            ask=1001.0,
            volume=1000,
        ),
    )


def _timestamp() -> datetime:
    return datetime(2026, 4, 18, tzinfo=timezone.utc)

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_source.csv_loader import CsvMarketDataLoader
from data_source.kabu_api_client import KabuApiError
from domain.enums import (
    DataSourceMode,
    EventSource,
    EventType,
    KabuApiEnvironment,
    OrderSide,
    OrderStatus,
    SignalType,
    StrategyType,
    TradingHaltReason,
    TradingMode,
)
from domain.events import (
    BaseEvent,
    EventFactory,
    MarketDataPayload,
    OrderRequested,
    OrderStatusPayload,
    PositionUpdated,
    SignalDetected,
    SignalPayload,
)
from domain.models import (
    IndicatorValue,
    KabuOrderRequest,
    KabuOrderResult,
    Order,
    Position,
    SignalStrategyConfig,
    TradingProcessConfig,
    TradingSymbolConfig,
)
from infrastructure.event_bus import EventBus
from processes.external_data_process import ExternalDataProcess
from processes.signal_process import SignalProcess, SignalStrategySlot
from processes.trading_process import TradingProcess
from strategy.trend_strategy import TrendStrategy
from trading.live_order_gateway import LiveOrderGateway
from trading.order_safety_validator import OrderSafetyState, OrderSafetyValidator

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _create_market_data_event(price: float, symbol: str = "7203") -> BaseEvent[Any]:
    timestamp = datetime(2026, 4, 18, tzinfo=timezone.utc)
    return EventFactory(source=EventSource.EXTERNAL_DATA).create(
        event_type=EventType.MARKET_DATA_UPDATED,
        timestamp=timestamp,
        symbol=symbol,
        payload=MarketDataPayload(
            price=price,
            bid=None,
            ask=None,
            volume=None,
            timestamp=timestamp,
        ),
    )


def _create_signal_event(
    signal_type: SignalType,
    symbol: str = "7203",
) -> BaseEvent[Any]:
    timestamp = datetime(2026, 4, 18, tzinfo=timezone.utc)
    return EventFactory(source=EventSource.SIGNAL).create(
        event_type=EventType.SIGNAL_DETECTED,
        timestamp=timestamp,
        symbol=symbol,
        payload=SignalPayload(
            signal_type=signal_type,
            strategy_type=StrategyType.TREND,
            indicators=(IndicatorValue(name="current_price", value=1000.0),),
        ),
    )


def test_signal_process_publishes_buy_when_price_rises() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        trend_short_window=1,
        trend_long_window=2,
    )
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    event_bus.publish(_create_market_data_event(price=100.0))
    event_bus.publish(_create_market_data_event(price=101.0))

    assert len(received_events) == 1
    assert isinstance(received_events[0], SignalDetected)
    assert received_events[0].payload.signal_type == SignalType.BUY
    assert received_events[0].payload.indicators[0].name == "short_ma"
    assert received_events[0].payload.indicators[0].value == 101.0


def test_signal_process_publishes_sell_when_price_falls() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        trend_short_window=1,
        trend_long_window=2,
    )
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    event_bus.publish(_create_market_data_event(price=101.0))
    event_bus.publish(_create_market_data_event(price=100.0))

    assert len(received_events) == 1
    assert isinstance(received_events[0], SignalDetected)
    assert received_events[0].payload.signal_type == SignalType.SELL
    assert received_events[0].payload.indicators[0].name == "short_ma"
    assert received_events[0].payload.indicators[0].value == 100.0


def test_signal_process_keeps_last_price_by_symbol() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        trend_short_window=1,
        trend_long_window=2,
    )
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    event_bus.publish(_create_market_data_event(price=100.0, symbol="7203"))
    event_bus.publish(_create_market_data_event(price=500.0, symbol="6758"))
    event_bus.publish(_create_market_data_event(price=101.0, symbol="7203"))
    event_bus.publish(_create_market_data_event(price=499.0, symbol="6758"))

    assert len(received_events) == 2
    assert received_events[0].symbol == "7203"
    assert received_events[0].payload.signal_type == SignalType.BUY
    assert received_events[0].payload.indicators[2].name == "ma_diff"
    assert received_events[0].payload.indicators[2].value == 0.5
    assert received_events[1].symbol == "6758"
    assert received_events[1].payload.signal_type == SignalType.SELL
    assert received_events[1].payload.indicators[2].name == "ma_diff"
    assert received_events[1].payload.indicators[2].value == -0.5
    toyota_slot = _find_strategy_slot(signal_process, symbol="7203")
    sony_slot = _find_strategy_slot(signal_process, symbol="6758")

    assert isinstance(toyota_slot.strategy, TrendStrategy)
    assert isinstance(sony_slot.strategy, TrendStrategy)
    assert toyota_slot.strategy.state.last_price == 101.0
    assert sony_slot.strategy.state.last_price == 499.0


def test_signal_process_switches_strategy_by_symbol_config() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        strategy_configs=(
            SignalStrategyConfig(
                symbol="7203",
                strategy_type=StrategyType.TREND,
            ),
            SignalStrategyConfig(
                symbol="6758",
                strategy_type=StrategyType.RANGE,
            ),
        ),
        range_window=2,
        trend_short_window=1,
        trend_long_window=2,
    )
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    event_bus.publish(_create_market_data_event(price=100.0, symbol="7203"))
    event_bus.publish(_create_market_data_event(price=101.0, symbol="7203"))
    event_bus.publish(_create_market_data_event(price=100.0, symbol="6758"))
    event_bus.publish(_create_market_data_event(price=102.0, symbol="6758"))
    event_bus.publish(_create_market_data_event(price=99.0, symbol="6758"))

    assert len(received_events) == 2
    assert received_events[0].symbol == "7203"
    assert received_events[0].payload.strategy_type == StrategyType.TREND
    assert received_events[0].payload.signal_type == SignalType.BUY
    assert received_events[1].symbol == "6758"
    assert received_events[1].payload.strategy_type == StrategyType.RANGE
    assert received_events[1].payload.signal_type == SignalType.BUY


def test_signal_process_applies_symbol_range_window_override() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        strategy_configs=(
            SignalStrategyConfig(
                symbol="1306",
                strategy_type=StrategyType.RANGE,
                range_window=30,
            ),
        ),
        range_window=20,
    )

    signal_process.handle_market_data(
        _create_market_data_event(price=100.0, symbol="1306")
    )
    slot = _find_strategy_slot(signal_process, symbol="1306")

    assert slot.active_strategy_type == StrategyType.RANGE
    assert slot.strategy.window == 30


def test_signal_process_applies_symbol_trend_window_override() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        strategy_configs=(
            SignalStrategyConfig(
                symbol="1570",
                strategy_type=StrategyType.TREND,
                trend_short_window=3,
                trend_long_window=15,
            ),
        ),
        trend_short_window=5,
        trend_long_window=25,
    )

    signal_process.handle_market_data(
        _create_market_data_event(price=100.0, symbol="1570")
    )
    slot = _find_strategy_slot(signal_process, symbol="1570")

    assert slot.active_strategy_type == StrategyType.TREND
    assert slot.strategy.short_window == 3
    assert slot.strategy.long_window == 15


def test_signal_process_uses_global_windows_when_symbol_override_is_missing() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        strategy_configs=(
            SignalStrategyConfig(
                symbol="1321",
                strategy_type=StrategyType.TREND,
            ),
        ),
        range_window=25,
        trend_short_window=5,
        trend_long_window=25,
    )

    signal_process.handle_market_data(
        _create_market_data_event(price=100.0, symbol="1321")
    )
    slot = _find_strategy_slot(signal_process, symbol="1321")

    assert slot.strategy.short_window == 5
    assert slot.strategy.long_window == 25


def test_signal_process_falls_back_to_trend_when_auto_is_configured(caplog) -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        strategy_configs=(
            SignalStrategyConfig(
                symbol="7203",
                strategy_type=StrategyType.AUTO,
            ),
        ),
        trend_short_window=1,
        trend_long_window=2,
    )
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    with caplog.at_level("WARNING"):
        event_bus.publish(_create_market_data_event(price=100.0, symbol="7203"))
        event_bus.publish(_create_market_data_event(price=101.0, symbol="7203"))

    slot = _find_strategy_slot(signal_process, symbol="7203")

    assert "auto strategy is not implemented" in caplog.text
    assert slot.requested_strategy_type == StrategyType.AUTO
    assert slot.active_strategy_type == StrategyType.TREND
    assert isinstance(slot.strategy, TrendStrategy)
    assert len(received_events) == 1
    assert received_events[0].payload.signal_type == SignalType.BUY
    assert received_events[0].payload.strategy_type == StrategyType.TREND
    assert received_events[0].payload.indicators[0].name == "short_ma"
    assert received_events[0].payload.indicators[0].value == 101.0


def test_signal_process_start_stop_start_does_not_duplicate_subscription() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        trend_short_window=1,
        trend_long_window=2,
    )
    received_events: list[BaseEvent[Any]] = []

    signal_process.start()
    signal_process.stop()
    signal_process.start()
    event_bus.subscribe(EventType.SIGNAL_DETECTED, received_events.append)
    event_bus.publish(_create_market_data_event(price=100.0))
    event_bus.publish(_create_market_data_event(price=101.0))

    assert len(event_bus._subscribers[EventType.MARKET_DATA_UPDATED]) == 1
    assert len(received_events) == 1


def test_trading_process_publishes_order_requested_from_signal() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        lot_configs=(TradingSymbolConfig(symbol="7203", lot_size=200),),
    )
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert len(received_events) == 1
    assert isinstance(received_events[0], OrderRequested)
    assert received_events[0].payload.symbol == "7203"
    assert received_events[0].payload.side == OrderSide.BUY
    assert received_events[0].payload.quantity == 200


def test_trading_process_does_not_publish_for_symbol_outside_trade_symbols() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=50,
        process_config=_trading_process_config(trade_symbols=("1306",)),
    )
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert received_events == []


def test_trading_process_does_not_publish_when_symbol_state_is_busy() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=50)
    state = trading_process.get_state("7203")
    state.is_busy = True
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert received_events == []


def test_trading_process_does_not_publish_when_quantity_is_zero() -> None:
    event_bus = EventBus()

    class ZeroLotRiskManager:
        def __init__(self) -> None:
            self.state = type(
                "RiskState",
                (),
                {
                    "kill_switch_active": False,
                    "trading_halt_reason": None,
                    "current_equity": 1_000_000.0,
                },
            )()

        def can_enter(self, symbol: str, side: OrderSide, current_state) -> bool:
            return True

        def calculate_lot(self, symbol: str, account_state) -> int:
            return 0

    trading_process = TradingProcess(
        event_bus=event_bus,
        risk_manager=ZeroLotRiskManager(),  # type: ignore[arg-type]
        process_config=_trading_process_config(max_order_quantity=100),
    )
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert received_events == []


def test_trading_process_does_not_publish_when_quantity_exceeds_max_order_quantity() -> (
    None
):
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        process_config=_trading_process_config(max_order_quantity=50),
    )
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert received_events == []


def test_trading_process_does_not_publish_sell_without_position() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.SELL))

    assert received_events == []


def test_trading_process_uses_configured_quantity_instead_of_fixed_100() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=25,
        process_config=_trading_process_config(max_order_quantity=25),
    )
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert len(received_events) == 1
    assert received_events[0].payload.quantity == 25


def test_trading_process_does_not_publish_when_market_is_closed() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=25,
        process_config=_trading_process_config(
            max_order_quantity=25,
            trading_start_time="09:00",
            trading_end_time="15:00",
        ),
        current_time_provider=lambda: datetime(2026, 4, 18, 8, 0, tzinfo=timezone.utc),
    )
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert received_events == []


def test_trading_process_does_not_publish_duplicate_order_for_same_symbol() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))
    event_bus.publish(_create_signal_event(SignalType.SELL))

    state = trading_process.get_state("7203")

    assert len(received_events) == 1
    assert len(state.orders) == 1
    assert state.orders[0].status == OrderStatus.REQUESTED


def test_trading_process_ignores_same_direction_signal_when_position_exists() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    state = trading_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = 100
    state.position.average_price = 1000.0
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert received_events == []


def test_trading_process_ignores_all_signals_when_position_mismatch_halts_trading() -> (
    None
):
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    assert trading_process.risk_manager is not None
    trading_process.risk_manager.activate_kill_switch(
        TradingHaltReason.POSITION_MISMATCH
    )
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))
    event_bus.publish(_create_signal_event(SignalType.EXIT))

    assert received_events == []


def test_trading_process_publishes_exit_order_when_position_exists() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        process_config=_trading_process_config(max_order_quantity=200),
    )
    state = trading_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = 200
    state.position.average_price = 1000.0
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.EXIT))

    assert len(received_events) == 1
    assert isinstance(received_events[0], OrderRequested)
    assert received_events[0].payload.side == OrderSide.SELL
    assert received_events[0].payload.quantity == 200


def test_trading_process_ignores_exit_signal_without_position() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    received_events: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_events.append)
    event_bus.publish(_create_signal_event(SignalType.EXIT))

    assert received_events == []


def test_trading_process_updates_position_from_order_status() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )
    timestamp = datetime(2026, 4, 18, tzinfo=timezone.utc)

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    status_event = EventFactory(source=EventSource.TRADING).create(
        event_type=EventType.ORDER_STATUS_UPDATED,
        timestamp=timestamp,
        symbol="7203",
        payload=OrderStatusPayload(
            order_id=order.order_id,
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1000.0,
            is_exit=False,
        ),
    )
    event_bus.publish(status_event)

    assert state.position is not None
    assert order.status == OrderStatus.FILLED
    assert state.position.quantity == 100
    assert state.position.average_price == 1000.0
    assert order.reflected_filled_quantity == 100
    assert len(state.trade_histories) == 1
    assert state.trade_histories[0].filled_quantity == 100
    assert len(state.orders) == 0


def test_trading_process_publishes_position_updated_after_paper_fill() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    received_positions: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.POSITION_UPDATED, received_positions.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    state = trading_process.get_state("7203")

    assert len(received_positions) == 1
    assert isinstance(received_positions[0], PositionUpdated)
    assert state.position is not None
    assert state.position.quantity == 100
    assert received_positions[0].payload.quantity == 100


def test_trading_process_uses_gateway_without_direct_fill() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    received_statuses: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_STATUS_UPDATED, received_statuses.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert len(received_statuses) == 1
    assert received_statuses[0].payload.status == OrderStatus.FILLED


def test_trading_process_handles_live_gateway_failure_safely(caplog) -> None:
    class FailingApiClient:
        def send_order(
            self,
            token: str,
            order_request: KabuOrderRequest,
        ) -> KabuOrderResult:
            raise KabuApiError("api failed")

    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        order_gateway=LiveOrderGateway(
            api_client=FailingApiClient(),  # type: ignore[arg-type]
            token="token-1",
            allowed_symbols=("7203",),
            safety_validator=_live_validator(),
        ),
    )
    received_statuses: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_STATUS_UPDATED, received_statuses.append)
    with caplog.at_level("ERROR"):
        event_bus.publish(_create_signal_event(SignalType.BUY))

    state = trading_process.get_state("7203")

    assert "order gateway failed" in caplog.text
    assert received_statuses == []
    assert state.position is not None
    assert state.position.quantity == 0
    assert state.orders[0].status == OrderStatus.REQUESTED


def test_live_order_keeps_requested_state_after_send() -> None:
    class RequestedApiClient:
        def send_order(
            self,
            token: str,
            order_request: KabuOrderRequest,
        ) -> KabuOrderResult:
            return KabuOrderResult(
                order_id="api-order-1",
                symbol=order_request.symbol,
                status=OrderStatus.REQUESTED,
                filled_quantity=0,
                remaining_quantity=order_request.quantity,
                avg_price=None,
            )

    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        order_gateway=LiveOrderGateway(
            api_client=RequestedApiClient(),  # type: ignore[arg-type]
            token="token-1",
            allowed_symbols=("7203",),
            safety_validator=_live_validator(),
        ),
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]

    assert order.status == OrderStatus.REQUESTED
    assert order.external_order_id == "api-order-1"
    assert state.position is not None
    assert state.position.quantity == 0


def test_trading_process_updates_order_from_rest_status() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    order.external_order_id = "api-order-1"
    event_bus.publish(
        _create_order_status_event(
            order_id="api-order-1",
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1000.0,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert order.status == OrderStatus.FILLED
    assert state.position is not None
    assert state.position.quantity == 100
    assert state.position.average_price == 1000.0
    assert order.reflected_filled_quantity == 100
    assert len(state.trade_histories) == 1
    assert len(state.orders) == 0


def test_trading_process_restores_open_order_from_api_status() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(
        _create_order_status_event(
            order_id="api-order-1",
            status=OrderStatus.REQUESTED,
            filled_quantity=0,
            remaining_quantity=100,
            avg_price=None,
            side=OrderSide.BUY,
            order_quantity=100,
            is_exit=False,
            source=EventSource.EXTERNAL_DATA,
        )
    )
    state = trading_process.get_state("7203")

    assert len(state.orders) == 1
    assert state.orders[0].order_id == "api-order-1"
    assert state.orders[0].status == OrderStatus.REQUESTED
    assert state.orders[0].is_exit is False


def test_trading_process_restored_exit_sell_reduces_long_position() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )
    state = trading_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = 100
    state.position.average_price = 1000.0

    trading_process.start()
    event_bus.publish(
        _create_order_status_event(
            order_id="api-exit-sell-1",
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1010.0,
            side=OrderSide.SELL,
            order_quantity=100,
            is_exit=True,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position.quantity == 0
    assert len(state.trade_histories) == 1
    assert state.trade_histories[0].is_exit is True


def test_trading_process_restored_exit_buy_reduces_short_position() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )
    state = trading_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = -100
    state.position.average_price = 1000.0

    trading_process.start()
    event_bus.publish(
        _create_order_status_event(
            order_id="api-exit-buy-1",
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=990.0,
            side=OrderSide.BUY,
            order_quantity=100,
            is_exit=True,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position.quantity == 0
    assert len(state.trade_histories) == 1
    assert state.trade_histories[0].is_exit is True


def test_trading_process_restored_new_buy_increases_long_position() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(
        _create_order_status_event(
            order_id="api-entry-buy-1",
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1000.0,
            side=OrderSide.BUY,
            order_quantity=100,
            is_exit=False,
            source=EventSource.EXTERNAL_DATA,
        )
    )
    state = trading_process.get_state("7203")

    assert state.position is not None
    assert state.position.quantity == 100
    assert len(state.trade_histories) == 1
    assert state.trade_histories[0].is_exit is False


def test_trading_process_restored_new_sell_increases_short_position() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(
        _create_order_status_event(
            order_id="api-entry-sell-1",
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1000.0,
            side=OrderSide.SELL,
            order_quantity=100,
            is_exit=False,
            source=EventSource.EXTERNAL_DATA,
        )
    )
    state = trading_process.get_state("7203")

    assert state.position is not None
    assert state.position.quantity == -100
    assert len(state.trade_histories) == 1
    assert state.trade_histories[0].is_exit is False


def test_trading_process_ignores_when_existing_order_is_exit_conflicts() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1000.0,
            is_exit=True,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position is not None
    assert state.position.quantity == 0
    assert len(state.trade_histories) == 0
    assert len(state.orders) == 1


def test_trading_process_keeps_partial_fill_in_open_orders() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=50,
            remaining_quantity=50,
            avg_price=1000.0,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert len(state.orders) == 1
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == 50
    assert order.reflected_filled_quantity == 50
    assert state.position is not None
    assert state.position.quantity == 50
    assert len(state.trade_histories) == 1
    assert state.trade_histories[0].filled_quantity == 50


def test_trading_process_reflects_partial_fill_delta_only() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=40,
            remaining_quantity=60,
            avg_price=1000.0,
            source=EventSource.EXTERNAL_DATA,
        )
    )
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=70,
            remaining_quantity=30,
            avg_price=1000.0,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position is not None
    assert state.position.quantity == 70
    assert order.reflected_filled_quantity == 70
    assert len(state.trade_histories) == 2
    assert state.trade_histories[0].filled_quantity == 40
    assert state.trade_histories[1].filled_quantity == 30


def test_trading_process_removes_canceled_order_from_open_orders() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.CANCELED,
            filled_quantity=0,
            remaining_quantity=100,
            avg_price=None,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert order.status == OrderStatus.CANCELED
    assert len(state.orders) == 0


def test_trading_process_reflects_partial_fill_before_canceled() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.CANCELED,
            filled_quantity=40,
            remaining_quantity=60,
            avg_price=1000.0,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position is not None
    assert state.position.quantity == 40
    assert len(state.trade_histories) == 1
    assert state.trade_histories[0].filled_quantity == 40
    assert len(state.orders) == 0


def test_trading_process_removes_expired_order_from_open_orders() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.EXPIRED,
            filled_quantity=0,
            remaining_quantity=100,
            avg_price=None,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert order.status == OrderStatus.EXPIRED
    assert len(state.orders) == 0


def test_trading_process_reflects_partial_fill_before_expired() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.EXPIRED,
            filled_quantity=40,
            remaining_quantity=60,
            avg_price=1000.0,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position is not None
    assert state.position.quantity == 40
    assert len(state.trade_histories) == 1
    assert state.trade_histories[0].filled_quantity == 40
    assert len(state.orders) == 0


def test_trading_process_calls_order_status_syncer_after_order() -> None:
    sync_calls: list[str] = []
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    trading_process.order_status_syncer = lambda: sync_calls.append("sync") or 0

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert sync_calls == ["sync"]


def test_trading_process_does_not_double_reflect_same_filled_quantity() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    partial_event = _create_order_status_event(
        order_id=order.order_id,
        status=OrderStatus.PARTIALLY_FILLED,
        filled_quantity=40,
        remaining_quantity=60,
        avg_price=1000.0,
        source=EventSource.EXTERNAL_DATA,
    )
    event_bus.publish(partial_event)
    event_bus.publish(partial_event)

    assert state.position is not None
    assert state.position.quantity == 40
    assert len(state.trade_histories) == 1


def test_trading_process_does_not_additional_order_when_order_status_sync_fails() -> (
    None
):
    class RequestedApiClient:
        def send_order(
            self,
            token: str,
            order_request: KabuOrderRequest,
        ) -> KabuOrderResult:
            return KabuOrderResult(
                order_id="api-order-1",
                symbol=order_request.symbol,
                status=OrderStatus.REQUESTED,
                filled_quantity=0,
                remaining_quantity=order_request.quantity,
                avg_price=None,
            )

    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        order_gateway=LiveOrderGateway(
            api_client=RequestedApiClient(),  # type: ignore[arg-type]
            token="token-1",
            allowed_symbols=("7203",),
            safety_validator=_live_validator(),
        ),
    )
    trading_process.order_status_syncer = lambda: (_ for _ in ()).throw(
        KabuApiError("unsupported order status=UNKNOWN_STATUS")
    )
    received_orders: list[BaseEvent[Any]] = []

    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_orders.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))
    event_bus.publish(_create_signal_event(SignalType.SELL))
    state = trading_process.get_state("7203")

    assert len(received_orders) == 1
    assert len(state.orders) == 1
    assert state.orders[0].status == OrderStatus.REQUESTED


def test_trading_process_ignores_when_filled_quantity_decreases() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=70,
            remaining_quantity=30,
            avg_price=1000.0,
            source=EventSource.EXTERNAL_DATA,
        )
    )
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=40,
            remaining_quantity=60,
            avg_price=1000.0,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position is not None
    assert state.position.quantity == 70
    assert len(state.trade_histories) == 1
    assert order.reflected_filled_quantity == 70


def test_trading_process_ignores_when_filled_quantity_exceeds_order_quantity() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            status=OrderStatus.FILLED,
            filled_quantity=120,
            remaining_quantity=0,
            avg_price=1000.0,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position is not None
    assert state.position.quantity == 0
    assert len(state.trade_histories) == 0
    assert order.reflected_filled_quantity == 0


def test_trading_process_updates_average_price_when_new_buy_is_added() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )
    state = trading_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = 100
    state.position.average_price = 1000.0
    order = Order(
        order_id="order-2",
        symbol="7203",
        side=OrderSide.BUY,
        quantity=100,
        order_type="MARKET",
        status=OrderStatus.REQUESTED,
        remaining_quantity=100,
    )
    state.orders.append(order)

    trading_process.start()
    event_bus.publish(
        _create_order_status_event(
            order_id="order-2",
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1100.0,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position.quantity == 200
    assert state.position.average_price == 1050.0
    assert len(state.trade_histories) == 1


def test_trading_process_reduces_position_when_exit_sell_fills() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )
    state = trading_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = 100
    state.position.average_price = 1000.0
    order = Order(
        order_id="exit-order-1",
        symbol="7203",
        side=OrderSide.SELL,
        quantity=100,
        order_type="MARKET",
        is_exit=True,
        status=OrderStatus.REQUESTED,
        remaining_quantity=100,
    )
    state.orders.append(order)

    trading_process.start()
    event_bus.publish(
        _create_order_status_event(
            order_id="exit-order-1",
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1010.0,
            is_exit=True,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position.quantity == 0
    assert state.position.average_price == 0.0
    assert len(state.trade_histories) == 1
    assert state.trade_histories[0].is_exit is True


def test_trading_process_ignores_exit_fill_without_position() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )
    state = trading_process.get_state("7203")
    order = Order(
        order_id="exit-order-1",
        symbol="7203",
        side=OrderSide.SELL,
        quantity=100,
        order_type="MARKET",
        is_exit=True,
        status=OrderStatus.REQUESTED,
        remaining_quantity=100,
    )
    state.orders.append(order)

    trading_process.start()
    event_bus.publish(
        _create_order_status_event(
            order_id="exit-order-1",
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1010.0,
            is_exit=True,
            source=EventSource.EXTERNAL_DATA,
        )
    )

    assert state.position is not None
    assert state.position.quantity == 0
    assert len(state.trade_histories) == 0
    assert len(state.orders) == 1


def test_trading_process_does_not_rollback_terminal_order_status() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    order.external_order_id = "api-order-1"
    event_bus.publish(
        _create_order_status_event(
            order_id="api-order-1",
            status=OrderStatus.FILLED,
            filled_quantity=100,
            remaining_quantity=0,
            avg_price=1000.0,
        )
    )
    event_bus.publish(
        _create_order_status_event(
            order_id="api-order-1",
            status=OrderStatus.NEW,
            filled_quantity=0,
            remaining_quantity=100,
            avg_price=None,
        )
    )

    assert order.status == OrderStatus.FILLED
    assert state.position is not None
    assert state.position.quantity == 100
    assert len(state.orders) == 0


def test_trading_process_ignores_duplicate_filled_event() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=100,
        auto_fill_orders=False,
    )

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    state = trading_process.get_state("7203")
    order = state.orders[0]
    order.external_order_id = "api-order-1"
    filled_event = _create_order_status_event(
        order_id="api-order-1",
        status=OrderStatus.FILLED,
        filled_quantity=100,
        remaining_quantity=0,
        avg_price=1000.0,
    )
    event_bus.publish(filled_event)
    event_bus.publish(filled_event)

    assert order.status == OrderStatus.FILLED
    assert state.position is not None
    assert state.position.quantity == 100
    assert len(state.orders) == 0


def test_trading_process_resyncs_order_status_after_restore() -> None:
    original_process = TradingProcess(
        event_bus=EventBus(),
        order_quantity=100,
        auto_fill_orders=False,
    )
    original_process.start()
    original_process.event_bus.publish(_create_signal_event(SignalType.BUY))
    original_state = original_process.get_state("7203")
    original_state.orders[0].external_order_id = "api-order-1"
    snapshot = original_process.get_snapshot(_timestamp())

    restored_process = TradingProcess(
        event_bus=EventBus(),
        order_quantity=100,
        auto_fill_orders=False,
    )
    restored_process.restore_snapshot(snapshot)
    restored_state = restored_process.get_state("7203")
    restored_order = restored_state.orders[0]
    restored_process.apply_order_status_events(
        (
            _create_order_status_event(
                order_id="api-order-1",
                status=OrderStatus.FILLED,
                filled_quantity=100,
                remaining_quantity=0,
                avg_price=1000.0,
                source=EventSource.EXTERNAL_DATA,
            ),
        )
    )

    assert restored_order.status == OrderStatus.FILLED
    assert restored_state.position is not None
    assert restored_state.position.quantity == 100
    assert len(restored_state.orders) == 0


def test_trading_process_start_stop_start_does_not_duplicate_subscription() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    received_orders: list[BaseEvent[Any]] = []

    trading_process.start()
    trading_process.stop()
    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_orders.append)
    event_bus.publish(_create_signal_event(SignalType.BUY))

    assert len(event_bus._subscribers[EventType.SIGNAL_DETECTED]) == 1
    assert len(event_bus._subscribers[EventType.ORDER_STATUS_UPDATED]) == 1
    assert len(received_orders) == 1


def test_trading_process_updates_average_price_when_long_reverses_to_short() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=200,
        auto_fill_orders=False,
    )
    state = trading_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = 100
    state.position.average_price = 1000.0

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.SELL))
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            filled_quantity=200,
            avg_price=1100.0,
        )
    )

    assert state.position.quantity == -100
    assert state.position.average_price == 1100.0


def test_trading_process_updates_average_price_when_short_reverses_to_long() -> None:
    event_bus = EventBus()
    trading_process = TradingProcess(
        event_bus=event_bus,
        order_quantity=200,
        auto_fill_orders=False,
    )
    state = trading_process.get_state("7203")
    assert state.position is not None
    state.position.quantity = -100
    state.position.average_price = 1000.0

    trading_process.start()
    event_bus.publish(_create_signal_event(SignalType.BUY))
    order = state.orders[0]
    event_bus.publish(
        _create_order_status_event(
            order_id=order.order_id,
            filled_quantity=200,
            avg_price=900.0,
        )
    )

    assert state.position.quantity == 100
    assert state.position.average_price == 900.0


def test_csv_event_flow_publishes_order_requested_end_to_end() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        trend_short_window=1,
        trend_long_window=2,
    )
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    external_data_process = ExternalDataProcess(
        event_bus=event_bus,
        csv_loader=CsvMarketDataLoader(),
    )
    received_orders: list[BaseEvent[Any]] = []

    signal_process.start()
    trading_process.start()
    event_bus.subscribe(EventType.ORDER_REQUESTED, received_orders.append)
    external_data_process.run_csv(
        csv_path=FIXTURE_DIR / "market_data.csv",
        symbol="7203",
    )

    assert len(received_orders) == 1
    assert isinstance(received_orders[0], OrderRequested)
    assert received_orders[0].payload.side == OrderSide.BUY


def test_csv_event_flow_publishes_position_updated_in_paper_mode() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        trend_short_window=1,
        trend_long_window=2,
    )
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    external_data_process = ExternalDataProcess(
        event_bus=event_bus,
        csv_loader=CsvMarketDataLoader(),
    )
    received_positions: list[BaseEvent[Any]] = []

    signal_process.start()
    trading_process.start()
    event_bus.subscribe(EventType.POSITION_UPDATED, received_positions.append)
    external_data_process.run_csv(
        csv_path=FIXTURE_DIR / "market_data.csv",
        symbol="7203",
    )

    assert len(received_positions) == 1
    assert isinstance(received_positions[0], PositionUpdated)
    assert received_positions[0].payload.symbol == "7203"
    assert received_positions[0].payload.quantity == 100


def test_api_event_flow_publishes_position_updated_in_paper_mode() -> None:
    event_bus = EventBus()
    signal_process = SignalProcess(
        event_bus=event_bus,
        trend_short_window=1,
        trend_long_window=2,
    )
    trading_process = TradingProcess(event_bus=event_bus, order_quantity=100)
    external_data_process = ExternalDataProcess(
        event_bus=event_bus,
        push_client=_FakePushClient(),
    )
    received_positions: list[BaseEvent[Any]] = []

    signal_process.start()
    trading_process.start()
    event_bus.subscribe(EventType.POSITION_UPDATED, received_positions.append)
    external_data_process.run(
        data_source_mode=DataSourceMode.API,
        csv_path=None,
        symbol="7203",
    )

    assert len(received_positions) == 1
    assert isinstance(received_positions[0], PositionUpdated)
    assert received_positions[0].payload.quantity == 100


class _FakePushClient:
    def __init__(self) -> None:
        self.on_event = None

    def start(self) -> None:
        if self.on_event is None:
            return
        self.on_event(_create_market_data_event(price=100.0))
        self.on_event(_create_market_data_event(price=101.0))


def _find_strategy_slot(
    signal_process: SignalProcess,
    symbol: str,
) -> SignalStrategySlot:
    for slot in signal_process.slots:
        if slot.symbol == symbol:
            return slot
    raise AssertionError(f"strategy slot was not found: {symbol}")


def _timestamp() -> datetime:
    return datetime(2026, 4, 18, tzinfo=timezone.utc)


def _create_order_status_event(
    order_id: str,
    filled_quantity: int,
    avg_price: float | None,
    status: OrderStatus = OrderStatus.FILLED,
    remaining_quantity: int = 0,
    side: OrderSide | None = None,
    order_quantity: int = 0,
    is_exit: bool = False,
    source: EventSource = EventSource.TRADING,
) -> BaseEvent[Any]:
    timestamp = datetime(2026, 4, 18, tzinfo=timezone.utc)
    return EventFactory(source=source).create(
        event_type=EventType.ORDER_STATUS_UPDATED,
        timestamp=timestamp,
        symbol="7203",
        payload=OrderStatusPayload(
            order_id=order_id,
            status=status,
            filled_quantity=filled_quantity,
            remaining_quantity=remaining_quantity,
            avg_price=avg_price,
            side=side,
            order_quantity=order_quantity,
            is_exit=is_exit,
        ),
    )


def _live_validator() -> OrderSafetyValidator:
    return OrderSafetyValidator(
        trading_mode=TradingMode.LIVE,
        kabu_api_environment=KabuApiEnvironment.LIVE,
        max_order_quantity=100,
        trade_symbols=("7203",),
        enabled_symbols=("7203",),
        state_provider=lambda symbol: OrderSafetyState(
            position=Position(symbol=symbol)
        ),
    )


def _trading_process_config(
    *,
    trading_mode: TradingMode = TradingMode.PAPER,
    live_enabled: bool = False,
    data_source_mode: DataSourceMode = DataSourceMode.CSV,
    kabu_api_environment: KabuApiEnvironment = KabuApiEnvironment.PAPER,
    trade_symbols: tuple[str, ...] = ("7203",),
    max_order_quantity: int = 100,
    order_type: str = "MARKET",
    trading_start_time: str = "00:00",
    trading_end_time: str = "23:59",
) -> TradingProcessConfig:
    return TradingProcessConfig(
        trading_mode=trading_mode,
        live_enabled=live_enabled,
        data_source_mode=data_source_mode,
        kabu_api_environment=kabu_api_environment,
        trade_symbols=trade_symbols,
        max_order_quantity=max_order_quantity,
        order_type=order_type,
        trading_start_time=trading_start_time,
        trading_end_time=trading_end_time,
    )

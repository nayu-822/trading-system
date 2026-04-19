from datetime import datetime, timezone

from domain.enums import SignalType, StrategyType
from domain.snapshots import BaseSnapshot, SignalSnapshot, TradingSnapshot


def test_signal_snapshot_has_common_metadata() -> None:
    snapshot = SignalSnapshot(
        version=1,
        created_at=datetime(2026, 4, 18, tzinfo=timezone.utc),
        sequence_no=10,
        symbol="7203",
        last_signal_type=SignalType.BUY,
        strategy_type=StrategyType.TREND,
        updated_at=datetime(2026, 4, 18, tzinfo=timezone.utc),
    )

    assert isinstance(snapshot, BaseSnapshot)
    assert snapshot.version == 1
    assert snapshot.sequence_no == 10
    assert snapshot.updated_at == datetime(2026, 4, 18, tzinfo=timezone.utc)


def test_trading_snapshot_has_common_metadata() -> None:
    snapshot = TradingSnapshot(
        version=1,
        created_at=datetime(2026, 4, 18, tzinfo=timezone.utc),
        sequence_no=20,
        symbol="7203",
        position_quantity=100,
        avg_price=2500.0,
        current_lot=100,
        updated_at=datetime(2026, 4, 18, tzinfo=timezone.utc),
    )

    assert isinstance(snapshot, BaseSnapshot)
    assert snapshot.version == 1
    assert snapshot.sequence_no == 20
    assert snapshot.updated_at == datetime(2026, 4, 18, tzinfo=timezone.utc)
    assert snapshot.symbol == "7203"
    assert snapshot.current_lot == 100

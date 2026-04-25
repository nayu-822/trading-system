import pytest

from domain.models import Position
from infrastructure.repositories.position_repository import PositionRepositoryError
from trading.position_reconciliation_service import (
    PositionReconciliationError,
    PositionReconciliationService,
)


def test_position_reconciliation_service_matches_same_position() -> None:
    repository = _FakePositionRepository(
        position=Position(symbol="7203", quantity=100, average_price=1000.0)
    )
    service = PositionReconciliationService(position_repository=repository)

    result = service.reconcile(
        symbol="7203",
        internal_position=Position(symbol="7203", quantity=100, average_price=1000.0),
    )

    assert result.matched is True
    assert result.mismatch_reason is None
    assert repository.calls == [("7203", True)]


def test_position_reconciliation_service_raises_on_quantity_mismatch() -> None:
    repository = _FakePositionRepository(
        position=Position(symbol="7203", quantity=200, average_price=1000.0)
    )
    service = PositionReconciliationService(position_repository=repository)

    with pytest.raises(PositionReconciliationError):
        service.reconcile(
            symbol="7203",
            internal_position=Position(
                symbol="7203",
                quantity=100,
                average_price=1000.0,
            ),
        )
    assert repository.calls == [("7203", True)]


def test_position_reconciliation_service_raises_on_side_mismatch() -> None:
    service = PositionReconciliationService(
        position_repository=_FakePositionRepository(
            position=Position(symbol="7203", quantity=-100, average_price=1000.0)
        )
    )

    with pytest.raises(PositionReconciliationError):
        service.reconcile(
            symbol="7203",
            internal_position=Position(
                symbol="7203",
                quantity=100,
                average_price=1000.0,
            ),
        )


def test_position_reconciliation_service_raises_when_api_has_no_position_but_internal_has_position() -> None:
    service = PositionReconciliationService(
        position_repository=_FakePositionRepository(
            position=Position(symbol="7203", quantity=0, average_price=0.0)
        )
    )

    with pytest.raises(PositionReconciliationError):
        service.reconcile(
            symbol="7203",
            internal_position=Position(
                symbol="7203",
                quantity=100,
                average_price=1000.0,
            ),
        )


def test_position_reconciliation_service_raises_when_api_has_position_but_internal_is_empty() -> None:
    service = PositionReconciliationService(
        position_repository=_FakePositionRepository(
            position=Position(symbol="7203", quantity=100, average_price=1000.0)
        )
    )

    with pytest.raises(PositionReconciliationError):
        service.reconcile(symbol="7203", internal_position=None)


def test_position_reconciliation_service_allows_average_price_within_tolerance() -> None:
    service = PositionReconciliationService(
        position_repository=_FakePositionRepository(
            position=Position(symbol="7203", quantity=100, average_price=1000.005)
        ),
        average_price_tolerance=0.01,
    )

    result = service.reconcile(
        symbol="7203",
        internal_position=Position(symbol="7203", quantity=100, average_price=1000.0),
    )

    assert result.matched is True


def test_position_reconciliation_service_raises_when_average_price_exceeds_tolerance() -> None:
    service = PositionReconciliationService(
        position_repository=_FakePositionRepository(
            position=Position(symbol="7203", quantity=100, average_price=1000.02)
        ),
        average_price_tolerance=0.01,
    )

    with pytest.raises(PositionReconciliationError):
        service.reconcile(
            symbol="7203",
            internal_position=Position(symbol="7203", quantity=100, average_price=1000.0),
        )


def test_position_reconciliation_service_raises_when_api_fetch_fails() -> None:
    service = PositionReconciliationService(
        position_repository=_FailingPositionRepository(),
    )

    with pytest.raises(PositionReconciliationError):
        service.reconcile(
            symbol="7203",
            internal_position=Position(symbol="7203", quantity=100, average_price=1000.0),
        )


def test_position_reconciliation_service_uses_latest_api_position_when_cache_is_stale() -> None:
    repository = _StaleCachePositionRepository()
    service = PositionReconciliationService(position_repository=repository)

    result = service.reconcile(
        symbol="7203",
        internal_position=Position(symbol="7203", quantity=100, average_price=1000.0),
    )

    assert result.matched is True
    assert repository.calls == [("7203", True)]


class _FakePositionRepository:
    def __init__(self, position: Position) -> None:
        self.position = position
        self.calls: list[tuple[str, bool]] = []

    def get_position(self, symbol: str, force_refresh: bool = False) -> Position:
        self.calls.append((symbol, force_refresh))
        return self.position


class _FailingPositionRepository:
    def get_position(self, symbol: str, force_refresh: bool = False) -> Position:
        raise PositionRepositoryError(f"failed: {symbol}")


class _StaleCachePositionRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def get_position(self, symbol: str, force_refresh: bool = False) -> Position:
        self.calls.append((symbol, force_refresh))
        if force_refresh:
            return Position(symbol=symbol, quantity=100, average_price=1000.0)
        return Position(symbol=symbol, quantity=0, average_price=0.0)

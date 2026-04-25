import logging

import pytest

from data_source.kabu_api_client import KabuApiError
from domain.models import Position
from infrastructure.repositories.position_repository import (
    PositionRepository,
    PositionRepositoryError,
)


def test_position_repository_returns_position_from_api() -> None:
    class FakeApiClient:
        def get_positions(self, token: str, symbol: str | None = None) -> tuple[Position, ...]:
            assert token == "token-1"
            assert symbol == "7203"
            return (Position(symbol="7203", quantity=100, average_price=1000.0),)

    repository = PositionRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    position = repository.get_position("7203")

    assert position.symbol == "7203"
    assert position.quantity == 100
    assert position.average_price == 1000.0


def test_position_repository_raises_when_api_fails() -> None:
    class FakeApiClient:
        def get_positions(self, token: str, symbol: str | None = None) -> tuple[Position, ...]:
            raise KabuApiError("api failed")

    repository = PositionRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    with pytest.raises(PositionRepositoryError):
        repository.get_position("7203")


def test_position_repository_uses_cache_within_ttl() -> None:
    class FakeApiClient:
        def __init__(self) -> None:
            self.call_count = 0

        def get_positions(self, token: str, symbol: str | None = None) -> tuple[Position, ...]:
            self.call_count += 1
            return (Position(symbol="7203", quantity=100, average_price=1000.0),)

    current_time = 100.0

    def time_provider() -> float:
        return current_time

    api_client = FakeApiClient()
    repository = PositionRepository(
        api_client=api_client,  # type: ignore[arg-type]
        token="token-1",
        cache_ttl_sec=1.0,
        time_provider=time_provider,
    )

    first = repository.get_position("7203", force_refresh=False)
    second = repository.get_position("7203", force_refresh=False)

    assert api_client.call_count == 1
    assert first == second


def test_position_repository_force_refresh_bypasses_cache() -> None:
    class FakeApiClient:
        def __init__(self) -> None:
            self.call_count = 0

        def get_positions(self, token: str, symbol: str | None = None) -> tuple[Position, ...]:
            self.call_count += 1
            quantity = 100 if self.call_count == 1 else 200
            return (Position(symbol="7203", quantity=quantity, average_price=1000.0),)

    current_time = 100.0

    def time_provider() -> float:
        return current_time

    api_client = FakeApiClient()
    repository = PositionRepository(
        api_client=api_client,  # type: ignore[arg-type]
        token="token-1",
        cache_ttl_sec=1.0,
        time_provider=time_provider,
    )

    cached = repository.get_position("7203", force_refresh=False)
    refreshed = repository.get_position("7203", force_refresh=True)

    assert api_client.call_count == 2
    assert cached.quantity == 100
    assert refreshed.quantity == 200


def test_position_repository_updates_cache_after_force_refresh() -> None:
    class FakeApiClient:
        def __init__(self) -> None:
            self.call_count = 0

        def get_positions(self, token: str, symbol: str | None = None) -> tuple[Position, ...]:
            self.call_count += 1
            quantity = 100 if self.call_count == 1 else 200
            return (Position(symbol="7203", quantity=quantity, average_price=1000.0),)

    current_time = 100.0

    def time_provider() -> float:
        return current_time

    api_client = FakeApiClient()
    repository = PositionRepository(
        api_client=api_client,  # type: ignore[arg-type]
        token="token-1",
        cache_ttl_sec=1.0,
        time_provider=time_provider,
    )

    repository.get_position("7203")
    refreshed = repository.get_position("7203", force_refresh=True)
    cached_after_refresh = repository.get_position("7203")

    assert api_client.call_count == 2
    assert refreshed.quantity == 200
    assert cached_after_refresh.quantity == 200


def test_position_repository_refreshes_cache_after_ttl() -> None:
    class FakeApiClient:
        def __init__(self) -> None:
            self.call_count = 0

        def get_positions(self, token: str, symbol: str | None = None) -> tuple[Position, ...]:
            self.call_count += 1
            return (Position(symbol="7203", quantity=100, average_price=1000.0),)

    current_time = {"value": 100.0}

    def time_provider() -> float:
        return current_time["value"]

    api_client = FakeApiClient()
    repository = PositionRepository(
        api_client=api_client,  # type: ignore[arg-type]
        token="token-1",
        cache_ttl_sec=1.0,
        time_provider=time_provider,
    )

    repository.get_position("7203")
    current_time["value"] = 101.1
    repository.get_position("7203")

    assert api_client.call_count == 2


def test_position_repository_raises_when_symbol_mismatch() -> None:
    class FakeApiClient:
        def get_positions(self, token: str, symbol: str | None = None) -> tuple[Position, ...]:
            return (
                Position(symbol="6758", quantity=100, average_price=1000.0),
            )

    repository = PositionRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    with pytest.raises(PositionRepositoryError):
        repository.get_position("7203")


def test_position_repository_returns_zero_position_when_symbol_has_no_position() -> None:
    class FakeApiClient:
        def get_positions(self, token: str, symbol: str | None = None) -> tuple[Position, ...]:
            return ()

    repository = PositionRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    position = repository.get_position("7203")

    assert position.symbol == "7203"
    assert position.quantity == 0
    assert position.average_price == 0.0


def test_position_repository_raises_when_position_parse_fails() -> None:
    class FakeApiClient:
        def get_positions(self, token: str, symbol: str | None = None) -> tuple[Position, ...]:
            raise ValueError("parse failed")

    repository = PositionRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    with pytest.raises(PositionRepositoryError):
        repository.get_position("7203")


def test_position_repository_logs_position_from_api(caplog) -> None:
    class FakeApiClient:
        def get_positions(self, token: str, symbol: str | None = None) -> tuple[Position, ...]:
            return (Position(symbol="7203", quantity=-100, average_price=1000.0),)

    repository = PositionRepository(
        api_client=FakeApiClient(),  # type: ignore[arg-type]
        token="token-1",
    )

    with caplog.at_level(logging.INFO):
        repository.get_position("7203")

    assert "position fetched symbol=7203 side=SELL quantity=100 source=api" in caplog.text

from pathlib import Path

import main as app_main
from domain.enums import OrderSide, OrderStatus
from domain.models import Order, Position
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError


class FakeLogger:
    """main の例外ログ呼び出しを検証するための最小ロガー。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def exception(self, message: str) -> None:
        """例外ログメッセージを記録する。

        Args:
            message: ログメッセージ。

        Returns:
            なし。
        """

        self.messages.append(message)


def test_main_uses_config_log_level_when_config_can_be_loaded(
    monkeypatch,
) -> None:
    config = load_config(Path("config"))
    log_levels: list[str] = []
    fake_logger = FakeLogger()

    def fake_setup_logger(level: str, process_name: str) -> FakeLogger:
        log_levels.append(level)
        return fake_logger

    def fail_initialize_application(*_, **__) -> None:
        raise ConfigValidationError("invalid config")

    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", fake_setup_logger)
    monkeypatch.setattr(app_main, "initialize_application", fail_initialize_application)

    assert app_main.main() == 1
    assert log_levels == [config.app.log_level]
    assert fake_logger.messages == ["application initialization failed"]


def test_main_uses_default_log_level_when_config_cannot_be_loaded(
    monkeypatch,
) -> None:
    log_levels: list[str] = []
    fake_logger = FakeLogger()

    def raise_config_load_error(_) -> None:
        raise ConfigLoadError("missing config")

    def fake_setup_logger(level: str, process_name: str) -> FakeLogger:
        log_levels.append(level)
        return fake_logger

    monkeypatch.setattr(app_main, "load_config", raise_config_load_error)
    monkeypatch.setattr(app_main, "setup_logger", fake_setup_logger)

    assert app_main.main() == 1
    assert log_levels == [app_main.DEFAULT_LOG_LEVEL]
    assert fake_logger.messages == ["application initialization failed"]


def test_build_order_safety_state_provider_uses_synced_open_orders() -> None:
    class FakePositionRepository:
        def get_position(self, symbol: str) -> Position:
            return Position(symbol=symbol)

    class FakeOrderStatusRepository:
        def get_open_orders(self, symbol: str):
            return [
                Order(
                    order_id="api-order-1",
                    external_order_id="api-order-1",
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=100,
                    order_type="MARKET",
                    status=OrderStatus.REQUESTED,
                    remaining_quantity=100,
                )
            ]

    provider = app_main._build_order_safety_state_provider(
        position_repository=FakePositionRepository(),  # type: ignore[arg-type]
        order_status_repository=FakeOrderStatusRepository(),  # type: ignore[arg-type]
    )

    state = provider("7203")

    assert state.position.symbol == "7203"
    assert len(state.open_orders) == 1
    assert state.open_orders[0].order_id == "api-order-1"

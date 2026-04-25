import io
from datetime import datetime, timezone
from pathlib import Path

import main as app_main
from domain.enums import OrderSide, OrderStatus, TradingHaltReason
from domain.models import Order, Position, TradingHaltState
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError


class FakeLogger:
    """main の例外ログ呼び出しを検証するための最小ロガー。"""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.infos: list[str] = []

    def exception(self, message: str) -> None:
        """例外ログメッセージを記録する。

        Args:
            message: ログメッセージ。

        Returns:
            なし。
        """

        self.messages.append(message)

    def info(self, message: str, *args) -> None:
        self.infos.append(message % args if args else message)


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


def test_build_order_safety_state_provider_rejects_when_trading_is_halted() -> None:
    class FakePositionRepository:
        def get_position(self, symbol: str) -> Position:
            raise AssertionError("get_position should not be called")

    class FakeOrderStatusRepository:
        def get_open_orders(self, symbol: str):
            raise AssertionError("get_open_orders should not be called")

    config = load_config(Path("config"))
    risk_manager = app_main._build_risk_manager(config)
    risk_manager.activate_kill_switch(app_main.TradingHaltReason.POSITION_MISMATCH)
    provider = app_main._build_order_safety_state_provider(
        position_repository=FakePositionRepository(),  # type: ignore[arg-type]
        order_status_repository=FakeOrderStatusRepository(),  # type: ignore[arg-type]
        risk_manager=risk_manager,
    )

    try:
        provider("7203")
    except ValueError as error:
        assert "position_mismatch" in str(error)
    else:
        raise AssertionError("ValueError was not raised")


def test_main_halt_status_outputs_current_state(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(
            is_halted=True,
            reason=TradingHaltReason.POSITION_MISMATCH,
            message="position mismatch detected",
            halted_at=_timestamp(),
            requires_manual_resume=True,
        )
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(app_main, "build_application_runtime", lambda config: fake_runtime)
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["halt-status"])

    assert exit_code == 0
    assert "is_halted: True" in output.getvalue()
    assert "reason: position_mismatch" in output.getvalue()


def test_main_halt_sets_manual_halt_and_saves_snapshot(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(halt_state=TradingHaltState())
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(app_main, "build_application_runtime", lambda config: fake_runtime)
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        ["halt", "--reason", "MANUAL", "--message", "manual maintenance"]
    )

    assert exit_code == 0
    assert fake_runtime.halt_calls == [(TradingHaltReason.MANUAL, "manual maintenance")]
    assert fake_runtime.save_snapshot_calls == 1


def test_main_resume_success_saves_snapshot(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(
            is_halted=False,
            reason=TradingHaltReason.MANUAL,
            message="manual resume completed",
            resolved_at=_timestamp(),
        ),
        resume_result=True,
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(app_main, "build_application_runtime", lambda config: fake_runtime)
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["resume"])

    assert exit_code == 0
    assert fake_runtime.resume_calls == 1
    assert fake_runtime.save_snapshot_calls == 1


def test_main_resume_failure_keeps_halt_state(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(
            is_halted=True,
            reason=TradingHaltReason.ORDER_SYNC_FAILED,
            message="open orders are not synchronized with api",
            halted_at=_timestamp(),
            requires_manual_resume=True,
        ),
        resume_result=False,
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(app_main, "build_application_runtime", lambda config: fake_runtime)
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["resume"])

    assert exit_code == 1
    assert fake_runtime.resume_calls == 1
    assert "order_sync_failed" in output.getvalue()


def test_main_preflight_check_does_not_change_state(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(
            is_halted=True,
            reason=TradingHaltReason.MANUAL,
            message="manual maintenance",
            halted_at=_timestamp(),
            requires_manual_resume=True,
        ),
        preflight_result={
            "ok": False,
            "is_halted": True,
            "reason": "manual",
            "message": "manual maintenance",
            "checks": [{"name": "order_status_sync", "ok": False, "message": "ng"}],
            "errors": ["ng"],
        },
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(app_main, "build_application_runtime", lambda config: fake_runtime)
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["preflight-check"])

    assert exit_code == 1
    assert fake_runtime.resume_calls == 0
    assert fake_runtime.halt_calls == []
    assert fake_runtime.save_snapshot_calls == 0


def test_main_halt_status_outputs_json(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(
            is_halted=True,
            reason=TradingHaltReason.MANUAL,
            message="manual maintenance",
            halted_at=_timestamp(),
            requires_manual_resume=True,
        )
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(app_main, "build_application_runtime", lambda config: fake_runtime)
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["halt-status", "--json"])

    assert exit_code == 0
    assert '"is_halted": true' in output.getvalue()
    assert '"reason": "manual"' in output.getvalue()


class _FakeRuntime:
    def __init__(
        self,
        halt_state: TradingHaltState,
        resume_result: bool = False,
        preflight_result: dict | None = None,
    ) -> None:
        self.halt_state = halt_state
        self.resume_result = resume_result
        self.preflight_result = preflight_result or {
            "ok": True,
            "is_halted": halt_state.is_halted,
            "reason": halt_state.reason.value if halt_state.reason is not None else "",
            "message": halt_state.message,
            "checks": [],
            "errors": [],
        }
        self.halt_calls: list[tuple[TradingHaltReason, str]] = []
        self.resume_calls = 0
        self.save_snapshot_calls = 0
        self.restore_calls = 0

    def restore_snapshot_state(self) -> bool:
        self.restore_calls += 1
        return True

    def get_trading_halt_state(self) -> TradingHaltState:
        return self.halt_state

    def halt_trading_manually(
        self,
        reason: TradingHaltReason,
        message: str,
    ) -> TradingHaltState:
        self.halt_calls.append((reason, message))
        self.halt_state = TradingHaltState(
            is_halted=True,
            reason=reason,
            message=message,
            halted_at=_timestamp(),
            requires_manual_resume=True,
        )
        return self.halt_state

    def save_snapshot_now(self) -> None:
        self.save_snapshot_calls += 1

    def resume_trading(self) -> bool:
        self.resume_calls += 1
        return self.resume_result

    def run_preflight_check(self) -> dict:
        return self.preflight_result


def _timestamp() -> datetime:
    return datetime(2026, 4, 25, tzinfo=timezone.utc)

import io
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import main as app_main
from domain.enums import OrderSide, OrderStatus, TradingHaltReason
from domain.models import Order, Position, TradingHaltState
from infrastructure.config_loader import ConfigLoadError, load_config
from infrastructure.config_validator import ConfigValidationError
from infrastructure.repositories.operational_snapshot_store import (
    OperationalSnapshotStore,
)


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


def test_build_api_order_dry_run_resources_enables_api_paper_orders(monkeypatch) -> None:
    config = load_config(Path("config"))
    config = replace(
        config,
        app=replace(
            config.app,
            trading_mode=app_main.TradingMode.LIVE,
            kabu_api=replace(
                config.app.kabu_api,
                environment=app_main.KabuApiEnvironment.PAPER,
            ),
        ),
    )
    monkeypatch.setattr(app_main.KabuApiClient, "get_token", lambda self: "token-1")

    resources = app_main._build_api_order_dry_run_resources(
        config=config,
        logger=FakeLogger(),  # type: ignore[arg-type]
        trading_process=app_main.TradingProcess(event_bus=app_main.EventBus()),
    )

    assert resources.order_safety_validator.allow_api_paper_orders is True


def test_main_halt_status_outputs_current_state(monkeypatch) -> None:
    config = replace(
        load_config(Path("config")),
        app=replace(
            load_config(Path("config")).app,
            snapshot_dir="tests/.tmp_main_cli/halt_status",
        ),
    )
    fake_logger = FakeLogger()
    _save_halt_state(
        config=config,
        halt_state=TradingHaltState(
            is_halted=True,
            reason=TradingHaltReason.POSITION_MISMATCH,
            message="position mismatch detected",
            halted_at=_timestamp(),
            requires_manual_resume=True,
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config: (_ for _ in ()).throw(AssertionError("runtime should not be built")),
    )
    monkeypatch.setattr(
        app_main.KabuApiClient,
        "get_token",
        lambda self: (_ for _ in ()).throw(AssertionError("token should not be fetched")),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["halt-status"])

    assert exit_code == 0
    assert "is_halted: True" in output.getvalue()
    assert "reason: position_mismatch" in output.getvalue()


def test_main_halt_sets_manual_halt_and_saves_snapshot(monkeypatch) -> None:
    config = replace(
        load_config(Path("config")),
        app=replace(
            load_config(Path("config")).app,
            snapshot_dir="tests/.tmp_main_cli/halt_manual",
        ),
    )
    fake_logger = FakeLogger()
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config: (_ for _ in ()).throw(AssertionError("runtime should not be built")),
    )
    monkeypatch.setattr(
        app_main.KabuApiClient,
        "get_token",
        lambda self: (_ for _ in ()).throw(AssertionError("token should not be fetched")),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        ["halt", "--reason", "MANUAL", "--message", "manual maintenance"]
    )

    assert exit_code == 0
    halt_state = _load_halt_state(config)
    assert halt_state.is_halted is True
    assert halt_state.reason == TradingHaltReason.MANUAL
    assert halt_state.message == "manual maintenance"
    assert halt_state.requires_manual_resume is True


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
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
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
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
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
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["preflight-check"])

    assert exit_code == 1
    assert fake_runtime.resume_calls == 0
    assert fake_runtime.halt_calls == []
    assert fake_runtime.save_snapshot_calls == 0


def test_main_halt_status_outputs_json(monkeypatch) -> None:
    config = replace(
        load_config(Path("config")),
        app=replace(
            load_config(Path("config")).app,
            snapshot_dir="tests/.tmp_main_cli/halt_status_json",
        ),
    )
    fake_logger = FakeLogger()
    _save_halt_state(
        config=config,
        halt_state=TradingHaltState(
            is_halted=True,
            reason=TradingHaltReason.MANUAL,
            message="manual maintenance",
            halted_at=_timestamp(),
            requires_manual_resume=True,
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config: (_ for _ in ()).throw(AssertionError("runtime should not be built")),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["halt-status", "--json"])

    assert exit_code == 0
    assert '"is_halted": true' in output.getvalue()
    assert '"reason": "manual"' in output.getvalue()


def test_main_resume_returns_error_when_runtime_initialization_fails(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: (_ for _ in ()).throw(
            RuntimeError("api init failed")
        ),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["resume"])

    assert exit_code == 1
    assert "api init failed" in output.getvalue()


def test_main_preflight_check_returns_error_when_runtime_initialization_fails(
    monkeypatch,
) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: (_ for _ in ()).throw(
            RuntimeError("api init failed")
        ),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["preflight-check"])

    assert exit_code == 1
    assert "api init failed" in output.getvalue()


def test_main_preflight_check_outputs_json_when_runtime_initialization_fails(
    monkeypatch,
) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: (_ for _ in ()).throw(
            RuntimeError("api init failed")
        ),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["preflight-check", "--json"])

    assert exit_code == 1
    assert '"ok": false' in output.getvalue()
    assert '"runtime initialization failed: api init failed"' in output.getvalue()


def test_main_api_order_dry_run_outputs_json(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(),
        api_order_dry_run_result={
            "ok": True,
            "command": "api-order-dry-run",
            "executed_at": "2026-04-26T10:00:00+09:00",
            "git_commit": "abc1234",
            "config_summary": {
                "trading_mode": config.app.trading_mode.value,
                "data_source_mode": config.app.data_source_mode.value,
                "kabu_api_environment": config.app.kabu_api.environment.value,
                "token_env_name": config.app.kabu_api.token_env_name,
                "trade_symbols": list(config.app.trade_symbols),
                "max_order_quantity": config.app.max_order_quantity,
            },
            "record_hint": "docs/paper_api_test_record_template.md に結果を記録してください",
            "symbol": "1321",
            "side": "BUY",
            "quantity": 1,
            "order_id": "api-order-1",
            "order_status": "REQUESTED",
            "filled_quantity": 0,
            "remaining_quantity": 1,
            "position_quantity": 0,
            "position_side": "NONE",
            "reconciliation_result": "OK",
            "position": {"symbol": "1321", "quantity": 0, "average_price": 0.0},
            "trading_mode": config.app.trading_mode.value,
            "kabu_api_environment": config.app.kabu_api.environment.value,
            "base_url": config.app.kabu_api.base_url,
            "is_halted": False,
            "halt_reason": "",
            "next_action": ["時間を置いて注文状態同期を確認してください"],
            "log_hint": ["command=api-order-dry-run で検索してください"],
            "checks": [],
            "errors": [],
        },
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        [
            "api-order-dry-run",
            "--symbol",
            "1321",
            "--side",
            "BUY",
            "--quantity",
            "1",
            "--json",
        ]
    )

    assert exit_code == 0
    assert fake_runtime.api_order_dry_run_calls == [("1321", "BUY", 1, None)]
    assert '"command": "api-order-dry-run"' in output.getvalue()
    assert '"executed_at": "2026-04-26T10:00:00+09:00"' in output.getvalue()
    assert '"git_commit": "abc1234"' in output.getvalue()
    assert '"config_summary": {' in output.getvalue()
    assert '"record_hint": "docs/paper_api_test_record_template.md に結果を記録してください"' in output.getvalue()
    assert '"order_id": "api-order-1"' in output.getvalue()
    assert '"next_action": [' in output.getvalue()
    assert '"時間を置いて注文状態同期を確認してください"' in output.getvalue()
    assert '"log_hint": [' in output.getvalue()
    assert '"command=api-order-dry-run で検索してください"' in output.getvalue()


def test_main_api_order_precheck_outputs_json(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(),
        api_order_precheck_result={
            "ok": True,
            "command": "api-order-precheck",
            "executed_at": "2026-04-26T10:00:00+09:00",
            "git_commit": "abc1234",
            "config_summary": {
                "trading_mode": config.app.trading_mode.value,
                "data_source_mode": config.app.data_source_mode.value,
                "kabu_api_environment": config.app.kabu_api.environment.value,
                "token_env_name": config.app.kabu_api.token_env_name,
                "trade_symbols": list(config.app.trade_symbols),
                "max_order_quantity": config.app.max_order_quantity,
            },
            "record_hint": "docs/paper_api_test_record_template.md に結果を記録してください",
            "trading_mode": config.app.trading_mode.value,
            "kabu_api_environment": config.app.kabu_api.environment.value,
            "base_url": config.app.kabu_api.base_url,
            "symbol": "1321",
            "side": "BUY",
            "quantity": 1,
            "is_halted": False,
            "reason": "",
            "message": "ok",
            "checks": [
                {"name": "environment_is_paper", "ok": True, "message": "ok"},
                {"name": "token_env_name_is_paper", "ok": True, "message": "ok"},
                {"name": "token_env_exists", "ok": True, "message": "ok"},
            ],
            "errors": [],
            "next_action": ["api-order-dry-run を実行できます"],
        },
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        ["api-order-precheck", "--symbol", "1321", "--quantity", "1", "--json"]
    )

    assert exit_code == 0
    assert fake_runtime.api_order_precheck_calls == [("1321", "BUY", 1)]
    assert '"command": "api-order-precheck"' in output.getvalue()
    assert '"executed_at": "2026-04-26T10:00:00+09:00"' in output.getvalue()
    assert '"git_commit": "abc1234"' in output.getvalue()
    assert '"config_summary": {' in output.getvalue()
    assert '"record_hint": "docs/paper_api_test_record_template.md に結果を記録してください"' in output.getvalue()
    assert '"token_env_name_is_paper"' in output.getvalue()
    assert '"next_action": ["api-order-dry-run を実行できます"]' in output.getvalue()


def test_main_api_order_precheck_outputs_next_action_on_failure(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(),
        api_order_precheck_result={
            "ok": False,
            "command": "api-order-precheck",
            "trading_mode": config.app.trading_mode.value,
            "kabu_api_environment": "live",
            "base_url": "http://localhost:18080",
            "symbol": "1321",
            "side": "BUY",
            "quantity": 1,
            "is_halted": False,
            "reason": "",
            "message": "kabu_api_environment must be paper",
            "checks": [
                {
                    "name": "environment_is_paper",
                    "ok": False,
                    "message": "kabu_api_environment must be paper",
                }
            ],
            "errors": ["kabu_api_environment must be paper"],
            "next_action": [
                "config/app.yaml の kabu_api_environment を paper にしてください"
            ],
        },
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["api-order-precheck", "--symbol", "1321", "--quantity", "1"])

    assert exit_code == 1
    assert "next_action:" in output.getvalue()
    assert "config/app.yaml の kabu_api_environment を paper にしてください" in output.getvalue()


def test_main_api_order_precheck_returns_formatted_result_when_api_password_is_missing(
    monkeypatch,
) -> None:
    base_config = load_config(Path("config"))
    config = replace(
        base_config,
        app=replace(
            base_config.app,
            data_source_mode=app_main.DataSourceMode.API,
            trading_mode=app_main.TradingMode.LIVE,
            live_enabled=True,
            kabu_api=replace(
                base_config.app.kabu_api,
                environment=app_main.KabuApiEnvironment.PAPER,
                token_env_name="KABU_API_PASSWORD_PAPER",
                base_url="http://localhost:18081/kabusapi",
            ),
        ),
    )
    fake_logger = FakeLogger()
    output = io.StringIO()
    monkeypatch.delenv("KABU_API_PASSWORD_PAPER", raising=False)
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: (_ for _ in ()).throw(
            RuntimeError("api init failed")
        ),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["api-order-precheck", "--symbol", "1321", "--quantity", "1"])

    assert exit_code == 1
    assert "ok: False" in output.getvalue()
    assert "token_env_exists: NG" in output.getvalue()
    assert "環境変数 KABU_API_PASSWORD_PAPER が設定されていません" in output.getvalue()
    assert (
        'PowerShellで $env:KABU_API_PASSWORD_PAPER="検証用APIパスワード" を設定してください'
        in output.getvalue()
    )


def test_main_api_order_precheck_outputs_json_when_api_password_is_missing(
    monkeypatch,
) -> None:
    base_config = load_config(Path("config"))
    config = replace(
        base_config,
        app=replace(
            base_config.app,
            data_source_mode=app_main.DataSourceMode.API,
            trading_mode=app_main.TradingMode.LIVE,
            live_enabled=True,
            kabu_api=replace(
                base_config.app.kabu_api,
                environment=app_main.KabuApiEnvironment.PAPER,
                token_env_name="KABU_API_PASSWORD_PAPER",
                base_url="http://localhost:18081/kabusapi",
            ),
        ),
    )
    fake_logger = FakeLogger()
    output = io.StringIO()
    monkeypatch.delenv("KABU_API_PASSWORD_PAPER", raising=False)
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: (_ for _ in ()).throw(
            RuntimeError("api init failed")
        ),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        ["api-order-precheck", "--symbol", "1321", "--quantity", "1", "--json"]
    )

    assert exit_code == 1
    assert '"ok": false' in output.getvalue()
    assert '"token_env_exists"' in output.getvalue()
    assert '"errors": [' in output.getvalue()
    assert '"next_action": [' in output.getvalue()


def test_main_api_order_dry_run_returns_exit_code_one_on_api_error(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(),
        api_order_dry_run_result={
            "ok": False,
            "command": "api-order-dry-run",
            "symbol": "1570",
            "side": "BUY",
            "quantity": 1,
            "order_id": "",
            "order_status": "",
            "filled_quantity": 0,
            "remaining_quantity": 1,
            "position_quantity": 0,
            "position_side": "NONE",
            "reconciliation_result": "UNKNOWN",
            "position": {"symbol": "1570", "quantity": 0, "average_price": 0.0},
            "trading_mode": config.app.trading_mode.value,
            "kabu_api_environment": config.app.kabu_api.environment.value,
            "base_url": config.app.kabu_api.base_url,
            "is_halted": False,
            "halt_reason": "",
            "next_action": ["kabuステーションが検証モードで起動しているか確認してください"],
            "log_hint": ["command=api-order-dry-run で検索してください"],
            "checks": [],
            "errors": ["api failed"],
        },
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        [
            "api-order-dry-run",
            "--symbol",
            "1570",
            "--side",
            "BUY",
            "--quantity",
            "1",
        ]
    )

    assert exit_code == 1
    assert "api failed" in output.getvalue()
    assert "next_action:" in output.getvalue()
    assert "kabuステーションが検証モードで起動しているか確認してください" in output.getvalue()


def test_main_api_order_dry_run_success_output_includes_next_action(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(),
        api_order_dry_run_result={
            "ok": True,
            "command": "api-order-dry-run",
            "symbol": "1306",
            "side": "BUY",
            "quantity": 10,
            "order_id": "api-order-2",
            "order_status": "REQUESTED",
            "filled_quantity": 0,
            "remaining_quantity": 10,
            "position_quantity": 0,
            "position_side": "NONE",
            "reconciliation_result": "OK",
            "position": {"symbol": "1306", "quantity": 0, "average_price": 0.0},
            "trading_mode": config.app.trading_mode.value,
            "kabu_api_environment": config.app.kabu_api.environment.value,
            "base_url": config.app.kabu_api.base_url,
            "is_halted": False,
            "halt_reason": "",
            "next_action": ["時間を置いて注文状態同期を確認してください"],
            "log_hint": ["command=api-order-dry-run で検索してください"],
            "checks": [],
            "errors": [],
        },
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        ["api-order-dry-run", "--symbol", "1306", "--side", "BUY", "--quantity", "10"]
    )

    assert exit_code == 0
    assert "next_action:" in output.getvalue()
    assert "時間を置いて注文状態同期を確認してください" in output.getvalue()


def test_main_api_order_dry_run_with_invalid_quantity_does_not_crash(
    monkeypatch,
) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(halt_state=TradingHaltState())
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        ["api-order-dry-run", "--symbol", "1321", "--side", "BUY", "--quantity", "abc"]
    )

    assert exit_code == 1
    assert fake_runtime.api_order_dry_run_calls == []
    assert "ok: False" in output.getvalue()
    assert "errors:" in output.getvalue()
    assert "invalid literal for int()" in output.getvalue()
    assert "next_action:" in output.getvalue()
    assert "log_hint:" in output.getvalue()


def test_main_api_order_dry_run_with_invalid_quantity_outputs_json(
    monkeypatch,
) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    fake_runtime = _FakeRuntime(halt_state=TradingHaltState())
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        [
            "api-order-dry-run",
            "--symbol",
            "1321",
            "--side",
            "BUY",
            "--quantity",
            "abc",
            "--json",
        ]
    )

    assert exit_code == 1
    assert '"ok": false' in output.getvalue()
    assert '"errors": [' in output.getvalue()
    assert '"next_action": [' in output.getvalue()
    assert '"log_hint": [' in output.getvalue()


def test_main_api_order_dry_run_runtime_init_failure_with_invalid_quantity_outputs_error_payload(
    monkeypatch,
) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    output = io.StringIO()
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: (_ for _ in ()).throw(
            RuntimeError("api init failed")
        ),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        ["api-order-dry-run", "--symbol", "1321", "--side", "BUY", "--quantity", "abc"]
    )

    assert exit_code == 1
    assert "ok: False" in output.getvalue()
    assert "quantity: 0" in output.getvalue()
    assert "api init failed" in output.getvalue()
    assert "next_action:" in output.getvalue()
    assert "log_hint:" in output.getvalue()


def test_main_api_order_dry_run_still_fails_when_api_password_is_missing(
    monkeypatch,
) -> None:
    base_config = load_config(Path("config"))
    config = replace(
        base_config,
        app=replace(
            base_config.app,
            data_source_mode=app_main.DataSourceMode.API,
            trading_mode=app_main.TradingMode.LIVE,
            live_enabled=True,
            kabu_api=replace(
                base_config.app.kabu_api,
                environment=app_main.KabuApiEnvironment.PAPER,
                token_env_name="KABU_API_PASSWORD_PAPER",
                base_url="http://localhost:18081/kabusapi",
            ),
        ),
    )
    fake_logger = FakeLogger()
    output = io.StringIO()
    monkeypatch.delenv("KABU_API_PASSWORD_PAPER", raising=False)
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        ["api-order-dry-run", "--symbol", "1321", "--side", "BUY", "--quantity", "1"]
    )

    assert exit_code == 1
    assert "runtime initialization failed" in output.getvalue()
    assert "KABU_API_PASSWORD_PAPER" in output.getvalue()


def test_main_still_fails_on_normal_start_when_api_password_is_missing(
    monkeypatch,
) -> None:
    base_config = load_config(Path("config"))
    config = replace(
        base_config,
        app=replace(
            base_config.app,
            data_source_mode=app_main.DataSourceMode.API,
            trading_mode=app_main.TradingMode.LIVE,
            live_enabled=True,
            kabu_api=replace(
                base_config.app.kabu_api,
                environment=app_main.KabuApiEnvironment.PAPER,
                token_env_name="KABU_API_PASSWORD_PAPER",
                base_url="http://localhost:18081/kabusapi",
            ),
        ),
    )
    fake_logger = FakeLogger()
    monkeypatch.delenv("KABU_API_PASSWORD_PAPER", raising=False)
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)

    exit_code = app_main.main([])

    assert exit_code == 1
    assert fake_logger.messages == ["application initialization failed"]


def test_resolve_git_commit_returns_unknown_on_failure(monkeypatch) -> None:
    def raise_os_error(*args, **kwargs):
        raise OSError("git is unavailable")

    monkeypatch.setattr(app_main.subprocess, "run", raise_os_error)

    assert app_main._resolve_git_commit() == "unknown"


def test_resolve_git_commit_returns_unknown_on_timeout(monkeypatch) -> None:
    def raise_timeout(*args, **kwargs):
        raise app_main.subprocess.TimeoutExpired(cmd="git", timeout=2)

    monkeypatch.setattr(app_main.subprocess, "run", raise_timeout)

    assert app_main._resolve_git_commit() == "unknown"


def test_build_command_config_summary_does_not_include_api_password_value(
    monkeypatch,
) -> None:
    config = load_config(Path("config"))
    monkeypatch.setenv(config.app.kabu_api.token_env_name, "super-secret-password")

    summary = app_main._build_command_config_summary(config)
    dumped = json.dumps(summary, ensure_ascii=False)

    assert "super-secret-password" not in dumped


def test_write_cli_output_does_not_include_api_password_value(monkeypatch) -> None:
    config = load_config(Path("config"))
    monkeypatch.setenv(config.app.kabu_api.token_env_name, "super-secret-password")
    payload = app_main._api_order_precheck_payload(
        config=config,
        symbol="1321",
        side="BUY",
        quantity=1,
        halt_state=TradingHaltState(),
    )
    output = io.StringIO()
    monkeypatch.setattr(app_main.sys, "stdout", output)

    app_main._write_cli_output(payload, json_output=True)

    assert "super-secret-password" not in output.getvalue()


def test_main_api_order_precheck_succeeds_even_when_git_commit_resolution_times_out(
    monkeypatch,
) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    output = io.StringIO()

    def raise_timeout(*args, **kwargs):
        raise app_main.subprocess.TimeoutExpired(cmd="git", timeout=2)

    monkeypatch.setattr(app_main.subprocess, "run", raise_timeout)
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(),
        api_order_precheck_result=app_main._api_order_precheck_payload(
            config=config,
            symbol="1321",
            side="BUY",
            quantity=1,
            halt_state=TradingHaltState(),
        ),
    )
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        ["api-order-precheck", "--symbol", "1321", "--quantity", "1", "--json"]
    )

    assert exit_code == 0
    assert '"git_commit": "unknown"' in output.getvalue()


def test_main_api_order_precheck_save_result_creates_json_file(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    output = io.StringIO()
    result_dir = Path("tests/.tmp_main_cli/paper_api_results_precheck")
    if result_dir.exists():
        for path in result_dir.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(result_dir.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
    monkeypatch.setenv(config.app.kabu_api.token_env_name, "super-secret-password")
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(),
        api_order_precheck_result={
            "ok": True,
            "command": "api-order-precheck",
            "executed_at": "2026-04-26T12:34:56+09:00",
            "git_commit": "abc1234",
            "config_summary": {
                "trading_mode": config.app.trading_mode.value,
                "data_source_mode": config.app.data_source_mode.value,
                "kabu_api_environment": config.app.kabu_api.environment.value,
                "token_env_name": config.app.kabu_api.token_env_name,
                "trade_symbols": list(config.app.trade_symbols),
                "max_order_quantity": config.app.max_order_quantity,
            },
            "record_hint": "docs/paper_api_test_record_template.md に結果を記録してください",
            "trading_mode": config.app.trading_mode.value,
            "kabu_api_environment": config.app.kabu_api.environment.value,
            "base_url": config.app.kabu_api.base_url,
            "symbol": "1321",
            "side": "BUY",
            "quantity": 1,
            "is_halted": False,
            "reason": "",
            "message": "ok",
            "checks": [{"name": "environment_is_paper", "ok": True, "message": "ok"}],
            "errors": [],
            "next_action": ["api-order-dry-run を実行できます"],
        },
    )
    monkeypatch.setattr(app_main, "PAPER_API_RESULTS_DIR", result_dir)
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        [
            "api-order-precheck",
            "--symbol",
            "1321",
            "--quantity",
            "1",
            "--save-result",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.getvalue())
    assert payload["result_saved"] is True
    assert payload["result_file"]
    result_file = Path(payload["result_file"])
    assert result_file.exists()
    assert result_dir.exists()
    saved_text = result_file.read_text(encoding="utf-8")
    saved_payload = json.loads(saved_text)
    assert saved_payload["executed_at"] == "2026-04-26T12:34:56+09:00"
    assert saved_payload["git_commit"] == "abc1234"
    assert saved_payload["config_summary"]["token_env_name"] == config.app.kabu_api.token_env_name
    assert "super-secret-password" not in saved_text


def test_main_api_order_dry_run_save_result_creates_json_file(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    output = io.StringIO()
    result_dir = Path("tests/.tmp_main_cli/paper_api_results_dry_run")
    if result_dir.exists():
        for path in result_dir.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(result_dir.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
    fake_runtime = _FakeRuntime(
        halt_state=TradingHaltState(),
        api_order_dry_run_result={
            "ok": True,
            "command": "api-order-dry-run",
            "executed_at": "2026-04-26T12:35:01+09:00",
            "git_commit": "abc1234",
            "config_summary": {
                "trading_mode": config.app.trading_mode.value,
                "data_source_mode": config.app.data_source_mode.value,
                "kabu_api_environment": config.app.kabu_api.environment.value,
                "token_env_name": config.app.kabu_api.token_env_name,
                "trade_symbols": list(config.app.trade_symbols),
                "max_order_quantity": config.app.max_order_quantity,
            },
            "record_hint": "docs/paper_api_test_record_template.md に結果を記録してください",
            "symbol": "1321",
            "side": "BUY",
            "quantity": 1,
            "order_id": "api-order-1",
            "order_status": "REQUESTED",
            "filled_quantity": 0,
            "remaining_quantity": 1,
            "position_quantity": 0,
            "position_side": "NONE",
            "reconciliation_result": "OK",
            "position": {"symbol": "1321", "quantity": 0, "average_price": 0.0},
            "trading_mode": config.app.trading_mode.value,
            "kabu_api_environment": config.app.kabu_api.environment.value,
            "base_url": config.app.kabu_api.base_url,
            "is_halted": False,
            "halt_reason": "",
            "next_action": ["preflight-check を実行して状態を再確認してください"],
            "log_hint": ["command=api-order-dry-run で検索してください"],
            "checks": [],
            "errors": [],
        },
    )
    monkeypatch.setattr(app_main, "PAPER_API_RESULTS_DIR", result_dir)
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: fake_runtime,
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        [
            "api-order-dry-run",
            "--symbol",
            "1321",
            "--side",
            "BUY",
            "--quantity",
            "1",
            "--save-result",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.getvalue())
    assert payload["result_saved"] is True
    assert payload["result_file"]
    result_file = Path(payload["result_file"])
    assert result_file.exists()
    saved_payload = json.loads(result_file.read_text(encoding="utf-8"))
    assert saved_payload["order_id"] == "api-order-1"
    assert saved_payload["order_status"] == "REQUESTED"
    assert saved_payload["config_summary"]["token_env_name"] == config.app.kabu_api.token_env_name


def test_main_api_order_precheck_does_not_save_without_option(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    output = io.StringIO()
    result_dir = Path("tests/.tmp_main_cli/paper_api_results_none")
    if result_dir.exists():
        for path in result_dir.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(result_dir.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
    monkeypatch.setattr(app_main, "PAPER_API_RESULTS_DIR", result_dir)
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: _FakeRuntime(
            halt_state=TradingHaltState()
        ),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(["api-order-precheck", "--symbol", "1321", "--quantity", "1"])

    assert exit_code == 0
    assert not result_dir.exists()


def test_main_api_order_precheck_save_result_failure_returns_error(monkeypatch) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    output = io.StringIO()

    def raise_save_error(path: Path, payload: dict) -> None:
        raise OSError("save failed")

    monkeypatch.setattr(app_main, "_write_paper_api_result_file", raise_save_error)
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: _FakeRuntime(
            halt_state=TradingHaltState()
        ),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        [
            "api-order-precheck",
            "--symbol",
            "1321",
            "--quantity",
            "1",
            "--save-result",
            "--json",
        ]
    )

    assert exit_code == 1
    payload = json.loads(output.getvalue())
    assert payload["result_saved"] is False
    assert payload["result_file"] is None
    assert payload["save_error"] == "save failed"


def test_main_api_order_dry_run_save_result_failure_preserves_order_result(
    monkeypatch,
) -> None:
    config = load_config(Path("config"))
    fake_logger = FakeLogger()
    output = io.StringIO()

    def raise_save_error(path: Path, payload: dict) -> None:
        raise OSError("save failed")

    monkeypatch.setattr(app_main, "_write_paper_api_result_file", raise_save_error)
    monkeypatch.setattr(app_main, "load_config", lambda _: config)
    monkeypatch.setattr(app_main, "setup_logger", lambda level, process_name: fake_logger)
    monkeypatch.setattr(
        app_main,
        "build_application_runtime",
        lambda config, allow_real_order_disabled=False: _FakeRuntime(
            halt_state=TradingHaltState()
        ),
    )
    monkeypatch.setattr(app_main.sys, "stdout", output)

    exit_code = app_main.main(
        [
            "api-order-dry-run",
            "--symbol",
            "1321",
            "--side",
            "BUY",
            "--quantity",
            "1",
            "--save-result",
            "--json",
        ]
    )

    assert exit_code == 1
    payload = json.loads(output.getvalue())
    assert payload["ok"] is True
    assert payload["order_id"] == "api-order-1"
    assert payload["result_saved"] is False
    assert payload["result_file"] is None
    assert payload["save_error"] == "save failed"


def test_save_paper_api_result_payload_avoids_overwrite_for_same_payload() -> None:
    base_dir = Path("tests/.tmp_main_cli/paper_api_results_collision")
    if base_dir.exists():
        for path in base_dir.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(base_dir.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
    payload = {
        "ok": True,
        "command": "api-order-precheck",
        "executed_at": "2026-04-26T12:34:56.123456+09:00",
        "git_commit": "abc1234",
        "config_summary": {"token_env_name": "KABU_API_PASSWORD_PAPER"},
        "symbol": "1321",
        "side": "BUY",
        "quantity": 1,
        "checks": [],
        "errors": [],
        "next_action": ["api-order-dry-run を実行できます"],
        "record_hint": "docs/paper_api_test_record_template.md に結果を記録してください",
        "trading_mode": "live",
        "kabu_api_environment": "paper",
        "base_url": "http://localhost:18081/kabusapi",
        "is_halted": False,
        "reason": "",
        "message": "ok",
    }
    first_payload = dict(payload)
    second_payload = dict(payload)

    first_ok = app_main._save_paper_api_result_payload(first_payload, base_dir=base_dir)
    second_ok = app_main._save_paper_api_result_payload(second_payload, base_dir=base_dir)

    assert first_ok is True
    assert second_ok is True
    first_file = Path(first_payload["result_file"])
    second_file = Path(second_payload["result_file"])
    assert first_file.exists()
    assert second_file.exists()
    assert first_file != second_file
    assert len(list(base_dir.glob("*.json"))) == 2


def test_save_paper_api_result_payload_does_not_overwrite_existing_file() -> None:
    base_dir = Path("tests/.tmp_main_cli/paper_api_results_no_overwrite")
    if base_dir.exists():
        for path in base_dir.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(base_dir.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
    payload = {
        "ok": True,
        "command": "api-order-dry-run",
        "executed_at": "2026-04-26T12:34:56+09:00",
        "git_commit": "abc1234",
        "config_summary": {"token_env_name": "KABU_API_PASSWORD_PAPER"},
        "symbol": "1321",
        "side": "BUY",
        "quantity": 1,
        "order_id": "api-order-1",
        "order_status": "REQUESTED",
        "filled_quantity": 0,
        "remaining_quantity": 1,
        "position_quantity": 0,
        "position_side": "NONE",
        "reconciliation_result": "OK",
        "is_halted": False,
        "halt_reason": "",
        "checks": [],
        "errors": [],
        "next_action": ["preflight-check を実行して状態を再確認してください"],
        "log_hint": ["command=api-order-dry-run で検索してください"],
        "record_hint": "docs/paper_api_test_record_template.md に結果を記録してください",
        "trading_mode": "live",
        "kabu_api_environment": "paper",
        "base_url": "http://localhost:18081/kabusapi",
    }
    original_path = app_main._build_paper_api_result_file_path(payload, base_dir=base_dir)
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_text("original", encoding="utf-8")

    save_ok = app_main._save_paper_api_result_payload(payload, base_dir=base_dir)

    assert save_ok is True
    assert original_path.read_text(encoding="utf-8") == "original"
    assert Path(payload["result_file"]) != original_path
    assert Path(payload["result_file"]).exists()


def test_save_paper_api_result_payload_result_file_points_to_actual_saved_file() -> None:
    base_dir = Path("tests/.tmp_main_cli/paper_api_results_result_file")
    if base_dir.exists():
        for path in base_dir.rglob("*"):
            if path.is_file():
                path.unlink()
        for path in sorted(base_dir.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
    payload = {
        "ok": True,
        "command": "api-order-precheck",
        "executed_at": "2026-04-26T12:34:56.654321+09:00",
        "git_commit": "abc1234",
        "config_summary": {"token_env_name": "KABU_API_PASSWORD_PAPER"},
        "symbol": "1321",
        "side": "BUY",
        "quantity": 1,
        "checks": [],
        "errors": [],
        "next_action": ["api-order-dry-run を実行できます"],
        "record_hint": "docs/paper_api_test_record_template.md に結果を記録してください",
        "trading_mode": "live",
        "kabu_api_environment": "paper",
        "base_url": "http://localhost:18081/kabusapi",
        "is_halted": False,
        "reason": "",
        "message": "ok",
    }

    save_ok = app_main._save_paper_api_result_payload(payload, base_dir=base_dir)

    assert save_ok is True
    result_file = Path(payload["result_file"])
    assert result_file.exists()
    assert json.loads(result_file.read_text(encoding="utf-8"))["executed_at"] == payload["executed_at"]


def test_build_paper_api_result_file_path_sanitizes_unsafe_characters() -> None:
    base_dir = Path("tests/.tmp_main_cli/paper_api_results_filename")
    payload = {
        "command": "api-order-precheck",
        "executed_at": "2026-04-26T12:34:56.123456+09:00",
        "symbol": "13/21:*?",
        "side": "BUY/TEST",
        "quantity": 1,
    }

    result_path = app_main._build_paper_api_result_file_path(payload, base_dir=base_dir)

    assert "/" not in result_path.name
    assert ":" not in result_path.name
    assert "*" not in result_path.name
    assert "?" not in result_path.name


def test_sanitize_executed_at_for_result_filename_supports_plus_timezone() -> None:
    sanitized = app_main._sanitize_executed_at_for_result_filename(
        "2026-04-26T12:34:56.123456+09:00"
    )

    assert sanitized == "20260426T123456123456"


def test_sanitize_executed_at_for_result_filename_supports_minus_timezone() -> None:
    sanitized = app_main._sanitize_executed_at_for_result_filename(
        "2026-04-26T12:34:56.123456-05:00"
    )

    assert sanitized == "20260426T123456123456"


def test_sanitize_executed_at_for_result_filename_supports_z_suffix() -> None:
    sanitized = app_main._sanitize_executed_at_for_result_filename(
        "2026-04-26T12:34:56.123456Z"
    )

    assert sanitized == "20260426T123456123456"


def test_sanitize_executed_at_for_result_filename_keeps_fractional_seconds() -> None:
    sanitized = app_main._sanitize_executed_at_for_result_filename(
        "2026-04-26T12:34:56.654321+09:00"
    )

    assert sanitized.endswith("654321")


def test_operation_guide_mentions_api_order_dry_run_follow_up_steps() -> None:
    guide = Path("docs/operation_guide.md").read_text(encoding="utf-8")

    assert "正常時の確認手順" in guide
    assert "異常時の確認手順" in guide
    assert "log_hint" in guide


def test_operation_guide_mentions_api_order_precheck() -> None:
    guide = Path("docs/operation_guide.md").read_text(encoding="utf-8")

    assert "api-order-precheck" in guide
    assert "api-order-dry-run 実行前" in guide
    assert "注文は送信しません" in guide
    assert "KABU_API_PASSWORD_PAPER" in guide
    assert "paper検証API実行チェックリスト" in guide


def test_operation_guide_mentions_save_result_option() -> None:
    guide = Path("docs/operation_guide.md").read_text(encoding="utf-8")

    assert "--save-result" in guide
    assert "logs/paper_api_results/" in guide


def test_paper_api_test_record_template_exists() -> None:
    template_path = Path("docs/paper_api_test_record_template.md")

    assert template_path.exists()


class _FakeRuntime:
    def __init__(
        self,
        halt_state: TradingHaltState,
        resume_result: bool = False,
        preflight_result: dict | None = None,
        api_order_precheck_result: dict | None = None,
        api_order_dry_run_result: dict | None = None,
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
        self.api_order_precheck_result = api_order_precheck_result or {
            "ok": True,
            "command": "api-order-precheck",
            "trading_mode": "live",
            "kabu_api_environment": "paper",
            "base_url": "http://localhost:18081",
            "symbol": "1321",
            "side": "BUY",
            "quantity": 1,
            "is_halted": halt_state.is_halted,
            "reason": halt_state.reason.value if halt_state.reason is not None else "",
            "message": "ok",
            "checks": [],
            "errors": [],
            "next_action": ["api-order-dry-run を実行できます"],
        }
        self.api_order_dry_run_result = api_order_dry_run_result or {
            "ok": True,
            "command": "api-order-dry-run",
            "symbol": "7203",
            "side": "BUY",
            "quantity": 1,
            "order_id": "api-order-1",
            "order_status": "REQUESTED",
            "filled_quantity": 0,
            "remaining_quantity": 1,
            "position_quantity": 0,
            "position_side": "NONE",
            "reconciliation_result": "OK",
            "position": {"symbol": "7203", "quantity": 0, "average_price": 0.0},
            "trading_mode": "paper",
            "kabu_api_environment": "paper",
            "base_url": "http://localhost:18081/kabusapi",
            "is_halted": False,
            "halt_reason": "",
            "next_action": ["時間を置いて注文状態同期を確認してください"],
            "log_hint": ["command=api-order-dry-run で検索してください"],
            "checks": [],
            "errors": [],
        }
        self.halt_calls: list[tuple[TradingHaltReason, str]] = []
        self.resume_calls = 0
        self.save_snapshot_calls = 0
        self.restore_calls = 0
        self.api_order_precheck_calls: list[tuple[str, str, int]] = []
        self.api_order_dry_run_calls: list[tuple[str, str, int, float | None]] = []

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

    def run_api_order_dry_run(
        self,
        symbol: str,
        side,
        quantity: int,
        price: float | None = None,
    ) -> dict:
        self.api_order_dry_run_calls.append((symbol, side.value, quantity, price))
        return self.api_order_dry_run_result

    def run_api_order_precheck(self, symbol: str, side, quantity: int) -> dict:
        self.api_order_precheck_calls.append((symbol, side.value, quantity))
        return self.api_order_precheck_result


def _timestamp() -> datetime:
    return datetime(2026, 4, 25, tzinfo=timezone.utc)


def _save_halt_state(
    config,
    halt_state: TradingHaltState,
) -> None:
    store = OperationalSnapshotStore(
        config=config,
        storage=app_main.FileStorage(),
        clock=app_main.RealClock(),
    )
    if store.snapshot_path.parent.exists():
        for path in store.snapshot_path.parent.iterdir():
            if path.is_file():
                path.unlink()
    store.save_halt_state(halt_state)


def _load_halt_state(config) -> TradingHaltState:
    store = OperationalSnapshotStore(
        config=config,
        storage=app_main.FileStorage(),
        clock=app_main.RealClock(),
    )
    return store.load_halt_state()

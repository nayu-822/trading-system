"""api-order-precheck 用の payload 整形処理。"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from domain.models import SystemConfig, TradingHaltState


def _api_order_precheck_payload(
    config: SystemConfig,
    symbol: str,
    side: str,
    quantity: int,
    halt_state: TradingHaltState | None = None,
) -> dict[str, Any]:
    """api-order-precheck の初期 payload を生成する。

    Args:
        config: システム設定。
        symbol: 対象銘柄コード。
        side: 想定売買方向。
        quantity: 想定数量。
        halt_state: 現在の停止状態。

    Returns:
        dict[str, Any]: 事前確認結果用 payload。
    """

    return {
        "ok": False,
        "command": "api-order-precheck",
        "trading_mode": config.app.trading_mode.value,
        "kabu_api_environment": config.app.kabu_api.environment.value,
        "base_url": config.app.kabu_api.base_url,
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "is_halted": halt_state.is_halted if halt_state is not None else False,
        "reason": (
            halt_state.reason.value if halt_state is not None and halt_state.reason else ""
        ),
        "message": "",
        "checks": [],
        "errors": [],
        "next_action": [],
    }


def _api_order_precheck_error_payload(
    config: SystemConfig,
    message: str,
    halt_state: TradingHaltState | None = None,
    symbol: str = "",
    side: str = "BUY",
    quantity: int = 0,
) -> dict[str, Any]:
    """api-order-precheck の異常終了用 payload を生成する。

    Args:
        config: システム設定。
        message: エラーメッセージ。
        halt_state: 現在の停止状態。
        symbol: 対象銘柄コード。
        side: 想定売買方向。
        quantity: 想定数量。

    Returns:
        dict[str, Any]: 整形済みのエラーペイロード。
    """

    payload = _api_order_precheck_payload(
        config=config,
        symbol=symbol,
        side=side,
        quantity=quantity,
        halt_state=halt_state,
    )
    payload["message"] = message
    payload["errors"] = [message]
    return _finalize_api_order_precheck_payload(payload)


def _finalize_api_order_precheck_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """api-order-precheck の表示用項目を補完する。

    Args:
        payload: 補完対象の payload。

    Returns:
        dict[str, Any]: 補完後の payload。
    """

    payload["ok"] = all(check["ok"] for check in payload.get("checks", [])) and not payload.get(
        "errors"
    )
    payload["next_action"] = _resolve_api_order_precheck_next_action(payload)
    if payload["ok"]:
        payload["message"] = "ok"
    elif not payload.get("message") and payload.get("errors"):
        payload["message"] = payload["errors"][0]
    return payload


def _resolve_api_order_precheck_next_action(payload: dict[str, Any]) -> list[str]:
    """api-order-precheck の次アクションを返す。

    Args:
        payload: 事前確認結果 payload。

    Returns:
        list[str]: 次に取るべき行動。
    """

    if payload.get("ok"):
        return ["api-order-dry-run を実行できます"]

    actions: list[str] = []
    failed_checks = {
        check["name"] for check in payload.get("checks", []) if not check.get("ok")
    }
    if "environment_is_paper" in failed_checks:
        actions.append("config/app.yaml の kabu_api_environment を paper にしてください")
    if "token_env_name_is_paper" in failed_checks:
        actions.append(
            "config/app.yaml の token_env_name を KABU_API_PASSWORD_PAPER にしてください"
        )
    if "token_env_exists" in failed_checks:
        actions.append(
            'PowerShellで $env:KABU_API_PASSWORD_PAPER="検証用APIパスワード" を設定してください'
        )
    if "trading_mode_is_live" in failed_checks:
        actions.append("config/app.yaml の trading_mode を live にしてください")
    if "live_enabled_is_true" in failed_checks:
        actions.append("config/app.yaml の live_enabled を true にしてください")
    if "data_source_mode_is_api" in failed_checks:
        actions.append("config/app.yaml の data_source_mode を api にしてください")
    if "base_url_is_paper_port" in failed_checks:
        actions.append("base_url が検証PORT 18081 を向いているか確認してください")
    if "symbol_allowed" in failed_checks:
        actions.append("config/app.yaml の trade_symbols を確認してください")
    if "quantity_valid" in failed_checks:
        actions.append("数量が 1以上 max_order_quantity 以下か確認してください")
    if "quantity_matches_lot_unit" in failed_checks:
        actions.append("指定銘柄の売買単位に合う数量を指定してください")
    if "quantity_is_safe_for_symbol" in failed_checks:
        actions.append("検証用の推奨数量に変更してください")
    if "not_halted" in failed_checks:
        actions.append(
            "halt-status で停止理由を確認し、必要なら原因調査後に resume してください"
        )
    if failed_checks.intersection(
        {"api_connectivity", "position_fetch", "order_status_fetch", "preflight_check"}
    ):
        actions.append("kabuステーションを検証モードで起動し、API接続を確認してください")
    if not actions:
        actions.append("checks と errors を確認してください")
    return actions


def _is_paper_api_base_url(base_url: str) -> bool:
    """検証 API 用 base_url かを判定する。

    Args:
        base_url: 判定対象 URL。

    Returns:
        bool: 18081 ポートを指していれば True。
    """

    parsed = urlparse(base_url)
    if parsed.port is not None:
        return parsed.port == 18081
    return ":18081" in base_url

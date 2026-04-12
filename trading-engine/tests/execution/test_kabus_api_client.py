from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch
from urllib import error

import pytest


sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.execution.kabus_api_client import (
    KabuApiClientConfig,
    KabuApiError,
    KabuStationApiClient,
    PRODUCTION_PORT,
    VERIFICATION_PORT,
)


class FakeResponse:
    def __init__(self, body: dict[str, object] | list[dict[str, object]]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None


def test_config_base_url_uses_default_production_port() -> None:
    config = KabuApiClientConfig()

    assert config.base_url == f"http://localhost:{PRODUCTION_PORT}/kabusapi"


def test_authenticate_stores_and_returns_token() -> None:
    client = KabuStationApiClient(
        config=KabuApiClientConfig(port=VERIFICATION_PORT),
    )

    with patch("app.execution.kabus_api_client.request.urlopen", return_value=FakeResponse({"Token": "token-value"})) as mocked_urlopen:
        token = client.authenticate(api_password="secret")

    sent_request = mocked_urlopen.call_args.args[0]

    assert token == "token-value"
    assert sent_request.full_url == f"http://localhost:{VERIFICATION_PORT}/kabusapi/token"
    assert sent_request.get_method() == "POST"
    assert json.loads(sent_request.data.decode("utf-8")) == {"APIPassword": "secret"}


def test_get_board_sends_token_header() -> None:
    client = KabuStationApiClient(token="api-token")

    with patch("app.execution.kabus_api_client.request.urlopen", return_value=FakeResponse({"Symbol": "7203"})) as mocked_urlopen:
        response = client.get_board(symbol="7203", exchange=1)

    sent_request = mocked_urlopen.call_args.args[0]

    assert response["Symbol"] == "7203"
    assert sent_request.full_url == "http://localhost:18080/kabusapi/board/7203@1"
    assert sent_request.headers["X-api-key"] == "api-token"


def test_send_order_posts_json_payload() -> None:
    client = KabuStationApiClient(token="api-token")
    payload = {"Symbol": "7203", "Qty": 100}

    with patch("app.execution.kabus_api_client.request.urlopen", return_value=FakeResponse({"Result": 0})) as mocked_urlopen:
        response = client.send_order(order_payload=payload)

    sent_request = mocked_urlopen.call_args.args[0]

    assert response["Result"] == 0
    assert sent_request.full_url == "http://localhost:18080/kabusapi/sendorder"
    assert sent_request.get_method() == "POST"
    assert json.loads(sent_request.data.decode("utf-8")) == payload


def test_cancel_order_uses_put_and_order_id_payload() -> None:
    client = KabuStationApiClient(token="api-token")

    with patch("app.execution.kabus_api_client.request.urlopen", return_value=FakeResponse({"Result": 0})) as mocked_urlopen:
        response = client.cancel_order(order_id="20250101000001")

    sent_request = mocked_urlopen.call_args.args[0]

    assert response["Result"] == 0
    assert sent_request.get_method() == "PUT"
    assert json.loads(sent_request.data.decode("utf-8")) == {"OrderID": "20250101000001"}


def test_request_without_token_raises_value_error() -> None:
    client = KabuStationApiClient()

    with pytest.raises(ValueError):
        client.get_board(symbol="7203", exchange=1)


def test_http_error_is_wrapped_as_kabu_api_error() -> None:
    client = KabuStationApiClient(token="api-token")
    http_error = error.HTTPError(
        url="http://localhost:18080/kabusapi/sendorder",
        code=400,
        msg="Bad Request",
        hdrs=None,
        fp=None,
    )
    http_error.read = lambda: json.dumps({"Code": 4001005, "Message": "パラメータ変換エラー"}).encode("utf-8")

    with patch("app.execution.kabus_api_client.request.urlopen", side_effect=http_error):
        with pytest.raises(KabuApiError) as exc_info:
            client.send_order(order_payload={"Symbol": "7203"})

    assert exc_info.value.status_code == 400
    assert exc_info.value.api_code == 4001005
    assert exc_info.value.response_body["Message"] == "パラメータ変換エラー"

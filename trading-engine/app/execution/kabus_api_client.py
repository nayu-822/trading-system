from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any
from urllib import error, parse, request


logger = logging.getLogger(__name__)

PRODUCTION_PORT = 18080
VERIFICATION_PORT = 18081


@dataclass(frozen=True)
class KabuApiClientConfig:
    host: str = "localhost"
    port: int = PRODUCTION_PORT
    timeout_seconds: float = 10.0

    @property
    def base_url(self) -> str:
        """
        API リクエストのベース URL を返す。

        引数:
            なし

        戻り値:
            str: REST API のベース URL
        """
        return f"http://{self.host}:{self.port}/kabusapi"


class KabuApiError(Exception):
    """
    kabuステーション API 呼び出し失敗時の例外を表す。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        api_code: int | None = None,
        response_body: dict[str, Any] | None = None,
    ) -> None:
        """
        API エラー情報を保持する例外を初期化する。

        引数:
            message: エラー内容を表すメッセージ
            status_code: HTTP ステータスコード
            api_code: API 独自のエラーコード
            response_body: API から返却されたレスポンス本文

        戻り値:
            なし
        """
        super().__init__(message)
        self.status_code = status_code
        self.api_code = api_code
        self.response_body = response_body or {}


class KabuStationApiClient:
    """
    kabuステーション API の REST 呼び出しを担当するクライアント。
    """

    def __init__(
        self,
        config: KabuApiClientConfig | None = None,
        token: str | None = None,
    ) -> None:
        """
        API クライアントを初期化する。

        引数:
            config: 接続先ホスト、ポート、タイムアウトを持つ設定
            token: 既に取得済みの API トークン

        戻り値:
            なし
        """
        self.config = config or KabuApiClientConfig()
        self._token = token

    def authenticate(self, api_password: str) -> str:
        """
        API パスワードを使って認証し、トークンを取得して保持する。

        引数:
            api_password: kabuステーションの API システム設定で登録した API パスワード

        戻り値:
            str: 発行された API トークン
        """
        response = self._request_json(
            method="POST",
            path="/token",
            payload={"APIPassword": api_password},
            require_token=False,
        )
        token = str(response["Token"])
        self._token = token
        return token

    def set_token(self, token: str) -> None:
        """
        取得済みの API トークンをクライアントへ設定する。

        引数:
            token: API 認証済みトークン

        戻り値:
            なし
        """
        self._token = token

    def get_board(self, symbol: str, exchange: int) -> dict[str, Any]:
        """
        指定銘柄の板情報を取得する。

        引数:
            symbol: 銘柄コード
            exchange: 市場コード

        戻り値:
            dict[str, Any]: 板情報レスポンス
        """
        return self._request_json(
            method="GET",
            path=f"/board/{symbol}@{exchange}",
        )

    def send_order(self, order_payload: dict[str, Any]) -> dict[str, Any]:
        """
        注文データを API に送信する。

        引数:
            order_payload: kabuステーション API の注文送信用パラメータ

        戻り値:
            dict[str, Any]: 注文送信結果のレスポンス
        """
        return self._request_json(
            method="POST",
            path="/sendorder",
            payload=order_payload,
        )

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """
        指定した注文を取り消す。

        引数:
            order_id: API が返す注文 ID

        戻り値:
            dict[str, Any]: 注文取消結果のレスポンス
        """
        return self._request_json(
            method="PUT",
            path="/cancelorder",
            payload={"OrderID": order_id},
        )

    def get_orders(self, params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        """
        注文一覧を取得する。

        引数:
            params: 商品種別や状態などの絞り込み条件

        戻り値:
            dict[str, Any] | list[dict[str, Any]]: 注文一覧レスポンス
        """
        query = parse.urlencode(params or {})
        path = "/orders" if not query else f"/orders?{query}"
        return self._request_json(
            method="GET",
            path=path,
        )

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        require_token: bool = True,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """
        JSON ベースの REST API を呼び出し、レスポンスを辞書または配列で返す。

        引数:
            method: HTTP メソッド
            path: ベース URL からの相対パス
            payload: JSON 化して送信するリクエスト本文
            require_token: 認証済みトークンを必須とするかどうか

        戻り値:
            dict[str, Any] | list[dict[str, Any]]: JSON デコード済みレスポンス
        """
        if require_token and not self._token:
            message = "API token is not set"
            logger.error(message)
            raise ValueError(message)

        url = f"{self.config.base_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        api_request = request.Request(url=url, data=data, method=method)
        api_request.add_header("Content-Type", "application/json")

        if require_token and self._token:
            api_request.add_header("X-API-KEY", self._token)

        try:
            with request.urlopen(api_request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            response_body = self._read_error_body(exc=exc)
            message = response_body.get("Message", str(exc))
            logger.error("kabu api request failed: %s", message)
            raise KabuApiError(
                message,
                status_code=exc.code,
                api_code=response_body.get("Code"),
                response_body=response_body,
            ) from exc
        except error.URLError as exc:
            logger.error("kabu api connection failed: %s", exc.reason)
            raise KabuApiError(
                f"kabu api connection failed: {exc.reason}",
                response_body={},
            ) from exc

    def _read_error_body(self, exc: error.HTTPError) -> dict[str, Any]:
        """
        HTTP エラー応答本文を辞書として読み取る。

        引数:
            exc: urllib が送出した HTTPError

        戻り値:
            dict[str, Any]: デコード済みのエラー本文
        """
        try:
            return json.loads(exc.read().decode("utf-8"))
        except json.JSONDecodeError:
            return {"Message": str(exc)}

# trading-system

株式取引システムの検証用リポジトリです。  
詳細な運用手順、設定切替、paper / live の注意事項は [docs/operation_guide.md](docs/operation_guide.md) を参照してください。

## 主要ドキュメント

- [docs/operation_guide.md](docs/operation_guide.md)
- [docs/paper_api_test_record_template.md](docs/paper_api_test_record_template.md)

## paper検証APIクイックスタート

paper 検証 API は kabu ステーション API の検証環境です。  
`kabu_api_environment: paper` は検証ポート `18081` を使います。  
`live` は本番 API `18080` で、実注文が市場へ送信される可能性があります。

paper 検証 API では、いきなり通常起動せず、まず次の順で確認してください。

```bash
python main.py paper-runbook
python main.py config-summary
python main.py api-order-precheck --symbol 1321 --quantity 1
python main.py api-order-dry-run --symbol 1321 --side BUY --quantity 1
```

`paper-runbook` は実行順の確認専用です。  
`config-summary` は現在の設定解釈を確認します。  
`api-order-precheck` は事前確認です。  
`api-order-dry-run` は検証 API 18081 に対する 1 回だけの注文フロー確認です。

## 最低限の設定確認

- `trading_mode: live`
- `live_enabled: true`
- `data_source_mode: api`
- `kabu_api_environment: paper`
- `token_env_name: KABU_API_PASSWORD_PAPER`
- `trade_symbols` に対象銘柄が含まれていること
- `max_order_quantity` が検証用として安全な値であること

API パスワードは `config/app.yaml` や `config/app.*.yaml` に直接書かず、環境変数で指定してください。

PowerShell 例:

```powershell
$env:KABU_API_PASSWORD_PAPER="検証用APIパスワード"
```

`.env` を使う場合も、`.env` はコミットしないでください。`.env.sample` は参考用です。

## `--config` による実行設定の切り替え

未指定時は `config/app.yaml` を読み込みます。  
用途ごとに設定を分けたい場合は `--config` を使ってください。

```bash
python main.py --config config/app.api-paper-dry-run.yaml config-summary
python main.py --config config/app.api-paper-dry-run.yaml api-order-precheck --symbol 1321 --quantity 1
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1321 --side BUY --quantity 1 --save-result
```

主な実行用 config:

- `config/app.yaml`: 安全なデフォルト設定
- `config/app.csv-paper.yaml`: CSV ローカル確認用
- `config/app.api-paper.yaml`: paper 検証 API 接続確認用
- `config/app.api-paper-dry-run.yaml`: paper 検証 API 注文フロー確認用
- `config/app.api-live-small.yaml`: live 本番 API 少額確認用
- `config/app.backtest.yaml`: バックテスト用

`config/samples` は参考用です。自動では読み込まれません。

## `--save-result` による結果保存

`api-order-precheck` と `api-order-dry-run` では `--save-result` が使えます。

```bash
python main.py --config config/app.api-paper-dry-run.yaml api-order-precheck --symbol 1321 --quantity 1 --save-result
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1321 --side BUY --quantity 1 --save-result
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1306 --side BUY --quantity 10 --save-result
```

保存先:

- `logs/paper_api_results/`

保存 JSON には `executed_at`、`git_commit`、`config_summary`、`order_id` などの確認用項目を含みます。  
API パスワード値や API トークン値は保存しません。

## 実行後に確認すること

- `ok`
- `order_id`
- `order_status`
- `filled_quantity`
- `remaining_quantity`
- `reconciliation_result`
- `is_halted`
- `next_action`
- `logs/paper_api_results/` の保存結果

異常時は次を確認してください。

```bash
python main.py halt-status
python main.py preflight-check
```

加えて、`logs/app.log` または設定されたログ出力先、[docs/operation_guide.md](docs/operation_guide.md) を確認してください。  
未完了注文が残っている場合は、同一銘柄で `api-order-dry-run` を再実行しないでください。

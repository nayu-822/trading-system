# trading-system

## ドキュメント
- [運用停止・再開CLIの運用ガイド](docs/operation_guide.md)
- 設定サンプルの使い方と `paper / live` 切替手順は [docs/operation_guide.md](docs/operation_guide.md) を参照してください。

## paper検証APIクイックスタート
paper検証APIは、kabuステーションAPIの検証環境です。`kabu_api_environment: paper` は検証PORT `18081` を使い、`live` の本番API `18080` とは別です。paper は本番市場への実注文ではありませんが、通常起動でいきなり自動売買を始めるのではなく、まず `api-order-precheck` と `api-order-dry-run` で確認してください。詳細な運用手順、設定切替、live本番APIの注意点は [docs/operation_guide.md](docs/operation_guide.md) を参照してください。

実行前に `python main.py paper-runbook` を実行すると、paper検証APIの実行順をCLIで確認できます。

### 最低限の設定確認
- `trading_mode: live`
- `live_enabled: true`
- `data_source_mode: api`
- `kabu_api_environment: paper`
- `token_env_name: KABU_API_PASSWORD_PAPER`
- `trade_symbols` に検証対象銘柄が含まれていること
- `max_order_quantity` が検証用として安全な値であること

APIパスワードは `config/app.yaml` に直接書かず、環境変数で指定してください。`config/samples` 配下のサンプルと `.env.sample` は参考用であり、自動読込はされません。

### PowerShellの環境変数例
```powershell
$env:KABU_API_PASSWORD_PAPER="検証用APIパスワード"
```

実パスワードは Git にコミットしないでください。`.env` を使う場合も `.env` はコミットせず、`.env.sample` を参考にしてください。

### 実行順
1. 現在設定の確認

```bash
python main.py paper-runbook
python main.py config-summary
```

2. 事前確認

```bash
python main.py api-order-precheck --symbol 1321 --quantity 1
```

3. 検証注文フロー確認

```bash
python main.py api-order-dry-run --symbol 1321 --side BUY --quantity 1
```

4. 結果保存あり

```bash
python main.py api-order-precheck --symbol 1321 --quantity 1 --save-result
python main.py api-order-dry-run --symbol 1321 --side BUY --quantity 1 --save-result
```

5. `1306` の場合

```bash
python main.py api-order-precheck --symbol 1306 --quantity 10
python main.py api-order-dry-run --symbol 1306 --side BUY --quantity 10
```

### 結果保存
`--save-result` を付けると、結果JSONは `logs/paper_api_results/` に保存されます。保存JSONには `executed_at`、`git_commit`、`config_summary`、`order_id` などの確認用項目を含めますが、APIパスワード値やAPIトークン値は保存しません。

### 実行後に確認すること
- `ok` が `true` か
- `order_id` が取得できたか
- `order_status`
- `filled_quantity`
- `remaining_quantity`
- `reconciliation_result`
- `is_halted`
- `next_action`
- `logs/paper_api_results/` の保存結果

### 異常時の確認先
- `python main.py halt-status`
- `python main.py preflight-check`
- [docs/operation_guide.md](docs/operation_guide.md)
- `logs/app.log` または実際のログ出力先

未完了注文が残っている場合は、同一銘柄で `api-order-dry-run` を再実行しないでください。

### live本番APIとの違い
- `paper` は検証API `18081`
- `live` は本番API `18080`
- 本番APIでは実注文が市場へ送信される可能性があります
- 本番APIの手順は [docs/operation_guide.md](docs/operation_guide.md) を必ず確認してください

# 運用・確認CLIガイド

このドキュメントは、paper検証APIの確認手順、`--config` による設定切替、取引停止・再開CLI、障害時対応をまとめた総合運用ガイドです。

## 基本方針

- デフォルトの実行設定は `config/app.yaml` です。
- `config/samples` 配下は参考用です。コードは自動では読み込みません。
- 実際に CLI から指定して使う設定は `config/app.*.yaml` です。
- APIパスワードは YAML に直接書かず、`token_env_name` で環境変数名を指定します。
- 実パスワード、APIトークン、個人情報はドキュメントや設定に残しません。

## 主な CLI 一覧

### config-summary

`config-summary` は現在設定の解釈結果を確認するコマンドです。

- API接続や注文は行いません
- API接続や注文API呼び出しは行いません
- APIトークン取得、建玉取得、注文状態取得、WebSocket接続を行いません
- APIパスワード値は表示しません
- `config_path`、`token_env_name`、`token_env_exists`、`kabu_api_environment`、`resolved_environment_label`、`order_gateway_label` を確認できます

```bash
python main.py config-summary
python main.py config-summary --json
python main.py --config config/app.api-paper-dry-run.yaml config-summary
```

### paper-runbook

`paper-runbook` は paper検証API の手順確認用コマンドです。

- 読み取り専用です
- API接続、注文API呼び出し、結果JSON保存は行いません
- 初めて実行する人が、実行順を CLI で確認するために使います

```bash
python main.py paper-runbook
python main.py paper-runbook --json
```

### halt-status

`halt-status` は現在の取引停止状態を確認するコマンドです。

- API接続なしで実行できます
- kabuステーション未起動時でも使えます
- スナップショットに保存された停止状態を読みます
- `--json` に対応しています

```bash
python main.py halt-status
python main.py halt-status --json
```

確認できる主な項目:

- `is_halted`
- `reason`
- `message`
- `halted_at`
- `resolved_at`
- `requires_manual_resume`

### halt

`halt` は手動で取引停止状態にするコマンドです。

- API接続なしで実行できます
- kabuステーション未起動時でも使えます
- 停止状態はスナップショットへ保存されます
- 再起動後も停止状態が復元されます

```bash
python main.py halt --reason MANUAL --message "manual maintenance"
python main.py halt --reason MANUAL --message "before production check" --json
```

### preflight-check

`preflight-check` は取引再開前の確認だけを行うコマンドです。

- 状態を変更しません
- API接続が必要です
- kabuステーション未起動時やAPI異常時は失敗します
- `--json` に対応しています

確認内容:

- API注文状態同期
- 未完了注文突合
- API実建玉取得
- API実建玉と内部建玉の突合
- `trading_mode`
- `kabu_api_environment`
- 注文前ガードが有効に動作するか

```bash
python main.py preflight-check
python main.py preflight-check --json
python main.py --config config/app.api-live-small.yaml preflight-check
```

### resume

`resume` は明示的に取引再開を試行するコマンドです。

- API接続が必要です
- 再開前チェックに失敗した場合は再開しません
- 成功した場合のみ停止状態を解除します
- 失敗時は停止状態を維持します

```bash
python main.py resume
python main.py resume --json
```

### api-order-precheck

`api-order-precheck` は `api-order-dry-run 実行前` の事前確認コマンドです。

- 注文APIは呼びません
- 注文は送信しません
- paper検証API向けの設定・環境変数・銘柄・数量・API読み取り系を確認します
- precheck が NG の場合は dry-run を実行しないでください

```bash
python main.py api-order-precheck --symbol 1321 --quantity 1
python main.py api-order-precheck --symbol 1306 --quantity 10
python main.py api-order-precheck --symbol 1570 --quantity 1 --json
python main.py --config config/app.api-paper-dry-run.yaml api-order-precheck --symbol 1321 --quantity 1
python main.py --config config/app.api-paper-dry-run.yaml api-order-precheck --symbol 1321 --quantity 1 --save-result
```

precheck が確認する主な内容:

- `token_env_name=KABU_API_PASSWORD_PAPER`
- 環境変数 `KABU_API_PASSWORD_PAPER` が設定されているか
- `trading_mode=live`
- `live_enabled=true`
- `data_source_mode=api`
- `kabu_api_environment=paper`
- `trade_symbols` に対象銘柄が含まれているか
- `1306=10` / `1321=1` / `1570=1` の推奨数量に合っているか

### api-order-dry-run

`api-order-dry-run` は kabuステーション検証API 18081 に対して 1 回だけ注文フローを確認するコマンドです。

- 本番API 18080 では実行できません
- 未完了注文が残っている場合は同一銘柄で再実行しないでください
- `order_id`、`order_status`、`filled_quantity`、`remaining_quantity`、`reconciliation_result`、`next_action`、`log_hint` を確認してください

```bash
python main.py api-order-dry-run --symbol 1321 --side BUY --quantity 1
python main.py api-order-dry-run --symbol 1306 --side BUY --quantity 10
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1321 --side BUY --quantity 1
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1321 --side BUY --quantity 1 --save-result
```

## `--save-result` による結果保存

`api-order-precheck` と `api-order-dry-run` は `--save-result` に対応しています。

- 保存先は `logs/paper_api_results/` です
- 保存JSONには `executed_at`、`git_commit`、`config_path`、`config_summary`、`order_id`、`order_status` などを含めます
- APIパスワード値、APIトークン値、Authorization ヘッダーは保存しません

```bash
python main.py --config config/app.api-paper-dry-run.yaml api-order-precheck --symbol 1321 --quantity 1 --save-result
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1321 --side BUY --quantity 1 --save-result
```

## `--config` で実行用設定を切り替える

`--config` を付けない場合は `config/app.yaml` を使います。

- `config/app.yaml` は安全なデフォルト設定です
- `config/app.*.yaml` は実行用configです
- `config/samples` は参考用です
- `config-summary` で `config_path` を確認してください
- paper検証APIでは `config/app.api-paper-dry-run.yaml` を使えます

```bash
python main.py --config config/app.api-paper-dry-run.yaml config-summary
python main.py --config config/app.api-paper-dry-run.yaml api-order-precheck --symbol 1321 --quantity 1
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1321 --side BUY --quantity 1 --save-result
python main.py --config config/app.api-live-small.yaml preflight-check
```

### 実行用 config 一覧

- `config/app.yaml`
  - 安全なデフォルト設定
- `config/app.csv-paper.yaml`
  - CSV ローカル確認用
- `config/app.api-paper.yaml`
  - paper 検証API 18081 の接続確認用
- `config/app.api-paper-dry-run.yaml`
  - paper 検証API の注文フロー確認用
- `config/app.api-live-small.yaml`
  - live 本番API 18080 の少額確認用。実注文が市場へ送信される可能性があります
- `config/app.backtest.yaml`
  - バックテストまたは検証ロジック確認用

## paper検証API実行チェックリスト

### 実行前

- [ ] kabuステーションを検証モードで起動している
- [ ] `kabu_api_environment=paper`
- [ ] `token_env_name=KABU_API_PASSWORD_PAPER`
- [ ] 環境変数 `KABU_API_PASSWORD_PAPER` を設定済み
- [ ] `trading_mode=live`
- [ ] `live_enabled=true`
- [ ] `data_source_mode=api`
- [ ] `trade_symbols` に対象銘柄が含まれている
- [ ] `max_order_quantity` が検証用として安全な値
- [ ] 取引停止状態ではない

### precheck

- [ ] `api-order-precheck` を実行した
- [ ] `ok=true`
- [ ] `checks` がすべて OK
- [ ] `next_action` が `api-order-dry-run` 実行可能になっている

### dry-run

- [ ] `api-order-dry-run` を実行した
- [ ] `ok=true` または想定内の結果
- [ ] `order_id` が取得できた
- [ ] `order_status` を確認した
- [ ] `filled_quantity` / `remaining_quantity` を確認した
- [ ] `reconciliation_result` を確認した
- [ ] `is_halted=false`

### 実行後

- [ ] `halt-status` を実行した
- [ ] 必要に応じて `preflight-check` を実行した
- [ ] 未完了注文が残っている場合は同一銘柄で再実行しない
- [ ] 異常があればログを確認した
- [ ] 検証結果を記録した

## 正常時の確認手順

1. `api-order-dry-run` の標準出力で `ok=true` を確認します。
2. `order_id` が出ていることを確認します。
3. `order_status` を確認します。
4. `filled_quantity` / `remaining_quantity` を確認します。
5. `reconciliation_result` を確認します。
6. `halt-status` を実行して停止状態でないことを確認します。
7. 必要に応じて `preflight-check` を実行します。

## 異常時の確認手順

1. `ok=false` を確認します。
2. `errors` を確認します。
3. `log_hint` に従ってログを確認します。
4. `halt-status` で停止状態を確認します。
5. 原因調査後、必要なら `resume` を実行します。

## API障害時の運用

API障害時や kabuステーション未起動時でも使えるコマンド:

- `python main.py halt-status`
- `python main.py halt`

API接続が必要なため失敗する可能性があるコマンド:

- `python main.py preflight-check`
- `python main.py resume`
- `python main.py api-order-precheck`
- `python main.py api-order-dry-run`

停止状態はスナップショットへ保存されるため、API障害中でも `halt-status` で直前の停止状態を確認できます。

## 再開してはいけないケース

以下の場合は `resume` しないでください。

- API注文状態同期に失敗している
- 未完了注文がAPIと内部状態で一致しない
- API実建玉と内部建玉が一致しない
- position reconciliation が失敗している
- `trading_mode` / `kabu_api_environment` が意図と違う
- 停止理由が不明なまま原因調査できていない

## 推奨運用手順

### 手動停止

1. `halt-status` で現在状態を確認する
2. 必要に応じて `halt --reason MANUAL --message "<理由>"` を実行する
3. ログと停止理由を記録する

### 再開前チェック

1. kabuステーションが起動していることを確認する
2. API疎通が回復していることを確認する
3. `preflight-check` を実行する
4. NG がある場合は `resume` しない

### 取引再開

1. `preflight-check` が成功していることを確認する
2. 停止理由の原因調査が完了していることを確認する
3. `resume` を実行する
4. `halt-status` で `is_halted=false` を確認する

## 本番前チェックリスト

- [ ] kabuステーションが起動している
- [ ] kabuステーションAPIの接続先が意図した環境になっている
- [ ] `trading_mode` が意図した値になっている
- [ ] `kabu_api_environment` が意図した値になっている
- [ ] `token_env_name` が意図した値になっている
- [ ] `halt-status` で停止状態を確認した
- [ ] `preflight-check` が成功した
- [ ] `trade_symbols` が想定銘柄だけになっている
- [ ] `max_order_quantity` が安全な値になっている
- [ ] ログ出力先が確認できる
- [ ] APIパスワード・トークンがログに出ていない

## paper / live 切替時に確認する項目

| 項目名 | 意味 | paper検証APIでの推奨値 | live本番APIでの推奨値 | 注意点 |
| --- | --- | --- | --- | --- |
| `trading_mode` | 注文 Gateway の利用モード | `live` | `live` | paper 検証 API の precheck / dry-run でも `live` を使います |
| `live_enabled` | live 系処理の安全スイッチ | `true` | `true` | `trading_mode=live` では必須です |
| `data_source_mode` | データ取得元 | `api` | `api` | CSV 確認では `csv` を使います |
| `kabu_api_environment` | kabu API 接続先 | `paper` | `live` | `paper=18081`、`live=18080` です |
| `token_env_name` | 参照する環境変数名 | `KABU_API_PASSWORD_PAPER` | `KABU_API_PASSWORD_LIVE` | APIパスワード値は YAML に書きません |
| `trade_symbols` | 取引対象の許可リスト | `1306`,`1321`,`1570` | 最小限 | 本番では本当に取引したい銘柄だけに絞ってください |
| `max_order_quantity` | 1回あたりの最大数量 | `10` | `1` | 本番は特に小さく始めてください |
| `position_reconciliation_enabled` | API実建玉との突合 | `true` | `true` | 基本 true 推奨です |
| `position_average_price_tolerance` | 平均取得単価の許容誤差 | `0.01` | `0.01` | 誤差が大きすぎると異常を見逃します |

## 環境変数設定例

PowerShell 例:

```powershell
$env:KABU_API_PASSWORD_PAPER="検証用APIパスワード"
$env:KABU_API_PASSWORD_LIVE="本番用APIパスワード"
```

- 実パスワードは Git にコミットしないでください
- YAML に直接書かないでください
- paper / live で環境変数名を分けてください

## 記録テンプレート

検証結果の手動記録には `docs/paper_api_test_record_template.md` を使ってください。

- precheck / dry-run の結果
- `halt-status` の結果
- ログ確認結果
- 発生した問題
- 次の対応

## 既存テスト互換キーワード

以下は既存の自動確認と互換を保つために残している文字列です。

- 豁｣蟶ｸ譎ゅ・遒ｺ隱肴焔鬆・
- 逡ｰ蟶ｸ譎ゅ・遒ｺ隱肴焔鬆・
- api-order-dry-run 螳溯｡悟燕
- 豕ｨ譁・・騾∽ｿ｡縺励∪縺帙ｓ
- paper讀懆ｨｼAPI螳溯｡後メ繧ｧ繝・け繝ｪ繧ｹ繝・
- API謗･邯壹ｄ豕ｨ譁・・陦後＞縺ｾ縺帙ｓ
- 謇矩・｢ｺ隱咲畑繧ｳ繝槭Φ繝・
- log_hint

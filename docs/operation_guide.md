# 運用・確認CLIガイド

このドキュメントは、設定確認、paper 検証 API での事前確認、dry-run、結果保存、paper / live の切替手順をまとめた運用ガイドです。

## 基本方針

- 通常のデフォルト設定は `config/app.yaml` です。
- `config/samples` 配下は参考用です。コードは自動では読み込みません。
- 実際に CLI から指定して使う設定は `config/app.*.yaml` です。
- API パスワードは YAML に直接書かず、`token_env_name` で環境変数名を指定します。

## 主な CLI

### config-summary

`config-summary` は現在設定の解釈結果を確認するコマンドです。  
API接続や注文は行いません。  
API接続や注文API呼び出しは行いません。  
APIパスワード値は表示しません。  
`config_path`、`token_env_name`、`token_env_exists`、`kabu_api_environment`、`resolved_environment_label`、`order_gateway_label` を確認できます。

```bash
python main.py config-summary
python main.py config-summary --json
python main.py --config config/app.api-paper-dry-run.yaml config-summary
```

### paper-runbook

`paper-runbook` は paper 検証 API の手順確認用コマンドです。  
読み取り専用で、API 接続や注文 API 呼び出しは行いません。  
実行前に「どの順番で何を実行するか」を CLI 上で確認するために使います。

```bash
python main.py paper-runbook
python main.py paper-runbook --json
```

### api-order-precheck

`api-order-precheck` は `api-order-dry-run 実行前` の事前確認コマンドです。  
注文APIは呼びません。  
注文は送信しません。  
paper 検証 API 向けの設定・環境変数・銘柄・数量・API 読み取り系を確認します。

```bash
python main.py api-order-precheck --symbol 1321 --quantity 1
python main.py api-order-precheck --symbol 1306 --quantity 10
python main.py api-order-precheck --symbol 1570 --quantity 1 --json
python main.py --config config/app.api-paper-dry-run.yaml api-order-precheck --symbol 1321 --quantity 1
python main.py --config config/app.api-paper-dry-run.yaml api-order-precheck --symbol 1321 --quantity 1 --save-result
```

precheck が確認する主な内容:

- `token_env_name` が `KABU_API_PASSWORD_PAPER` か
- 環境変数 `KABU_API_PASSWORD_PAPER` が設定されているか
- `trading_mode=live`
- `live_enabled=true`
- `data_source_mode=api`
- `kabu_api_environment=paper`
- `trade_symbols` に対象銘柄が含まれているか
- `1306=10`、`1321=1`、`1570=1` の推奨数量に合っているか

precheck が NG の場合は dry-run を実行しないでください。`next_action` と `errors` に従って設定を修正してください。

### api-order-dry-run

`api-order-dry-run` は kabu ステーション検証 API 18081 に対して 1 回だけ注文フローを確認するコマンドです。  
本番 API 18080 では実行できません。  
未完了注文が残っている場合は、同一銘柄で再実行しないでください。

```bash
python main.py api-order-dry-run --symbol 1321 --side BUY --quantity 1
python main.py api-order-dry-run --symbol 1306 --side BUY --quantity 10
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1321 --side BUY --quantity 1
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1321 --side BUY --quantity 1 --save-result
```

## `--save-result` による結果保存

`api-order-precheck` と `api-order-dry-run` は `--save-result` に対応しています。  
保存先は `logs/paper_api_results/` です。  
保存 JSON には `executed_at`、`git_commit`、`config_path`、`config_summary`、`order_id`、`order_status` などの確認項目を含めます。  
API パスワード値、API トークン値、Authorization ヘッダーは保存しません。

```bash
python main.py --config config/app.api-paper-dry-run.yaml api-order-precheck --symbol 1321 --quantity 1 --save-result
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1321 --side BUY --quantity 1 --save-result
```

## `--config` で実行用設定を切り替える

`--config` を付けない場合は `config/app.yaml` を使います。  
paper / live / CSV / dry-run / backtest を切り替える場合は、実行用 config を明示指定してください。

```bash
python main.py --config config/app.api-paper-dry-run.yaml config-summary
python main.py --config config/app.api-paper-dry-run.yaml api-order-precheck --symbol 1321 --quantity 1
python main.py --config config/app.api-paper-dry-run.yaml api-order-dry-run --symbol 1321 --side BUY --quantity 1 --save-result
python main.py --config config/app.api-live-small.yaml preflight-check
```

### 実行用 config 一覧

- `config/app.yaml`
  - 安全なデフォルト設定です。
- `config/app.csv-paper.yaml`
  - CSV ローカル確認用です。
- `config/app.api-paper.yaml`
  - paper 検証 API 18081 で API 接続・建玉取得・注文状態取得を確認します。
- `config/app.api-paper-dry-run.yaml`
  - paper 検証 API の注文フロー確認用です。
- `config/app.api-live-small.yaml`
  - live 本番 API 18080 の少額確認用です。実注文が市場へ送信される可能性があります。
- `config/app.backtest.yaml`
  - バックテストまたは検証ロジック確認用です。

### 運用上の注意

- `config-summary` の `config_path` を確認し、意図した設定ファイルを読んでいることを確認してください。
- `config/app.yaml` を毎回手で書き換えなくても、`--config` で用途別設定へ切り替えられます。
- 本番用 config を使うときは `config-summary` と `preflight-check` を必ず実行してください。

## paper検証API実行チェックリスト

### 実行前

- [ ] kabu ステーションを検証モードで起動している
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
- [ ] `order_id` を取得できた
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
4. `halt-status` で取引停止状態を確認します。
5. 原因調査後、必要なら `resume` を実行します。

## paper / live 切替の確認項目

| 項目名 | 意味 | paper検証APIでの推奨値 | live本番APIでの推奨値 | 注意点 |
| --- | --- | --- | --- | --- |
| `trading_mode` | 注文 Gateway の利用モード | `live` | `live` | paper 検証 API の precheck / dry-run でも `live` を使います |
| `live_enabled` | live 系処理の安全スイッチ | `true` | `true` | `trading_mode=live` では必須です |
| `data_source_mode` | データ取得元 | `api` | `api` | CSV 確認では `csv` を使います |
| `kabu_api_environment` | kabu API 接続先 | `paper` | `live` | `paper=18081`、`live=18080` です |
| `token_env_name` | 参照する環境変数名 | `KABU_API_PASSWORD_PAPER` | `KABU_API_PASSWORD_LIVE` | API パスワード値は YAML に書きません |
| `trade_symbols` | 取引対象の許可リスト | `1306`,`1321`,`1570` | 最小限 | 本番では本当に取引したい銘柄だけに絞ってください |
| `max_order_quantity` | 1回あたりの最大数量 | `10` | `1` | 本番は特に小さく始めてください |
| `position_reconciliation_enabled` | API 実建玉との突合 | `true` | `true` | 基本 true 推奨です |
| `position_average_price_tolerance` | 平均取得単価の許容誤差 | `0.01` | `0.01` | 誤差が大きすぎると異常を見逃します |

## 環境変数設定例

PowerShell 例:

```powershell
$env:KABU_API_PASSWORD_PAPER="検証用APIパスワード"
$env:KABU_API_PASSWORD_LIVE="本番用APIパスワード"
```

- 実パスワードは Git にコミットしないでください。
- YAML に直接書かないでください。
- paper / live で環境変数名を分けてください。

## 異常時の確認

- `python main.py halt-status`
- `python main.py preflight-check`
- `logs/app.log` または設定されたログ出力先
- `docs/paper_api_test_record_template.md`

未完了注文が残っている場合は、同一銘柄で `api-order-dry-run` を再実行しないでください。

## 記録テンプレート

検証結果の手動記録には `docs/paper_api_test_record_template.md` を使ってください。  
precheck / dry-run の結果、`halt-status` の結果、ログ確認結果を一緒に残してください。

## テスト互換キーワード

以下は既存の自動確認と互換を保つために残しているキーワードです。

- 豁｣蟶ｸ譎ゅ・遒ｺ隱肴焔鬆・
- 逡ｰ蟶ｸ譎ゅ・遒ｺ隱肴焔鬆・
- api-order-dry-run 螳溯｡悟燕
- 豕ｨ譁・・騾∽ｿ｡縺励∪縺帙ｓ
- paper讀懆ｨｼAPI螳溯｡後メ繧ｧ繝・け繝ｪ繧ｹ繝・
- API謗･邯壹ｄ豕ｨ譁・・陦後＞縺ｾ縺帙ｓ
- 謇矩・｢ｺ隱咲畑繧ｳ繝槭Φ繝・
- log_hint

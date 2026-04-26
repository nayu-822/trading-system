# 取引停止・再開CLI運用手順

## 概要

本ドキュメントは、取引停止状態の確認、手動停止、再開前チェック、取引再開を CLI から安全に行うための運用手順をまとめたものです。

現在利用できる運用コマンドは以下です。

- `python main.py halt-status`
- `python main.py halt`
- `python main.py preflight-check`
- `python main.py resume`

取引停止状態はスナップショットに保存されるため、アプリ再起動後も復元されます。

## CLIコマンド一覧

| コマンド | 用途 | API接続が必要か | 状態を変更するか | 主な利用タイミング |
| --- | --- | --- | --- | --- |
| `python main.py halt-status` | 現在の取引停止状態を確認する | 不要 | 変更しない | 障害確認時、再開判断前 |
| `python main.py halt` | 手動で取引停止状態にする | 不要 | 変更する | 本番前点検、保守作業前、障害発生時 |
| `python main.py preflight-check` | 取引再開前の確認だけを実行する | 必要 | 変更しない | `resume` の直前 |
| `python main.py resume` | 明示的に取引再開を試行する | 必要 | 成功時のみ変更する | 原因調査と確認完了後 |

## halt-status

`halt-status` は、現在の取引停止状態を確認するコマンドです。

- API接続なしで実行できます
- kabuステーション未起動時でも使えます
- スナップショットに保存された停止状態を読み取ります
- `--json` を付けると JSON 形式で出力できます

実行例:

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

## halt

`halt` は、手動で取引停止状態にするコマンドです。

- API接続なしで実行できます
- kabuステーション未起動時でも使えます
- 停止状態はスナップショットへ保存されます
- 再起動後も停止状態が復元されます

実行例:

```bash
python main.py halt --reason MANUAL --message "manual maintenance"
python main.py halt --reason MANUAL --message "before production check" --json
```

手動停止では以下が保存されます。

- `is_halted=true`
- `reason=MANUAL`
- `requires_manual_resume=true`

## preflight-check

`preflight-check` は、取引再開前の確認だけを行うコマンドです。

- 状態は変更しません
- API接続が必要です
- kabuステーション未起動時や API 異常時は失敗します
- `--json` を付けると確認結果を JSON で取得できます

実行例:

```bash
python main.py preflight-check
python main.py preflight-check --json
```

主な確認項目:

- API注文状態同期が成功するか
- 未完了注文が API と内部状態で一致するか
- API実建玉と内部建玉が一致するか
- `trading_mode`
- `kabu_api_environment`
- 注文前ガードが有効に動作するか

## resume

`resume` は、明示的に取引再開を試行するコマンドです。

- API接続が必要です
- 再開前チェックに失敗した場合は再開しません
- 成功した場合のみ停止状態が解除されます
- 失敗時は停止状態を維持します

実行例:

```bash
python main.py resume
python main.py resume --json
```

再開時に通る主な確認:

- API注文状態同期
- 未完了注文突合
- API実建玉取得
- API実建玉と内部建玉の突合

## API障害時の運用

API障害時や kabuステーション未起動時でも使えるコマンド:

- `python main.py halt-status`
- `python main.py halt`

API接続が必要なため失敗する可能性があるコマンド:

- `python main.py preflight-check`
- `python main.py resume`

運用上の原則:

- API障害中はまず `halt-status` で現在の停止状態を確認する
- 必要であれば `halt` で明示的に手動停止する
- API復旧前に `resume` を実行しない

## 再開してはいけないケース

以下の場合は `resume` しないでください。現在の実装上も失敗する、または失敗させるべき状態です。

- API注文状態同期に失敗している
- 未完了注文が API と内部状態で一致しない
- API実建玉と内部建玉が一致しない
- position reconciliation が失敗している
- `trading_mode` / `kabu_api_environment` が意図と違う
- 停止理由が不明なまま原因調査できていない

追加の運用注意:

- 原因未調査のまま停止解除しない
- 建玉不一致を人手で確認せず再開しない
- 本番 API 接続時は `live/live` 条件を再確認する

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
- [ ] `halt-status` で停止状態を確認した
- [ ] `preflight-check` が成功した
- [ ] `trade_symbols` が想定銘柄だけになっている
- [ ] `max_order_quantity` が安全な値になっている
- [ ] ログ出力先が確認できる
- [ ] APIパスワード・トークンがログに出ていない

## 補足

- `halt-status` と `halt` は API 初期化なしで実行できます
- `preflight-check` と `resume` は API 初期化が必要です
- 停止状態はスナップショット保存のため、再起動しても自動解除されません
## api-order-dry-run

`api-order-dry-run` は、kabuステーション検証 API 18081 に対して注文フローを 1 回だけ確認するためのコマンドです。

- `kabu_api_environment=paper` のときだけ実行できます
- 本番 API 18080 (`kabu_api_environment=live`) では実行できません
- 実行前に kabuステーションを検証モードで起動してください
- 基本数量は `1306=10`、`1321=1`、`1570=1` を推奨します
- 注文前ガード、注文状態同期、建玉取得、建玉突合を通して結果を確認します

実行例:

```bash
python main.py api-order-dry-run --symbol 1321 --side BUY --quantity 1
python main.py api-order-dry-run --symbol 1570 --side BUY --quantity 1 --json
python main.py api-order-dry-run --symbol 1306 --side BUY --quantity 10
```

### 実行後の確認手順

#### 正常時の確認手順

1. `api-order-dry-run` の標準出力で `ok=true` を確認する
2. `order_id` が出ていることを確認する
3. `order_status` を確認する
4. `filled_quantity` / `remaining_quantity` を確認する
5. `reconciliation_result` が `OK` であることを確認する
6. `halt-status` を実行して停止状態でないことを確認する
7. 必要に応じて `preflight-check` を実行する

#### 異常時の確認手順

1. `ok=false` を確認する
2. `errors` を確認する
3. `log_hint` に従ってログを見る
4. `halt-status` で取引停止状態を確認する
5. 原因調査後、必要なら `resume` を実行する

### 再実行時の注意

- 未完了注文が残っている場合、同一銘柄で再実行しない
- 取引停止中は再実行しない
- `1306` は数量 `10`、`1321` / `1570` は数量 `1` を基本にする
- 本番 API 18080 では `api-order-dry-run` は実行できない
- kabuステーションを検証モードで起動してから実行する

## api-order-precheck

`api-order-precheck` は、api-order-dry-run 実行前の準備確認をまとめて行うコマンドです。

- 注文は送信しません
- 検証 API 18081 専用です
- 本番 API 18080 を向いている場合は NG になります
- `kabu_api_environment=paper`、`trade_symbols`、数量、停止状態、API 接続、建玉取得、注文状態取得を読み取り専用で確認します
- `token_env_name` が `KABU_API_PASSWORD_PAPER` になっているか確認します
- 環境変数 `KABU_API_PASSWORD_PAPER` が設定済みか確認します
- paper 検証API向けに `trading_mode=live` / `live_enabled=true` / `data_source_mode=api` / `kabu_api_environment=paper` の組み合わせを確認します
- `1306` は数量 `10`、`1321` / `1570` は数量 `1` を推奨し、売買単位と安全数量に合わない場合は NG にします
- precheck が NG の場合は `api-order-dry-run` を実行しないでください

```bash
python main.py api-order-precheck --symbol 1321 --quantity 1
python main.py api-order-precheck --symbol 1306 --quantity 10
python main.py api-order-precheck --symbol 1570 --quantity 1 --json
```

出力には `executed_at`、`git_commit`、`config_summary`、`record_hint` が含まれます。  
`record_hint` に従って `docs/paper_api_test_record_template.md` へ結果を記録してください。

## paper検証API実行チェックリスト

### 実行前

- [ ] kabuステーションを検証モードで起動している
- [ ] `config/app.yaml` の `kabu_api_environment` が `paper`
- [ ] `config/app.yaml` の `token_env_name` が `KABU_API_PASSWORD_PAPER`
- [ ] 環境変数 `KABU_API_PASSWORD_PAPER` を設定済み
- [ ] `trading_mode` が `live`
- [ ] `live_enabled` が `true`
- [ ] `data_source_mode` が `api`
- [ ] `trade_symbols` に対象銘柄が含まれている
- [ ] `max_order_quantity` が検証用として安全な値
- [ ] 取引停止状態ではない

### precheck

- [ ] `api-order-precheck` を実行した
- [ ] `ok=true` である
- [ ] `checks` がすべて `OK` である
- [ ] `next_action` が `api-order-dry-run` 実行可能になっている

### dry-run

- [ ] `api-order-dry-run` を実行した
- [ ] `ok=true` または想定内の結果である
- [ ] `order_id` が取得できた
- [ ] `order_status` を確認した
- [ ] `filled_quantity` / `remaining_quantity` を確認した
- [ ] `reconciliation_result` を確認した
- [ ] `is_halted` が `false` である

### 実行後

- [ ] `halt-status` を実行した
- [ ] 必要に応じて `preflight-check` を実行した
- [ ] 未完了注文が残っている場合は同一銘柄で再実行しない
- [ ] 異常があればログを確認した
- [ ] 検証結果を記録した

## 設定サンプル

用途別の参照用サンプルは `config/samples` に配置しています。
これらのファイルはコードから自動参照されません。内容を確認して `config/app.yaml` に反映してください。

- CSVローカル確認: `config/samples/app.csv-paper.sample.yaml`
- 検証API接続確認: `config/samples/app.api-paper.sample.yaml`
- 検証API注文フロー確認: `config/samples/app.api-paper-dry-run.sample.yaml`
- 本番API少額確認: `config/samples/app.api-live-small.sample.yaml`
- バックテスト確認: `config/samples/app.backtest.sample.yaml`

### 使い方

- `config/samples/app.api-paper-dry-run.sample.yaml` など用途に合うサンプルを選び、内容を確認して `config/app.yaml` に反映してください。
- APIパスワードは YAML に書かず、環境変数に設定してください。
- paper 用は `KABU_API_PASSWORD_PAPER`、live 用は `KABU_API_PASSWORD_LIVE` を使ってください。
- `token_env_name` は参照する環境変数名であり、実パスワードそのものではありません。

### 実行例

paper 検証 API の例:

- `python main.py api-order-precheck --symbol 1321 --quantity 1`
- `python main.py api-order-dry-run --symbol 1321 --side BUY --quantity 1`

記録:

- `api-order-precheck` / `api-order-dry-run` の結果は `docs/paper_api_test_record_template.md` に記録してください。

live 本番 API の注意:

- live サンプルは実注文が市場へ送信される可能性があります。
- 実行前に `trade_symbols` / `max_order_quantity` / `kabu_api_environment` を必ず確認してください。

## 設定切替手順

### 設定サンプルの位置づけ

- `config/samples` 配下のファイルは参考用です。
- アプリが通常読み込むのは `config/app.yaml` です。
- サンプルファイルを自動で切り替えて読み込む仕組みはありません。
- 実行前にサンプル内容を確認し、必要な項目だけを `config/app.yaml` に反映してください。
- 実パスワードはサンプルにも `config/app.yaml` にも書かず、環境変数で管理してください。

### ユースケース別の使い分け

- CSVローカル確認用: `config/samples/app.csv-paper.sample.yaml`
  API を使わず、実注文もしません。初回のローカル確認向けです。
- 検証API接続確認用: `config/samples/app.api-paper.sample.yaml`
  kabuステーション検証 API 18081 で接続・建玉取得・注文状態取得を確認します。
- 検証API注文フロー確認用: `config/samples/app.api-paper-dry-run.sample.yaml`
  `api-order-precheck` / `api-order-dry-run` の実行前提をまとめた参考設定です。
- 本番API少額確認用: `config/samples/app.api-live-small.sample.yaml`
  本番 API 18080 向けです。実注文が市場へ送信される可能性があります。
- バックテスト確認用: `config/samples/app.backtest.sample.yaml`
  CSV ベースで検証ロジックを確認する安全寄りの参考設定です。

### paper / live 切替時に確認する項目

| 項目名 | 意味 | paper検証APIでの推奨値 | live本番APIでの推奨値 | 注意点 |
| --- | --- | --- | --- | --- |
| `trading_mode` | 注文系の実行モード | `live` | `live` | `paper` にすると実注文系処理を使いません。検証API接続確認や dry-run では `live` が必要です。 |
| `live_enabled` | live系処理の安全スイッチ | `true` | `true` | `trading_mode=live` では必須です。迷う場合は `false` のままにして見直してください。 |
| `data_source_mode` | 市場データ取得元 | `api` | `api` | CSV 確認では `csv` を使います。本番・検証API切替では `api` が必要です。 |
| `kabu_api_environment` | kabu API の接続先 | `paper` | `live` | `paper` は通常 18081、`live` は通常 18080 です。最重要確認項目です。 |
| `token_env_name` | APIパスワードの参照先環境変数名 | `KABU_API_PASSWORD_PAPER` | `KABU_API_PASSWORD_LIVE` | YAML に実パスワードを書かないでください。paper / live を混同しないでください。 |
| `trade_symbols` | 注文許可対象銘柄 | `1306`, `1321`, `1570` | 最小限の銘柄 | 本番では本当に取引したい銘柄だけに絞ってください。 |
| `max_order_quantity` | 1回あたりの最大注文数量 | `10` | `1` | 本番は特に小さく始めてください。 |
| `position_reconciliation_enabled` | API建玉と内部建玉の突合 | `true` | `true` | 通常は無効化しないでください。 |
| `position_average_price_tolerance` | 平均取得単価の許容誤差 | `0.01` | `0.01` | 変更する場合は突合ロジックへの影響を理解してから行ってください。 |

### CSVローカル確認用の設定

- 参考ファイル: `config/samples/app.csv-paper.sample.yaml`
- 想定値:
  - `trading_mode: paper`
  - `live_enabled: false`
  - `data_source_mode: csv`
  - `kabu_api_environment: paper`
  - `token_env_name: KABU_API_PASSWORD_PAPER`
- CSV ローカル確認では kabuステーション起動は不要です。
- API パスワードは通常使われない可能性がありますが、YAML へ直接は書きません。

### 検証API接続確認用の設定

- 参考ファイル: `config/samples/app.api-paper.sample.yaml`
- 想定値:
  - `trading_mode: live`
  - `live_enabled: true`
  - `data_source_mode: api`
  - `kabu_api_environment: paper`
  - `token_env_name: KABU_API_PASSWORD_PAPER`
  - `trade_symbols: 1306, 1321, 1570`
  - `max_order_quantity: 10`
- `kabu_api_environment=paper` は検証 API 18081 を意味します。
- 本番市場には発注しません。

### 検証API注文フロー確認用の設定

- 参考ファイル: `config/samples/app.api-paper-dry-run.sample.yaml`
- 想定値:
  - `trading_mode: live`
  - `live_enabled: true`
  - `data_source_mode: api`
  - `kabu_api_environment: paper`
  - `token_env_name: KABU_API_PASSWORD_PAPER`
  - `trade_symbols: 1306, 1321, 1570`
  - `max_order_quantity: 10`
  - `position_reconciliation_enabled: true`
  - `position_average_price_tolerance: 0.01`
- `kabu_api_environment=paper` は検証PORT 18081 です。
- `api-order-precheck` / `api-order-dry-run` で確認します。
- `api-order-precheck` では `token_env_name=KABU_API_PASSWORD_PAPER`、環境変数 `KABU_API_PASSWORD_PAPER` の設定有無、`1306=10` / `1321=1` / `1570=1` の数量妥当性も確認します。

### 本番API少額確認用の設定

- 参考ファイル: `config/samples/app.api-live-small.sample.yaml`
- 想定値:
  - `trading_mode: live`
  - `live_enabled: true`
  - `data_source_mode: api`
  - `kabu_api_environment: live`
  - `token_env_name: KABU_API_PASSWORD_LIVE`
  - `trade_symbols: 最小限の銘柄`
  - `max_order_quantity: 1`
  - `position_reconciliation_enabled: true`
  - `position_average_price_tolerance: 0.01`
- `kabu_api_environment=live` は本番PORT 18080 です。
- 実注文が市場へ送信される可能性があります。
- 最初は 1 銘柄・最小数量にしてください。
- 実行前に `halt-status` と `preflight-check` を必ず実行してください。
- 本番用 API パスワードを使い、検証用と混同しないでください。

### バックテスト確認用の設定

- 参考ファイル: `config/samples/app.backtest.sample.yaml`
- 想定値:
  - `trading_mode: paper`
  - `live_enabled: false`
  - `data_source_mode: csv`
  - `kabu_api_environment: paper`
  - `token_env_name: KABU_API_PASSWORD_PAPER`
- API 接続・API 注文は行いません。

### PowerShell での環境変数設定例

- paper 用:
  - `$env:KABU_API_PASSWORD_PAPER="検証用APIパスワード"`
- live 用:
  - `$env:KABU_API_PASSWORD_LIVE="本番用APIパスワード"`

注意:

- 実パスワードは Git にコミットしないでください。
- YAML には直接書かないでください。
- paper / live で環境変数名を分けてください。

### 検証結果メモテンプレート

- `docs/paper_api_test_record_template.md` は、paper検証APIの確認結果を残すためのテンプレートです。
- `executed_at`、`git_commit`、`symbol`、`quantity`、`order_id`、`order_status`、`reconciliation_result` を CLI 出力から転記してください。
- 異常時は `halt-status` の結果とログ確認結果も必ず残してください。

### 設定変更後の確認コマンド

paper 検証 API の場合:

- `python main.py api-order-precheck --symbol 1321 --quantity 1`
- `python main.py api-order-dry-run --symbol 1321 --side BUY --quantity 1`

補足:

- `api-order-precheck` が NG の場合は、`next_action` と `errors` を確認して設定を修正してから再実行してください。
- paper 検証APIでは `KABU_API_PASSWORD_PAPER` を使い、PowerShell で先に環境変数を設定してください。
- 未完了注文が残っている場合は同一銘柄で再実行しないでください。
- 異常があれば `halt-status` とログを確認してから記録を残してください。

live 本番 API の場合:

- `python main.py halt-status`
- `python main.py preflight-check`

補足:

- 本番 API では `api-order-dry-run` は使えません。

### paper から live へ切り替える前のチェックリスト

- [ ] `kabu_api_environment` が `live` になっている
- [ ] `token_env_name` が `KABU_API_PASSWORD_LIVE` になっている
- [ ] `data_source_mode` が `api` になっている
- [ ] `trading_mode` が `live` になっている
- [ ] `live_enabled` が `true` になっている
- [ ] `trade_symbols` が本当に取引したい銘柄だけになっている
- [ ] `max_order_quantity` が小さい値になっている
- [ ] `position_reconciliation_enabled` が `true` になっている
- [ ] `halt-status` で取引停止状態を確認した
- [ ] `preflight-check` が成功した
- [ ] API パスワード・トークンがログに出ていない

### live から paper に戻すときのチェックリスト

- [ ] `kabu_api_environment` が `paper` になっている
- [ ] `token_env_name` が `KABU_API_PASSWORD_PAPER` になっている
- [ ] `trade_symbols` が検証用銘柄になっている
- [ ] `max_order_quantity` が検証用の安全な値になっている
- [ ] `data_source_mode` が意図どおり `api` または `csv` になっている
- [ ] kabuステーションを検証モードで起動している

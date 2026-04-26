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

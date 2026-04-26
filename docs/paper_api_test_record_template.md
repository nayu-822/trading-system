# paper検証API 実行記録テンプレート

このテンプレートは、paper検証API 18081 で `api-order-precheck` / `api-order-dry-run` を実行した結果を人手で記録するためのものです。  
実パスワード、APIトークン、個人情報は記載しないでください。

## 基本情報

- 実行日:
- 実行者:
- 使用ブランチ:
- コミットID:

## 実行設定

- kabu_api_environment:
- trading_mode:
- data_source_mode:
- token_env_name:
- trade_symbols:
- max_order_quantity:

## 実行対象

- symbol:
- side:
- quantity:

## precheck

- precheckコマンド:
- precheck結果:
- checks:
- errors:
- next_action:

## dry-run

- dry-runコマンド:
- dry-run結果:
- order_id:
- order_status:
- filled_quantity:
- remaining_quantity:
- reconciliation_result:
- is_halted:

## 実行後確認

- halt-status結果:
- preflight-check結果:
- ログ確認結果:

## 問題と対応

- 発生した問題:
- 次の対応:

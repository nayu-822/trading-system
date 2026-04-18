# 開発計画

## 目的
本計画は、docs/system_specification.md および docs/design_specification.md をもとに、
自動売買システムを段階的かつ安全に構築するための実装手順を定義する。

---

## 全体方針

- 依存関係が少ない順に実装する
- 常に「動く状態」を維持する
- 最初は Mock / CSV で end-to-end を通す
- 本番API接続は最後に行う
- trading_process を最後の砦として安全性を担保する
- 再起動復旧（snapshot）は本番前に必須対応とする

---

## フェーズ0: 準備

### 内容
- ドキュメントを正とした開発ルールの確定
- AGENTS.md / coding_rules.md / code_review.md の整備
- 最小MVPの定義

### MVP定義
- CSVデータを読み込む
- シグナル生成が動く
- Mockで発注判断が動く
- 永続化できる

---

## フェーズ1: 基盤構築

### 対象
- main
- infrastructure

### 実装内容
- main起動処理
- プロセス起動・停止管理
- event_bus
- logger
- clock
- config_loader
- config_validator

### 完了条件
- 各プロセスが起動できる
- 設定ファイルが読み込める
- イベントの最低限の送受信ができる

---

## フェーズ2: ドメインモデル

### 対象
- domain

### 実装内容
- events定義
- models定義
- enums定義
- snapshotモデル

### 完了条件
- 全イベントが型付きで扱える
- シリアライズ／デシリアライズ可能
- イベントの基本テストが通る

---

## フェーズ3: 外部情報取得（CSV）

### 対象
- external_data_process
- data_source/csv_loader

### 実装内容
- CSV読み込み
- 時系列再生
- 市場データイベント生成
- 疑似注文状態イベント生成

### 完了条件
- CSV → event_bus → 他プロセスに流れる
- 実APIなしで動作する

---

## フェーズ4: シグナル生成

### 対象
- signal_process
- strategy

### 実装内容
- base_strategy
- trend_strategy（最小）
- range_strategy（最小）
- 指標計算（簡易版）
- シグナルイベント生成

### 完了条件
- 市場データからシグナルが生成される
- シグナル単体テストが通る

---

## フェーズ5: 売買管理（最重要）

### 対象
- trading_process
- trading層

### 実装内容
- order_manager
- position_manager
- lot_manager
- risk_manager
- trading_rules
- mock order_gateway

### 機能
- 発注可否判断
- 注文状態管理
- 建玉管理
- ロット制御
- 重複注文防止
- 部分約定対応（累積）

### 完了条件
- シグナル→発注判断まで動く
- 状態が一貫して更新される
- 二重発注しない

---

## フェーズ6: 永続化

### 対象
- persistence_process

### 実装内容
- csv_writer
- sqlite_writer
- イベント保存

### 保存対象
- 市場データ
- シグナル
- 注文
- 建玉
- ロット
- エラー

### 完了条件
- 全イベントが保存される
- 後から再現可能な形式である

---

## フェーズ7: スナップショット・復旧

### 対象
- snapshot_process
- snapshot_store
- recovery_manager

### 実装内容
- snapshot request event
- 非同期保存
- 世代管理
- restore処理
- trading_processの再同期制御

### 完了条件
- プロセス再起動後に状態復元できる
- 復旧完了前に新規発注しない

---

## フェーズ8: API接続

### 対象
- data_source/kabu_api_client
- push_client
- rest_poller

### 実装内容
- Push受信
- RESTポーリング
- 差分検知
- 初回同期処理
- 再接続処理

### 完了条件
- 実市場データが取得できる
- 注文状態差分がイベント化される

---

## フェーズ9: ペーパートレード

### 内容
- 実API + Mock発注
- 長時間稼働テスト
- 再接続テスト
- 日中動作確認

### 完了条件
- 落ちても復旧できる
- ログで全状態追跡できる

---

## フェーズ10: 本番導入（限定）

### 内容
- live発注有効化
- 低ロット運用
- 銘柄限定
- 停止条件強化

### 完了条件
- 小規模で安全に取引できる
- 異常時に即停止可能

---

## 優先順位

1. 基盤
2. ドメイン
3. CSV外部データ
4. シグナル
5. 売買管理
6. 永続化
7. スナップショット
8. API接続
9. ペーパートレード
10. 本番

---

## 第1マイルストーン（最重要）

以下が完成すれば設計通りに動く基盤ができる。

- main + event_bus
- config読み込み
- CSV市場データ
- シグナル生成
- Mock売買
- 永続化

---

## 補足

- 本番API接続は最後に行う
- snapshot未実装での長時間運用は禁止
- trading_processの品質を最優先する
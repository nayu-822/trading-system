# Event Definition

## 概要

本システムはイベント駆動で構成されるため、  
イベント定義はシステムの中核となる。

すべてのプロセスはイベントを通じて通信する。

---

## 共通イベント構造

すべてのイベントは以下の構造を持つ。

- event_id: 一意ID
- event_type: イベント種別
- timestamp: 発生時刻
- source: 発行元プロセス
- symbol: 銘柄コード（任意）
- payload: イベントデータ本体
- sequence_no: 順序制御用

---

## イベント一覧

---

### 1. MarketDataUpdated

市場データ更新

payload:
- price
- bid
- ask
- volume
- timestamp

---

### 2. SignalDetected

シグナル検知

payload:
- signal_type（BUY / SELL / EXIT）
- strategy_type（trend / range）
- confidence（任意）
- indicators

---

### 3. OrderRequested

発注要求

payload:
- symbol
- side（BUY / SELL）
- quantity
- order_type
- price（任意）

---

### 4. OrderStatusUpdated

注文状態更新

payload:
- order_id
- status（NEW / PARTIAL / FILLED / CANCELED）
- filled_quantity
- remaining_quantity
- avg_price

重要：
- 差分で更新される
- 累積処理が必要

---

### 5. PositionUpdated

建玉更新

payload:
- symbol
- quantity
- avg_price
- realized_pnl
- unrealized_pnl

---

### 6. RiskUpdated

リスク状態更新

payload:
- current_exposure
- available_margin
- drawdown

---

### 7. LotUpdated

ロット状態更新

payload:
- current_lot
- win_streak
- lose_streak

---

### 8. SnapshotRequested

スナップショット要求

payload:
- target（trading / signal / etc）

---

### 9. SnapshotCreated

スナップショット完了

payload:
- snapshot_id
- path
- timestamp

---

### 10. ErrorOccurred

エラー通知

payload:
- error_type
- message
- stacktrace

---

## イベント設計ルール

- すべて immutable
- 冪等性を保つ
- 順序ズレを考慮する
- 重複処理に耐える
- dictではなく構造化モデルで扱う

---

## やってはいけない設計

- イベントにロジックを持たせる
- event_typeを文字列乱用する
- payloadを曖昧なdictにする
- 同一イベントで複数責務を持たせる

---

## 補足

イベントはログ・永続化・バックテスト・再現のすべての基盤となるため、  
変更時は慎重に扱うこと。
# Architecture

## 1. 概要
本システムは以下の3コンポーネントで構成される：
- trading-engine
- notification-service
- gui-tool

---

## 2. 全体構成

[VPS]
- trading-engine
- notification-service

[Windows]
- gui-tool

---

## 3. trading-engine

### 役割
- 売買ロジック実行
- 発注処理
- ログ保存
- バックテスト

### 内部構成
- data
- indicators
- strategies
- execution
- risk
- logging
- optimization

### データフロー
市場データ取得
→ 指標計算
→ シグナル生成
→ リスクチェック
→ 発注
→ ログ保存

---

## 4. notification-service

### 役割
- Discord通知

### 構成
producer → queue → consumer → Discord

### 特徴
- 完全非同期
- 売買処理と独立

---

## 5. gui-tool

### 役割
- ログ可視化
- パフォーマンス分析

### データ
- CSV
- SQLite

---

## 6. モード設計
paper / live 切替

---

## 7. スケジューリング
- cron制御
- 市場時間のみ稼働

---

## 8. ログ設計
- trade log
- system log

---

## 9. 非同期設計
- Queueベース
- 売買と通知分離

---

## 10. 拡張性
- 戦略追加
- DB変更
- API差し替え

---

## 11. 設計原則
- 疎結合
- 単一責務
- 再利用性
- テスト容易性

---

## 12. 制約
- 売買処理優先
- 通知非同期
- ログ必須
- 安全性最優先

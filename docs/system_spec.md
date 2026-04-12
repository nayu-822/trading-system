# 自動売買システム仕様書

---

## 1. 概要

本システムは以下の3つのプロジェクトで構成される。

1. 自動売買システム（VPS上で稼働）
2. 通知システム（Discord連携）
3. GUI分析ツール（Windows）

それぞれは疎結合に設計し、独立して開発・運用可能とする。

---

## 2. 全体構成

[VPS (Ubuntu)]
- trading-engine（自動売買）
- notification-service（Discord通知）

[Windows]
- gui-tool（可視化・分析）

---

## 3. プロジェクト構成

---

### 3.1 trading-engine（自動売買）

#### 目的
- 売買ロジックの実行
- 発注処理
- ログ保存
- バックテスト・パラメータ最適化

#### 機能要件
- Ubuntu上で動作
- cronにより稼働時間制御（日本市場時間のみ）
- トレンド戦略・レンジ戦略の両方に対応
- ペーパートレード / 実売買の切り替え機能
- ログ保存（取引ログ・システムログ）
- パラメータ自動調整機能
- バックテスト機能

#### フォルダ構成
trading-engine/
├── app/
├── logs/
├── tests/
└── requirements.txt

---

### 3.2 notification-service（通知システム）

#### 目的
- Discordへの通知送信
- 売買処理と完全に分離（非同期）

#### 機能要件
- Discord Webhook送信
- 非同期処理（Queue / 非同期I/O）
- 通知種類：
  - 約定
  - エラー
  - システム状態
  - 日次・月次サマリー

#### フォルダ構成
notification-service/
├── app/
├── logs/
└── requirements.txt

---

### 3.3 gui-tool（Windows GUI）

#### 目的
- ログの可視化
- パフォーマンス分析
- 戦略改善のためのデータ確認

#### 機能要件
- CSV / SQLiteからデータ読み込み
- 損益推移表示
- 日次・月次集計
- 戦略別パフォーマンス表示
- フィルタリング（銘柄・期間）

#### 技術スタック
- 言語：C#
- フレームワーク：WPF
- アーキテクチャ：MVVM
- .NET：8.0 以上

#### 設計方針
- GUIは売買ロジックを直接操作しない
- データはCSVまたはSQLiteから取得する
- View / ViewModel / Model を分離する
- ビジネスロジックはApplication層に配置する

#### ソリューション構成
gui-tool/
├── GuiTool.sln
├── src/
│   ├── GuiTool.Wpf/
│   ├── GuiTool.Application/
│   ├── GuiTool.Domain/
│   └── GuiTool.Infrastructure/
├── tests/
│   └── GuiTool.Tests/
└── data/

---

## 4. ログ設計

### 4.1 取引ログ
- timestamp
- symbol
- strategy（trend / range）
- action（buy / sell）
- price
- quantity
- pnl

### 4.2 システムログ
- timestamp
- level（INFO / ERROR）
- message

---

## 5. モード切替
- paper（ペーパートレード）
- live（実売買）
- 設定ファイル（settings.yaml）で管理

---

## 6. スケジューリング
- cronにより制御
- 日本株市場の取引時間のみ稼働

---

## 7. 非同期設計（重要）
- 売買処理と通知処理を完全分離
- 通知はQueue経由で送信
- 売買処理は通知の成功/失敗に依存しない

---

## 8. 拡張性
- 戦略の追加が容易
- executorの差し替えで他証券API対応可能
- DB変更（SQLite → PostgreSQL）可能

---

## 9. 非機能要件
- 高可用性
- 障害時の影響最小化
- ログ完全保存
- 長時間安定稼働

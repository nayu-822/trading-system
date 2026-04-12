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
```
trading-engine/
├── app/
│   ├── main.py
│   ├── config/
│   │   └── settings.yaml
│   ├── core/
│   │   ├── engine.py
│   │   ├── scheduler.py
│   │   └── state_manager.py
│   ├── strategies/
│   │   ├── trend/
│   │   │   └── trend_strategy.py
│   │   └── range/
│   │       └── range_strategy.py
│   ├── indicators/
│   │   └── indicators.py
│   ├── execution/
│   │   ├── order_executor.py
│   │   ├── paper_executor.py
│   │   └── live_executor.py
│   ├── risk/
│   │   └── risk_manager.py
│   ├── data/
│   │   ├── market_data.py
│   │   └── repository.py
│   ├── backtest/
│   │   └── backtest_engine.py
│   ├── optimization/
│   │   └── parameter_optimizer.py
│   ├── logging/
│   │   ├── trade_logger.py
│   │   └── system_logger.py
│   └── utils/
│       └── time_utils.py
├── logs/
│   ├── trades/
│   └── system/
├── tests/
└── requirements.txt
```

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
```
notification-service/
├── app/
│   ├── main.py
│   ├── client/
│   │   └── discord_client.py
│   ├── queue/
│   │   └── message_queue.py
│   ├── handlers/
│   │   └── notification_handler.py
│   └── models/
│       └── message.py
├── logs/
└── requirements.txt
```

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

#### 技術候補
- Streamlit
- PyQt（将来的）

#### フォルダ構成
```
gui-tool/
├── app/
│   ├── main.py
│   ├── ui/
│   │   ├── dashboard.py
│   │   └── components.py
│   ├── services/
│   │   └── data_loader.py
│   ├── analytics/
│   │   ├── performance.py
│   │   └── metrics.py
│   └── models/
│       └── trade.py
├── data/
└── requirements.txt
```

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

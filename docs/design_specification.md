# 株自動売買システム 設計仕様書（コード構成・責務・設定・復旧）

## 1. 目的

本ドキュメントはシステム仕様書をもとに、
コード構成・モジュール分割・ファイル単位の責務・設定ファイル設計・再起動復旧設計を定義する。

目的は以下とする。

- 責務分離を維持した実装
- テストしやすい構造
- 将来の拡張・差し替えを容易にする
- Mock / 本番 / バックテストの共通化
- 設定変更で対象銘柄や戦略を柔軟に変更可能にする
- プロセス再起動後も安全に状態復元できるようにする
- ファイルI/Oがメイン処理に影響しない構造を確保する

---

## 2. ディレクトリ構成

project/

  main.py

  config/
    app.yaml
    symbols.yaml
    strategy.yaml
    risk.yaml

  processes/
    external_data_process.py
    signal_process.py
    trading_process.py
    persistence_process.py
    snapshot_process.py

  domain/
    events.py
    models.py
    enums.py
    snapshots.py

  infrastructure/
    event_bus.py
    logger.py
    clock.py
    config_loader.py
    config_validator.py
    snapshot_store.py
    recovery_manager.py

  data_source/
    kabu_api_client.py
    rest_poller.py
    push_client.py
    csv_loader.py

  strategy/
    base_strategy.py
    range_strategy.py
    trend_strategy.py
    auto_strategy_selector.py
    indicators/

  trading/
    order_gateway.py
    order_manager.py
    position_manager.py
    lot_manager.py
    risk_manager.py
    trading_rules.py

  persistence/
    writer.py
    csv_writer.py
    sqlite_writer.py
    snapshot_writer.py

---

## 3. レイヤ構成と責務

## 3.1 processes

プロセス単位の責務分離を行う

- external_data_process  
  市場データ・注文状態の取得とイベント化

- signal_process  
  戦略判定とシグナル生成

- trading_process  
  売買判断・発注・状態管理（正本）

- persistence_process  
  イベント保存

- snapshot_process  
  スナップショット保存専用プロセス

---

## 3.2 domain

- イベント定義
- モデル定義
- 列挙型
- スナップショット構造

---

## 3.3 infrastructure

- イベント基盤
- ログ
- 時刻管理
- 設定管理
- スナップショット保存
- 復旧処理

---

## 4. 設定ファイル設計

## 4.1 設定ファイル一覧

config 配下に以下を配置する

- app.yaml（システム設定）
- symbols.yaml（銘柄設定）
- strategy.yaml（戦略設定）
- risk.yaml（リスク設定）

設定の読込・検証・マージは main が行う

---

## 4.2 app.yaml（システム設定）

### 責務
- 実行モード
- API設定
- ポーリング設定
- 永続化設定
- イベント設定
- スナップショット設定
- 復旧設定

### 主な項目
- mode
- rest_poll_interval_sec
- push_enabled
- snapshot_enabled
- snapshot_dir
- snapshot_interval_sec
- snapshot_max_generations
- snapshot_debounce_sec
- recovery_enabled
- recovery_mode
- startup_reconcile_enabled

---

## 4.3 symbols.yaml（銘柄設定）

### 責務
- 対象銘柄定義
- 資金配分
- 戦略選択
- 戦略上書き
- ロット補正

---

## 4.4 strategy.yaml（戦略設定）

### 責務
- 戦略の共通パラメータ定義
- trend / range / auto の基本設定

---

## 4.5 risk.yaml（リスク設定）

### 責務
- 売買停止条件
- 最大損失制御
- 連敗制御
- 市場時間制御

---

## 4.6 設定優先順位

1. symbols.yaml の override  
2. strategy.yaml の共通設定  
3. コード内デフォルト  

---

## 5. スナップショット設計（再起動復旧）

## 5.1 基本方針

各プロセスは復旧に必要な状態をスナップショットとして保持する。

ただし、保存処理はメイン処理をブロックしないよう
非同期で実行する。

---

## 5.2 保存方式

- 各プロセスはスナップショット要求イベントを発行する
- snapshot_process が保存処理を担当する
- 保存は非同期で行う
- メイン処理は書込完了を待たない

---

## 5.3 保存タイミング

- 状態更新時
- 一定時間ごと
- 重要イベント発生時（約定・建玉更新など）

---

## 5.4 保存形式

- JSON形式
- version を含める
- sequence_no を含める

---

## 5.5 保存の安全性

- 一時ファイルへ書込
- flush / fsync
- rename により原子的更新

---

## 6. プロセス別復旧設計

## 6.1 external_data_process

復元内容
- REST差分基準
- 銘柄リスト
- CSV再生位置

---

## 6.2 signal_process

復元内容
- 指標計算状態
- クールダウン状態
- 戦略内部状態

---

## 6.3 trading_process（最重要）

復元内容
- 注文状態
- 建玉状態
- 資金状態
- ロット状態
- 売買停止状態

復旧後処理
- RESTで注文状態再取得
- 建玉再取得
- 内部状態と再同期

---

## 6.4 persistence_process

復元内容
- 出力状態
- ファイル状態

---

## 6.5 snapshot_process

復元内容
- 世代管理情報

---

## 7. 復旧フロー

1. mainが異常終了検知
2. スナップショット存在確認
3. recovery_manager が復旧実行
4. 各プロセス状態復元
5. trading_process が外部再同期
6. 正常処理再開

---

## 8. 重要設計ルール

### 8.1 正本管理
- 注文・建玉 → trading
- 市場データ → external_data
- シグナル → strategy

---

### 8.2 非同期I/Oルール
- 業務処理内で同期ファイルI/Oは禁止
- 保存は snapshot_process 経由

---

### 8.3 復旧時制御
- 再同期完了まで新規発注禁止

---

## 9. まとめ

本設計では

- 設定
- データ取得
- 戦略
- 売買
- 保存
- 復旧

を明確に分離することで、

- 高い安全性
- 再現性
- 拡張性

を持つ自動売買システムを構築する。
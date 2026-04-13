# 開発状況レビューと今後の開発計画

対象リポジトリ: `nayu-822/trading-system`  
確認対象ブランチ: `develop`  
確認日: 2026-04-13

## 1. 要約

現状のリポジトリは、**`trading-engine` を中心に基盤実装とテストがかなり進んでいる一方で、`notification-service` と `gui-tool` は未着手に近い状態**です。リポジトリ直下には `docs` / `gui-tool` / `notification-service` / `trading-engine` があり、`develop` ブランチ上で 19 コミット確認できます。 :contentReference[oaicite:0]{index=0}

仕様書では 3 コンポーネント構成（`trading-engine` / `notification-service` / `gui-tool`）を前提とし、`trading-engine` には売買ロジック、発注抽象化、ログ保存、バックテスト、パラメータ最適化が求められています。さらに、トレンド戦略・レンジ戦略、paper / live 切替、バックテスト比較モード、最適化モードまで要求されています。 :contentReference[oaicite:1]{index=1}

コード確認ベースでは、`trading-engine` 側に **トレンド戦略、レンジ戦略、CSV/yfinance データ取得、paper executor、kabuステーション API クライアント、単発実行エンジン、バックテスト、最適化、CLI、売買ログ保存** まで実装が存在します。テストも `core / data / execution / strategies / backtest / optimization / logging` に分かれて用意されており、設計意図はかなり明確です。 :contentReference[oaicite:2]{index=2}

一方で、`gui-tool` と `notification-service` のディレクトリには `.gitignore` しかなく、**仕様にある Discord 通知基盤と GUI 分析ツールは未実装**です。また、セットアップ文書では依存ライブラリ一覧が未確定と書かれており、実際に `trading-engine/requirements.txt` は空です。つまり、**コアロジックは進んでいるが、運用・配布・接続・可視化の仕上げがこれから**という状態です。 :contentReference[oaicite:3]{index=3}

## 2. 現在の実装状況

### 2.1 仕様書・設計書
`docs` 配下には `architecture.md` / `coding_rules.md` / `setup.md` / `system_spec.md` があり、3 コンポーネント構成、ローカル Windows 前提、kabuステーション API 利用、GUI は Nuxt、通知は非同期 Queue ベースという方針が整理されています。 :contentReference[oaicite:4]{index=4}

### 2.2 trading-engine
`trading-engine/app` には `core` / `data` / `execution` / `strategies` があり、テスト側から `domain` / `logging` / `backtest` / `optimization` も参照されています。 `core/engine.py` では市場時間判定、データ取得、シグナル生成、注文実行、売買ログ保存までの最小フローが定義されています。 :contentReference[oaicite:5]{index=5}

#### 実装済みと確認できた主要機能
- **トレンド戦略**: 移動平均クロスによる `BUY / SELL / HOLD` 判定。 `short_window` / `long_window` のバリデーションあり。 :contentReference[oaicite:6]{index=6}
- **レンジ戦略**: RSI ベースの逆張り判定。 `rsi_period` / `lower_threshold` / `upper_threshold` を持ち、データ不足時は `HOLD` を返す設計。 :contentReference[oaicite:7]{index=7}
- **市場データ取得**: CSV 読み込みプロバイダ、ダミーデータ、yfinance 取得・正規化処理。仕様書の「yfinance / CSV」に対応する土台がある。 :contentReference[oaicite:8]{index=8}
- **注文実行抽象化**: `Executor` プロトコル、`PaperExecutor`、`KabuStationApiClient` がある。paper と live の責務分離方針に沿っている。 :contentReference[oaicite:9]{index=9}
- **売買ログ**: `TradeLog` / `TradeLogger` があり、CSV 追記保存を行う。 :contentReference[oaicite:10]{index=10}
- **バックテスト**: `BacktestEngine` があり、逐次処理・単一ポジション前提・損益/勝率/連勝連敗/最大ドローダウン集計まで実装されている。 :contentReference[oaicite:11]{index=11}
- **比較モード / 最適化モード**: `run_backtest.py` に単体・比較・最適化の各実行経路があり、CLI 引数として `--compare` と `--optimize` を持つ。 `ParameterOptimizer` でグリッドサーチが実装されている。 :contentReference[oaicite:12]{index=12}
- **テスト群**: `core / data / execution / strategies / backtest / optimization / logging` にまとまっている。未完成の試作段階ではなく、モジュール単位で作り込む方針が見える。 :contentReference[oaicite:13]{index=13}

### 2.3 notification-service
ディレクトリは存在するが、中身は `.gitignore` のみです。仕様にある Discord Webhook 通知、Queue、非同期 consumer は未実装です。 :contentReference[oaicite:14]{index=14}

### 2.4 gui-tool
ディレクトリは存在するが、中身は `.gitignore` のみです。仕様にある Nuxt + TypeScript のローカル Web UI、CSV / SQLite 読み込み、損益推移表示、比較表示は未実装です。 :contentReference[oaicite:15]{index=15}

## 3. 現在の完成度評価

### 3.1 進んでいる点
- 仕様のうち、**売買ロジックのコア**はかなり前進している
- paper 実行、戦略分離、バックテスト、最適化まで一連の流れが見えている
- テストの粒度が比較的良く、将来の改修基盤がある
- ドキュメントが先に整理されており、設計意図がブレにくい

### 3.2 未完了・弱い点
- **live 発注 executor が未完成**  
  `KabuStationApiClient` はあるが、`Order` を実際の発注 payload に変換して送る live executor が見当たりません。paper / live 切替の完成にはここが必要です。 :contentReference[oaicite:16]{index=16}
- **通常売買用の起動入口が弱い**  
  バックテスト CLI はある一方、通常売買を実行する明確な `main` / runner / mode 切替エントリーポイントは確認しづらく、運用フローがまだ固まっていない印象です。 :contentReference[oaicite:17]{index=17}
- **依存関係管理が未整備**  
  `requirements.txt` が空で、`setup.md` でも依存一覧未確定と明記されています。再現性あるセットアップには未到達です。 :contentReference[oaicite:18]{index=18}
- **notification-service / gui-tool 未着手**
- **CI/CD や実行品質保証の情報が薄い**  
  テストはあるが、自動実行の仕組みまでは確認できませんでした。 :contentReference[oaicite:19]{index=19}

## 4. いまの時点での判断

現状は、**「売買システムの研究開発フェーズ後半」** です。  
つまり以下の状態です。

- 戦略・バックテスト・最適化の基盤はできている
- paper 寄りの検証には進める
- ただし **本番運用に必要な live 実行・安全装置・通知・可視化・環境整備は未完成**

したがって、**次に目指すべきは GUI ではなく、まず `trading-engine` の運用完成度を上げること**です。

## 5. 今後の開発優先計画

## フェーズ1: trading-engine を paper / live 運用可能な形に仕上げる
最優先です。

### 目的
バックテスト専用コードから、通常売買でも安全に動く実行系へ進める。

### 実装項目
1. **設定管理の導入**
   - `TRADING_MODE=paper/live`
   - API パスワード
   - 銘柄
   - 数量
   - 戦略種別
   - ログパス
   - 市場コードなど

2. **通常売買用 runner / main の作成**
   - 1回実行
   - 連続実行
   - 市場時間外スキップ
   - 例外時ログ出力

3. **live executor 実装**
   - `Order` → kabuステーション API payload 変換
   - 成行 / 指値方針の明確化
   - レスポンスを `ExecutionResult` に変換
   - 発注失敗時のエラー整形

4. **安全機構**
   - live 起動時の明示確認
   - 最大数量制限
   - 許可銘柄制限
   - API 未接続時停止
   - 市場時間外停止
   - 連続発注防止

5. **通常売買テスト**
   - mode 切替
   - live executor の payload テスト
   - 市場時間判定
   - 例外時安全停止

### 完了条件
- `paper` モードで通常売買 runner が動く
- `live` モードで発注前チェックが通る
- 発注 payload 生成とレスポンス処理がテストで担保される

## フェーズ2: trading-engine の品質を上げる
### 目的
実運用に耐える保守性と検証性を作る。

### 実装項目
1. **依存関係管理の確定**
   - `requirements.txt` または `pyproject.toml` 整備
   - `yfinance`, `pandas`, `pytest` など明文化

2. **ログの整理**
   - system log と trade log の出力先分離
   - ローテーション
   - 実行 run_id 付与

3. **バックテスト結果保存**
   - JSON / CSV 保存
   - 比較結果・最適化結果の永続化

4. **戦略ファクトリ整理**
   - trend / range 追加に備えた共通 factory 化

5. **ドキュメント更新**
   - 実装済み機能一覧
   - 実行手順
   - モード別フロー
   - バックテスト使用例

### 完了条件
- 新規環境で再現可能
- バックテスト結果が保存できる
- 新しい戦略追加の導線が整理される

## フェーズ3: notification-service を実装
### 目的
売買から分離した通知基盤を完成させる。

### 実装項目
1. Discord Webhook 送信クライアント
2. Queue ベースの producer / consumer
3. 売買成功 / 失敗 / 例外通知
4. リトライと失敗時ローカル退避
5. trading-engine 側との疎結合連携

### 完了条件
- 売買処理をブロックせず通知送信できる
- 通知失敗でも売買処理へ影響しない

## フェーズ4: gui-tool を実装
### 目的
バックテスト結果と取引ログの閲覧性を上げる。

### 実装項目
1. Nuxt 初期構築
2. CSV / SQLite ローダー
3. 損益推移グラフ
4. 日次 / 月次集計
5. 戦略別比較
6. 銘柄・期間・戦略フィルタ

### 完了条件
- バックテスト結果をブラウザで比較表示できる
- trade log を時系列で確認できる

## 6. 推奨する実装順

以下の順番を推奨します。

1. **通常売買 runner + mode 切替 + 設定管理**
2. **live executor + 安全機構**
3. **依存関係整理 + ログ整備**
4. **バックテスト結果保存**
5. **notification-service**
6. **gui-tool**

理由は、今の資産価値が最も高いのは `trading-engine` であり、ここを完成させると paper 検証・live 接続・通知連携・GUI 連携のすべての土台になるためです。

## 7. 次に着手すべき具体タスク

直近スプリントでは、以下の 5 件を推奨します。

### タスク1
**`trading-engine` に通常売買用 CLI エントリーポイントを追加する**

### タスク2
**設定ファイル / 環境変数ベースで `paper/live` を切り替えられるようにする**

### タスク3
**`KabuStationApiClient` を利用する live executor を実装する**

### タスク4
**live 用の安全チェック（銘柄、数量、時間帯、接続確認）を追加する**

### タスク5
**依存ライブラリとセットアップ手順を実装に合わせて確定する**

## 8. 補足コメント

- 仕様と実装のズレはまだ大きくありません。むしろ **仕様先行で、trading-engine がそこに追いついてきている状態**です。 :contentReference[oaicite:20]{index=20}
- 特にバックテスト比較と最適化まで進んでいるのは強みです。ここは後回しではなく、**通常売買系ときれいにつなぐ**のが重要です。 :contentReference[oaicite:21]{index=21}
- `gui-tool` を先に触りたくなる段階ですが、現時点では **通知よりさらに後** が妥当です。まずは engine 側の運用完成度が先です。

## 9. 結論

このプロジェクトは現状、**自動売買のコア基盤はかなり良い形で育っている**一方、**実運用に必要な周辺機能は未完成**です。  
したがって今後の中心課題は、**`trading-engine` を「バックテストできるコード」から「paper/live で安全に動くアプリケーション」へ進化させること**です。

その後に `notification-service`、最後に `gui-tool` を進めるのが、もっとも自然で失敗しにくい開発順です。
# セットアップ手順

## 1. このプロジェクトの構成概要

本リポジトリは以下の 3 コンポーネントで構成する。

- `trading-engine`：Windows ローカルPC上で動作する自動売買システム
- `notification-service`：Discord 通知を非同期で送信するサービス
- `gui-tool`：Nuxt で構築するローカル Web UI の確認ツール

今回の前提では、三菱UFJ eスマート証券の `kabuステーション API` を利用するため、VPS は使わない。`kabuステーション` を起動した Windows ローカルPC 上で `trading-engine` を実行する。

## 2. 実行構成

### 2.1 ローカルPC側

- `trading-engine`
- `notification-service`
- `gui-tool`
- `kabuステーション`

### 2.2 設計上の前提

- 売買ロジックは `trading-engine` に集約する
- Discord 通知は `notification-service` で非同期実行する
- GUI は売買ロジックを直接操作しない
- GUI は CSV または SQLite を読み込んで表示する
- GUI はブラウザで HTML として確認する

## 3. Windows 側のセットアップ手順

### 3.1 必要ツール

- Windows 11
- pyenv-win
- Python 3.12.8
- `pip`
- `venv`
- Volta
- Node.js
- pnpm
- `kabuステーション`

確認コマンド:

```powershell
pyenv --version
python --version
volta --version
node --version
pnpm --version
```

### 3.2 Python バージョン管理方針

Python は `pyenv-win` で管理し、リポジトリルートの `.python-version` で固定する。

このリポジトリで固定するバージョン:

```text
3.12.8
```

導入例:

```powershell
winget install pyenv-win.pyenv-win
pyenv install 3.12.8
pyenv local 3.12.8
python --version
```

補足:

- 他プロジェクトでは別の `.python-version` を置くことで切り替えできる
- 本リポジトリでは `trading-engine` と `notification-service` で同じ Python バージョンを前提とする
- Python のパッチバージョンを変更する場合は `.python-version` と `docs/setup.md` を同時に更新する

### 3.3 Python 仮想環境の作成方法

`trading-engine`:

```powershell
cd trading-engine
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

`notification-service`:

```powershell
cd notification-service
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

### 3.4 必要な依存関係のインストール方法

現時点では依存ライブラリ一覧が未確定のため、各プロジェクトに `requirements.txt` または `pyproject.toml` を追加後、以下の形式で導入する。

```powershell
pip install -r requirements.txt
```

または

```powershell
pip install .
```

### 3.5 kabuステーション の前提

- `kabuステーション` をローカルPCにインストールする
- 売買実行前に `kabuステーション` を起動しておく
- API 利用設定、API パスワード、接続先モードは証券会社の設定に従う
- `trading-engine` は `kabuステーション` 未起動時に安全に停止できる設計とする

### 3.6 環境変数の設定方法

`.env.example` が追加された場合は `.env` を作成し、以下のような情報を管理する。

- `KABUSTATION_API_PASSWORD`
- `TRADING_MODE`
- `DISCORD_WEBHOOK_URL`
- `LOG_LEVEL`

機密情報は Git 管理しない。

### 3.7 実行制御方針

VPS の `cron` は利用しない。市場時間に合わせた起動制御は、以下のいずれかで行う。

- Windows タスクスケジューラ
- 常駐プロセス内の市場時間判定

市場時間制御は売買ロジックに密結合させず、共通モジュールまたは起動制御で管理する。

## 4. GUI のセットアップ手順

### 4.1 Node.js / Volta の導入手順

Node.js 系ツールは Volta でバージョン管理する。

インストール例:

```powershell
winget install Volta.Volta
volta install node@22
volta install pnpm@10
```

確認コマンド:

```powershell
volta --version
node --version
pnpm --version
```

### 4.2 GUI の前提

- フレームワークは Nuxt
- 言語は TypeScript を基本とする
- ローカル実行の Web UI として動作する
- GUI は CSV / SQLite を読み込んで表示する
- 売買ロジックや `kabuステーション` を GUI から直接操作しない
- ブラウザで HTML として閲覧する

### 4.3 GUI のセットアップ例

```powershell
cd gui-tool
pnpm install
pnpm dev
```

ブラウザで `http://localhost:3000` を開いて確認する。

### 4.4 ソリューション構成方針

```text
gui-tool/
├── app/
├── components/
├── composables/
├── pages/
├── server/
│   ├── api/
│   └── services/
├── public/
├── tests/
├── package.json
├── pnpm-lock.yaml
└── nuxt.config.ts
└── data/
```

## 5. Node.js バージョン管理方針

`gui-tool` で Node.js を利用するため、Volta の導入を必須とする。

方針:

- Node.js は Volta で固定する
- `pnpm` をパッケージマネージャとして使用する
- バージョンは `package.json` と Volta 設定で明示する

## 6. 実行確認コマンド

Windows:

```powershell
pyenv --version
python --version
python -m venv --help
volta --version
node --version
pnpm --version
```

運用前確認:

- `kabuステーション` が起動していること
- API 利用設定が有効であること
- GUI が CSV / SQLite 前提で設計されていること
- `pnpm dev` でローカル画面を起動できること

## 7. よくある注意点

- `kabuステーション` を起動していないと売買 API は利用できない
- ローカルPCをスリープさせると自動売買は停止する
- GUI は表示専用とし、売買処理へ直接接続しない
- `paper` / `live` の切替は設定ファイルまたは環境変数で管理する
- 通知処理は売買処理の成功可否に依存させない
- GUI 用の Node.js / pnpm バージョンは Volta で固定する

## 8. 未確認事項

- `kabuステーション API` の正式な接続方式と認証手順の詳細
- `trading-engine` / `notification-service` の依存ライブラリ一覧
- Windows タスクスケジューラと常駐方式のどちらを採用するか
- ローカルPC障害時の再起動・復旧方針
- GUI で採用するグラフライブラリ
## 9. trading-engine 通常売買 runner

### 9.1 エントリーポイント

通常売買は `trading-engine/app/run_trading.py` から起動する。
互換のため `trading-engine/app/main.py` も同じ runner を呼び出す。

```powershell
cd trading-engine
python app/run_trading.py --settings app/config/settings.yaml
```

### 9.2 設定ファイル

サンプルは `trading-engine/app/config/settings.yaml.example` を使う。
秘匿値はサンプルへ直書きせず、環境変数で上書きする。

主な設定項目:

- `symbol`
- `mode`
- `strategy`
- `quantity`
- `system_log_path`
- `trade_log_path`
- `trend_short_window`
- `trend_long_window`
- `rsi_period`
- `rsi_lower`
- `rsi_upper`
- `api_host`
- `api_port`
- `api_timeout_seconds`
- `api_exchange`
- `api_password`
- `api_token`

### 9.3 環境変数での上書き

runner は設定ファイルより環境変数を優先する。

- `TRADING_SYMBOL`
- `TRADING_MODE`
- `TRADING_STRATEGY`
- `TRADING_QUANTITY`
- `TRADING_SYSTEM_LOG_PATH`
- `TRADING_TRADE_LOG_PATH`
- `TREND_SHORT_WINDOW`
- `TREND_LONG_WINDOW`
- `RANGE_RSI_PERIOD`
- `RANGE_RSI_LOWER`
- `RANGE_RSI_UPPER`
- `KABU_API_HOST`
- `KABU_API_PORT`
- `KABU_API_TIMEOUT_SECONDS`
- `KABU_API_EXCHANGE`
- `KABU_API_PASSWORD`
- `KABU_API_TOKEN`

### 9.4 paper / live の切替

- `mode: paper`
  `PaperExecutor` を使って通常売買フローを実行する。
- `mode: live`
  execution 層で `LiveExecutor` へ切り替わる。
  現時点では API 接続設定と差し替えポイントまで実装済みで、実際の注文 payload 生成は未実装。

### 9.5 安全チェック

- `symbol` 未設定時は停止する
- `quantity <= 0` の場合は停止する
- `live` モードでは市場時間外に停止する
- `live` モードでは `api_exchange` と `api_password` または `api_token` が必須
- 例外は握りつぶさず system log に記録する

### 9.6 ログ

- system log: `system_log_path` または既定値 `logs/system/trading_runner.log`
- trade log: `trade_log_path` または既定値 `logs/trades/trade_log.csv`

runner の開始・終了・スキップ・異常終了は system log に残る。
約定結果は trade log に CSV で残る。
## 10. trading-engine 通常売買 runner 改訂

### 10.1 実行方法

```powershell
cd trading-engine
python app/run_trading.py --settings app/config/settings.yaml
```

### 10.2 設定ファイル

既定設定は `trading-engine/app/config/settings.yaml` を使う。
サンプルは `trading-engine/app/config/settings.yaml.example` を参照する。

主な設定項目:

- `symbol`
- `mode`
- `strategy`
- `quantity`
- `data_source`
- `csv_path`
- `yfinance_period`
- `yfinance_interval`
- `trend_short_window`
- `trend_long_window`
- `rsi_period`
- `rsi_lower`
- `rsi_upper`
- `log_path`
- `system_log_path`
- `api_host`
- `api_port`
- `api_timeout_seconds`
- `api_exchange`
- `api_password`
- `api_token`

### 10.3 data_source

- `dummy`
  テスト用のダミー OHLCV を返す。
- `csv`
  `csv_path` で指定した CSV を使う。
- `yfinance`
  `yfinance_period` と `yfinance_interval` を使って取得する。

### 10.4 市場時間判定

既定の市場時間判定は日本株の場中に合わせる。

- 前場: `09:00` から `11:30`
- 昼休み: 取引不可
- 後場: `12:30` から `15:30`
- 土日: 停止

### 10.5 paper / live

- `mode: paper`
  `PaperExecutor` を使って通常売買フローを実行する。
- `mode: live`
  現時点では未対応。設定検証時に明示エラーで停止する。

### 10.6 ログ

- system log: `system_log_path` または既定値 `logs/system/trading_runner.log`
- trade log: `log_path` または既定値 `logs/trades/trade_log.csv`

### 10.7 依存ライブラリ

設定ファイルの読込には `PyYAML` が必要。

```powershell
cd trading-engine
pip install -r requirements.txt
```

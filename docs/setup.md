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
- Python 3.12 系
- `pip`
- `venv`
- Volta
- Node.js
- pnpm
- `kabuステーション`

確認コマンド:

```powershell
python --version
volta --version
node --version
pnpm --version
```

### 3.2 Python 仮想環境の作成方法

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

### 3.3 必要な依存関係のインストール方法

現時点では依存ライブラリ一覧が未確定のため、各プロジェクトに `requirements.txt` または `pyproject.toml` を追加後、以下の形式で導入する。

```powershell
pip install -r requirements.txt
```

または

```powershell
pip install .
```

### 3.4 kabuステーション の前提

- `kabuステーション` をローカルPCにインストールする
- 売買実行前に `kabuステーション` を起動しておく
- API 利用設定、API パスワード、接続先モードは証券会社の設定に従う
- `trading-engine` は `kabuステーション` 未起動時に安全に停止できる設計とする

### 3.5 環境変数の設定方法

`.env.example` が追加された場合は `.env` を作成し、以下のような情報を管理する。

- `KABUSTATION_API_PASSWORD`
- `TRADING_MODE`
- `DISCORD_WEBHOOK_URL`
- `LOG_LEVEL`

機密情報は Git 管理しない。

### 3.6 実行制御方針

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

## 5. Node.js / Volta の導入手順

`gui-tool` で Node.js を利用するため、Volta の導入を必須とする。

方針:

- Node.js は Volta で固定する
- `pnpm` をパッケージマネージャとして使用する
- バージョンは `package.json` と Volta 設定で明示する

## 6. 実行確認コマンド

Windows:

```powershell
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

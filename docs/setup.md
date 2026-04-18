# セットアップ手順

## Python 環境

本リポジトリでは Python 3.12 系と Pipenv を使用する。

仮想環境はリポジトリ直下には作成せず、`.venv` ファイルで以下の相対パスを参照する。

```text
..\..\python\trading-system
```

このパスは本リポジトリから見て、以下に解決される。

```text
C:\Users\nakat\OneDrive\ドキュメント\python\trading-system
```

## 初回構築

```powershell
pipenv install
```

`pipenv` が PATH にない場合は、Python モジュールとして実行する。

```powershell
python -m pipenv install
```

## 仮想環境の確認

```powershell
pipenv --venv
pipenv run python --version
```

## パッケージ追加

実行時依存を追加する場合は以下を使用する。

```powershell
pipenv install <package-name>
```

開発時のみ使用する依存を追加する場合は以下を使用する。

```powershell
pipenv install --dev <package-name>
```

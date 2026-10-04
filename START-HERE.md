# START HERE — Berth

Berth をとにかく起動するための最短ガイドです。以下の通常コマンドは macOS Apple Silicon の参照環境向けです。Windows WSL2 / Ubuntu 24.04 では [Linux / WSL2 ガイド](docs/oss/linux-wsl.md) を使用してください。

## 1. 必要なものを入れる

macOS Apple Silicon には Git、Podman、k3d、kubectl をインストールしてください。Windows では Windows 11、WSL2、Ubuntu 24.04、Git を用意し、[Linux / WSL2 ガイド](docs/oss/linux-wsl.md) に従ってください。

## 2. ダウンロードする

```bash
git clone https://github.com/tohaya-dev/berth.git
cd berth
```

Git を使わない場合は、GitHub の **Code** → **Download ZIP** を選び、ZIP を展開して、その `berth` フォルダでターミナルを開きます。

## 3. 起動する

```bash
./ops/status.sh
./ops/start.sh
```

## 4. ブラウザで開く

`http://127.0.0.1:18765` を開きます。

## 5. 確認する

```bash
./ops/verify.sh
```

コマンドが正常終了し、ブラウザで上の URL を開ければ完了です。

## 6. 停止する

```bash
./ops/stop.sh
```

## 7. 困ったとき

- macOS: [トラブルシューティング](docs/oss/troubleshooting.md)
- Windows / WSL2: [Linux / WSL2 ガイド](docs/oss/linux-wsl.md)
- 詳細な起動と復旧: [STARTUP.md](STARTUP.md)

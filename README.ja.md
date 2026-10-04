# Berth

Berth は Kubernetes-native な Cynovela runtime です。
ローカル環境で RAG、guardrail、provider switching、API worker、PostgreSQL + pgvector、Redis、CLI、HTTP MCP を扱うための実行基盤です。

回答生成 LLM そのものは同梱しません。
LM Studio、Ollama、OpenAI 互換 API などを外部 provider として接続します。

Berth `v1.0.0-ga` は現在の公開 GA ラインです。
**参照ローカル環境:** macOS Apple Silicon + Podman + k3d。
**ドキュメント化済みの Windows/Linux 検証経路:** Windows WSL2 + Ubuntu 24.04。
**runtime contract:** Kubernetes / Linux workloads。

## 最初にここを読んでください

初めて使う場合は、この順番で進めてください。

### 1. 必要なものを入れる

**macOS Apple Silicon:** Git、Podman、k3d、kubectl。
**Windows:** Windows 11、WSL2、Ubuntu 24.04、Git。起動前に [Linux / WSL2 ガイド](docs/oss/linux-wsl.md) を読んでください。

### 2. Berth をダウンロードする

```bash
git clone https://github.com/tohaya-dev/berth.git
cd berth
```

Git を使わない場合は、GitHub ページ右上の **Code** → **Download ZIP** からダウンロードしてください。ZIP を展開したあと、展開した `berth` フォルダでターミナルを開いてください。

### 3. Berth を起動する
```bash
./ops/status.sh
./ops/start.sh
./ops/verify.sh
```

### 4. ブラウザで開く
次の URL を開いてください: `http://127.0.0.1:18765`

`status.sh` は現在状態の表示、`start.sh` は起動、`verify.sh` は動作確認を行います。

### 5. うまく動いたか確認する

`./ops/verify.sh` が正常終了し、ブラウザで `http://127.0.0.1:18765` を開ければ起動完了です。失敗する場合は [トラブルシューティング](docs/oss/troubleshooting.md) を確認してください。

### 6. 停止する

```bash
./ops/stop.sh
```

### 7. 詳しいガイド

- [START-HERE.md](START-HERE.md)
- [English README](README.md)
- [English Start Here](START-HERE.en.md)
- [Linux / WSL2 ガイド](docs/oss/linux-wsl.md)
- [Security](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## Berth が動かすもの

Berth は次の runtime service を Kubernetes 上で動かします。

- API と worker の replica
- PostgreSQL + pgvector
- Redis
- RAG と guardrail 処理
- provider switching
- CLI と HTTP MCP

回答生成 model は Berth の外部に置きます。runtime の起動後に、LM Studio、Ollama、または OpenAI 互換 endpoint を設定してください。

## サポート済みの入口

repository root で次の script を実行します。

```bash
./ops/status.sh
./ops/start.sh
./ops/verify.sh
./ops/restart.sh
./ops/stop.sh
```

上のコマンドは macOS の参照ローカル環境向けです。Windows WSL2 / Ubuntu 24.04 では [Linux / WSL2 ガイド](docs/oss/linux-wsl.md) に記載された環境とコマンドを使用してください。

## ドキュメント

- [起動と復旧](STARTUP.md)
- [architecture](docs/architecture.md)
- [operations](docs/operations.md)
- [CLI / MCP](docs/CLI-MCP.md)
- [system requirements](docs/oss/requirements.md)
- [troubleshooting](docs/oss/troubleshooting.md)
- [update / uninstall](docs/oss/uninstall-upgrade.md)
- [GA release notes](docs/release-notes-v1.0.0-ga.md)

## License

Berth の source code は MIT License です。依存 package、model、container base image にはそれぞれの license が適用されます。詳細は [Third-party notices](THIRD_PARTY_NOTICES.md) を参照してください。

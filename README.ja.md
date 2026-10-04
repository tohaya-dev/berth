# Berth

Berth は Cynovela の Kubernetes ベース実行基盤です。ローカル Kubernetes 環境で RAG、guardrail、provider switching、API worker、PostgreSQL + pgvector、Redis、CLI、HTTP MCP を動かします。

回答生成 LLM そのものは同梱しません。LM Studio、Ollama、OpenAI 互換 API などを外部 provider として接続します。

Berth `v1.0.0-ga` は現在の公開 GA ラインです。runtime contract は Kubernetes / Linux workloads です。

## 最短起動

```bash
git clone https://github.com/tohaya-dev/berth.git
cd berth
./ops/status.sh
./ops/start.sh
./ops/verify.sh
```

ブラウザで `http://127.0.0.1:18765` を開いてください。`./ops/verify.sh` が正常終了し、URL を開ければ起動完了です。

## システム要件

- macOS Apple Silicon + Podman + k3d
- または Windows / WSL2 Ubuntu 24.04
- または Windows / WSL2 Rocky 9
- kubectl
- ローカルKubernetes環境

上記 macOS 構成が参照ローカル環境です。Windows では [Linux / WSL2 ガイド](docs/oss/linux-wsl.md) に従ってください。

Git を使わない場合は、GitHub の **Code** → **Download ZIP** を選び、ZIP を展開して、その `berth` フォルダでターミナルを開いてください。

## Berth を停止する

```bash
./ops/stop.sh
```

## Berth が動かすもの

- API と worker の replica
- PostgreSQL + pgvector
- Redis
- RAG と guardrail 処理
- provider switching
- CLI と HTTP MCP

回答生成 model は Berth の外部に置きます。runtime の起動後に、LM Studio、Ollama、または OpenAI 互換 API を設定してください。

## 詳しいガイド

- [English README](README.md)
- [START-HERE.md](START-HERE.md)
- [English Start Here](START-HERE.en.md)
- [Linux / WSL2 ガイド](docs/oss/linux-wsl.md)
- [起動と復旧](STARTUP.md)
- [operations](docs/operations.md)
- [CLI / MCP](docs/CLI-MCP.md)
- [Security](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

Berth の source code は MIT License です。依存 package、model、container base image にはそれぞれの license が適用されます。詳細は [Third-party notices](THIRD_PARTY_NOTICES.md) を参照してください。

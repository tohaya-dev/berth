# Berth

Berth は、PostgreSQL/pgvector、Redis、複数のAPI・worker replicaを使用する
Kubernetes-nativeなRAG runtimeです。回答生成LLMは同梱せず、LM Studio、Ollama、
またはOpenAI互換endpointへ接続します。

## 対応状況

- macOS Apple Silicon上のPodman + k3d: 検証済み参照環境
- Windows 11 / WSL2 Ubuntu 24.04 amd64: 検証済み
- Windows 11 / WSL2 Rocky Linux 9 amd64: 検証済み
- Native Linux: 未検証。一般的なLinux GA対応とは表記しません

この公開候補はsource配布用です。生成LLM model、実データ、database、秘密情報、
private Git履歴は含みません。binary/container imageの再配布前には、
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) に記載した依存関係と脆弱性の
確認が必要です。

## 最初に読む文書

- [`START-HERE.md`](START-HERE.md): 構成と日常操作
- [`docs/quickstart.md`](docs/quickstart.md): Quick Start
- [`docs/architecture.md`](docs/architecture.md): architecture
- [`docs/deployment.md`](docs/deployment.md): deployment
- [`docs/operations.md`](docs/operations.md): 起動、更新、backup/restore
- [`docs/oss/requirements.md`](docs/oss/requirements.md): system requirements
- [`docs/oss/troubleshooting.md`](docs/oss/troubleshooting.md): troubleshooting
- [`docs/oss/uninstall-upgrade.md`](docs/oss/uninstall-upgrade.md): update / uninstall
- [`docs/oss/before-distributing.md`](docs/oss/before-distributing.md): 配布前checklist
- [`docs/CLI-MCP.md`](docs/CLI-MCP.md): CLI / API / MCP
- [`docs/known-limitations.md`](docs/known-limitations.md): 制限事項
- [`SECURITY.md`](SECURITY.md): security policy
- [`docs/oss/model-policy.md`](docs/oss/model-policy.md): model policy

## 基本確認

repository rootで以下を実行します。

```bash
./ops/status.sh
./ops/start.sh
./ops/verify.sh
```

既定のローカルendpointは `http://127.0.0.1:18765` です。別環境へ展開するときは、
個人環境の絶対pathや既存databaseをコピーせず、設定例とfresh bootstrapを使用してください。

## License

BerthのsourceはMIT Licenseです。依存package、model、container base imageにはそれぞれの
licenseが適用されます。詳細は [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) を参照してください。

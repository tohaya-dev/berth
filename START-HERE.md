# START HERE — Berth

この文書を最初の入口にします。Berth は **Kubernetes-native Cynovela runtime** です。
Podman は現在の macOS 参照環境で k3d/Kubernetes を動かすためのホスト側基盤であり、Berth 自体の実行契約は Kubernetes です。

## 1. まず理解する構成

```text
host OS
  ↓
container substrate (reference: Podman)
  ↓
k3d / Linux Kubernetes
  ↓
Berth
  ├─ API replicas
  ├─ worker replicas
  ├─ PostgreSQL + pgvector
  └─ Redis
```

回答生成 LLM は Berth に同梱しません。LM Studio / Ollama / OpenAI-compatible endpoint 等を外部 provider として接続します。
一方、Berth 自身が RAG/guardrail に必要とする embedding / reranker / PII/NER 系のローカルモデルは、portable bundle では bundle 内 `models/` に保持する方針です。

## 2. 日常の入口

リポジトリまたは展開済み portable tree の root で実行します。

```bash
./ops/status.sh
./ops/start.sh
./ops/verify.sh
```

既定のローカル入口は `http://127.0.0.1:18765` です。

`ops/start.sh` は、必要に応じて container substrate と k3d cluster を復帰させ、PostgreSQL/pgvector、Redis、API、worker を待ち、最後に verification を行うことを目的としたサポート済み入口です。

## 3. 停止と再起動

```bash
./ops/stop.sh
./ops/start.sh
```

再起動だけなら:

```bash
./ops/restart.sh
```

Mac/host の再起動後も、まず `./ops/start.sh`、続けて `./ops/verify.sh` を使います。

## 4. 初回・別マシンへの展開

Berth portable distribution の設計正本は [`docs/portable-distribution.md`](docs/portable-distribution.md) です。

Portable 版の目標は、元の Mac の以下へ依存しないことです。

- 個人home directoryへ固定された絶対パス
- Chewie の `store/models` への参照
- 元マシンにだけ存在する Podman image cache
- 固定 host IP
- 日付入り cluster 名
- 元マシンの実データ

Portable bundle は、Kubernetes manifests、必要 OCI images、Berth 必須 RAG/guardrail models、設定テンプレート、検証スクリプトを bundle-relative に解決できる形へ整備します。

## 5. Fresh deploy / restore

現行 GA では fresh scratch deploy と PostgreSQL restore の運用経路があります。

```bash
./deploy/k8s/rebuild-all.sh --recreate
./deploy/k8s/pg-backup.sh backup
./deploy/k8s/pg-backup.sh verify <dump-file>
```

Portable 化では `HAN_SOLO_MODELS_DIR` を元マシンの絶対パスへ向けず、展開された bundle 内の `models/` を使うことを必須にします。

## 6. CLI

まず Berth の URL を指定します。

```bash
export CYNOVELA_URL=http://127.0.0.1:18765
python3 cynovela_cli.py status
python3 cynovela_cli.py health
python3 cynovela_cli.py login --username cynovela
```

詳しくは [`docs/CLI-MCP.md`](docs/CLI-MCP.md) を参照してください。

## 7. MCP

Berth の分散構成では **HTTP MCP** を優先します。

- RPC: `/api/mcp/rpc`
- fingerprint: `/api/mcp/fingerprint`
- auth: JWT または用途限定 API key (`cyn_...`)

Kyber 等の外部連携では、管理者 JWT の常用ではなく、workspace / collection / tool を絞った用途限定 API key を基本にします。

詳しくは [`docs/CLI-MCP.md`](docs/CLI-MCP.md) を参照してください。

## 8. Platform portability

優先順位は次の通りです。

1. macOS Apple Silicon / arm64 — 現在の参照環境
2. Linux arm64
3. Linux amd64 — multi-arch application image 化後
4. Windows host 上の Linux Kubernetes 環境 — 将来ターゲット

Windows container そのものを Berth の必須条件にはしません。Berth の Linux/Kubernetes runtime contract を保ったまま host OS の選択肢を広げる方針です。

## 9. Vendor neutrality

Portable artifact には、過去のテスト用 vendor/product 固有 fixture を含めません。
Source repo にテスト目的で残す場合でも、portable staging から除外し、neutrality/path/secret scan を通過したものだけを最終 bundle とします。

## 10. 何を読めばよいか

- 最初の入口: `START-HERE.md`（この文書）
- 起動詳細: `STARTUP.md`
- portable / backup / platform portability: `docs/portable-distribution.md`
- CLI / MCP / Kyber連携: `docs/CLI-MCP.md`
- 運用: `docs/operations.md`
- GA release: `docs/release-notes-v1.0.0-ga.md`
- security: `SECURITY.md`

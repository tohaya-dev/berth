# Berth Startup / Recovery Guide

最初に読む文書は [`START-HERE.md`](START-HERE.md) です。この文書は起動シーケンスの詳細です。

Berth は Kubernetes-native runtime です。旧 standalone/conda/SQLite/Chroma 起動手順は Berth の現行運用経路ではありません。

## 1. 日常の起動

リポジトリ root で:

```bash
./ops/status.sh
./ops/start.sh
./ops/verify.sh
```

既定のローカル入口:

```text
http://127.0.0.1:18765
```

## 2. 起動シーケンス

`ops/start.sh` を入口とし、次の順で復帰・確認することを運用契約とします。

```text
Preflight
  ↓
host container substrate
  ↓
k3d / Linux Kubernetes cluster
  ↓
PostgreSQL + pgvector
  ↓
Redis
  ↓
Berth API replicas
  ↓
Berth worker replicas
  ↓
/api/health
  ↓
/api/ready
  ↓
ops/verify.sh
  ↓
endpoint available
```

起動後は少なくとも次を確認します。

- API replicas Ready
- worker replicas Ready
- PostgreSQL/pgvector Ready
- Redis Ready
- `/api/health` = ok
- `/api/ready` = ready
- pgvector consistency

## 3. 再起動

```bash
./ops/restart.sh
./ops/verify.sh
```

## 4. 停止

```bash
./ops/stop.sh
```

停止はデータ削除ではありません。Persistent state / PostgreSQL backup は別管理です。

## 5. Host reboot 後

Host reboot 後は、いきなり cluster を作り直さず、既存 state の再利用を優先します。

```bash
./ops/start.sh
./ops/verify.sh
```

既存 k3d node の network state が変わった場合は、サポート済みの復帰処理で修復してから health/readiness を確認します。

## 6. Fresh deploy

Fresh/scratch cluster を作る場合は live cluster と entry port を分離します。

基本経路:

```bash
export CYNOVELA_CLUSTER=hansolo-scratch
export ENTRY_PORT=18766
export IMAGE=<han-solo-image>
export HAN_SOLO_MODELS_DIR=<bundle-or-repo-relative-model-directory>
./deploy/k8s/rebuild-all.sh --recreate
```

**Portable/移行検証では、`HAN_SOLO_MODELS_DIR` に元 Mac の Chewie 絶対パスを使ってはいけません。** 展開した portable bundle 内 `models/` または Berth 自身の相対 model directory を使用します。

## 7. Backup / restore

Backup:

```bash
./deploy/k8s/pg-backup.sh backup
```

Restore verification:

```bash
./deploy/k8s/pg-backup.sh verify <dump-file>
```

Scratch restore:

```bash
./deploy/k8s/pg-backup.sh restore <dump-file> --yes
./ops/verify.sh
```

少なくとも1世代の restore-verified backup を保持します。

## 8. Models

Berth portable bundle に同梱するのは Berth 自身の RAG/guardrail に必要なモデルです。

- embedding
- reranker
- active configuration が必要とする PII/NER/classifier model

**回答生成 LLM は同梱しません。** LM Studio / Ollama / OpenAI-compatible endpoint 等へ外部接続します。

## 9. Provider connection

Provider URL/model は環境・設定から選択します。元 Mac 固有 IP を portable default にしてはいけません。

接続確認:

```bash
export CYNOVELA_URL=http://127.0.0.1:18765
python3 cynovela_cli.py settings models
python3 cynovela_cli.py settings test-connection
```

## 10. CLI / MCP

```bash
export CYNOVELA_URL=http://127.0.0.1:18765
python3 cynovela_cli.py status
python3 cynovela_cli.py login --username cynovela
```

HTTP MCP:

```text
POST /api/mcp/rpc
GET  /api/mcp/fingerprint
```

詳しくは [`docs/CLI-MCP.md`](docs/CLI-MCP.md)。

## 11. Portable distribution

別マシンへ持ち出すための正本は [`docs/portable-distribution.md`](docs/portable-distribution.md) です。

Portable path の必須条件:

- original user absolute path dependency = 0
- Chewie tree dependency = 0
- original local image-cache dependency = 0 for offline bundle mode
- generative LLM payload = 0
- required RAG/guardrail model payload = present
- vendor/product-specific test fixture = excluded
- secret leakage = 0

## 12. Troubleshooting

### `status` / `verify` が失敗する

```bash
./ops/status.sh
./ops/verify.sh
```

Kubernetes resource と health/readiness のどこで止まったかを先に特定します。

### CLI が違う port を見る

Berth では URL を明示します。

```bash
export CYNOVELA_URL=http://127.0.0.1:18765
```

### RAG が空

```bash
python3 cynovela_cli.py collections list --workspace <workspace_id>
python3 cynovela_cli.py index-status --workspace <workspace_id>
python3 cynovela_cli.py jobs --kind publish
```

### LLM 接続が失敗する

Berth 本体の Ready と provider 接続は別問題です。まず `./ops/verify.sh`、次に `settings test-connection` で分離して確認します。

# Berth Operations

Berth is operated as a **Kubernetes-native runtime**. The current reference environment uses k3d on Podman, but operational documentation avoids original-machine absolute paths and dated cluster names as required defaults.

## Runtime shape

| Component | Operation |
|---|---|
| Runtime contract | Linux Kubernetes |
| Reference substrate | k3d on Podman |
| Namespace | `cynovela` |
| Local entry | `http://127.0.0.1:18765` |
| API | deployment `cynovela`, 2 replicas |
| Worker | deployment `cynovela-worker`, 2 replicas |
| Database/vector | PostgreSQL + pgvector |
| Queue/cache | Redis |
| LLM providers | external LM Studio / Ollama / OpenAI-compatible endpoint |
| GA application image | `localhost/cynovela-hansolo:dd0144-ga` |
| Chewie semantic baseline | `v1.2.0` |

## Daily commands

Run from the Berth root:

```bash
./ops/status.sh
./ops/start.sh
./ops/verify.sh
./ops/restart.sh
./ops/stop.sh
```

## Recovery after host/container restart

```bash
./ops/start.sh
./ops/verify.sh
```

Expected result:

- container substrate running
- Kubernetes cluster running
- API replicas Ready
- worker replicas Ready
- PostgreSQL/pgvector Ready
- Redis Ready
- `/api/health` = ok
- `/api/ready` = ready
- verification PASS

Do not rebuild a healthy persisted database merely because the host restarted.

## Fresh scratch deploy

Use a neutral scratch cluster and an explicit model directory owned by the Berth tree or portable bundle.

```bash
export CYNOVELA_CLUSTER=hansolo-scratch
export ENTRY_PORT=18766
export IMAGE=localhost/cynovela-hansolo:dd0144-ga
export HAN_SOLO_MODELS_DIR="$PWD/store/models"
export HAN_SOLO_SKIP_INDEX_WAIT=1
export HAN_SOLO_SKIP_FINAL_VERIFY=1
./deploy/k8s/rebuild-all.sh --recreate
```

Portable acceptance must instead point `HAN_SOLO_MODELS_DIR` at the expanded bundle's own model directory and must not read another local product tree.

## Backup / restore verification

Create a backup:

```bash
./deploy/k8s/pg-backup.sh backup
```

Verify without replacing live data:

```bash
./deploy/k8s/pg-backup.sh verify <dump-file>
```

Restore into an isolated scratch environment:

```bash
./deploy/k8s/pg-backup.sh restore <dump-file> --yes
./ops/verify.sh
```

Keep at least one restore-verified backup.

## Verification matrix

Operational verification should cover:

- `./ops/verify.sh`
- Admin login and RAG
- Viewer RAG in an allowed workspace
- unauthorized workspace denial
- masking / restricted-data non-leakage
- external provider connectivity
- provider switching persistence
- CLI
- HTTP MCP discovery/tools/call
- PostgreSQL backup verify
- fresh scratch deploy / restore
- secret/path/vendor-neutral packaging scan for portable artifacts

## CLI / MCP

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

See [`CLI-MCP.md`](CLI-MCP.md).

## Portable operations

See [`portable-distribution.md`](portable-distribution.md).

Portable restore must not depend on:

- original host absolute paths
- original user name
- original Chewie model tree
- original Podman image cache
- original fixed provider IP
- dated cluster names as mandatory defaults

## Known limitations

- A physical host cold boot still requires a human-triggered restart. After reboot, run `./ops/start.sh && ./ops/verify.sh` and record the result.
- On Podman-backed k3d, node container IPs can drift across stop/start. Supported startup logic must repair transient networking state before health checks.
- The source repository still contains compatibility/history/test material that is not part of the active Berth GA runtime. The portable artifact must exclude such material unless explicitly required and neutralized.
- Multi-architecture portability is not PASS until the Berth application image and native dependencies are acceptance-tested on each declared target architecture.

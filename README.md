## Linux amd64 / Windows WSL2 release candidate

A direct Kubernetes adapter and tested WSL2 Ubuntu 24.04 path are available in [the Linux/WSL guide](docs/oss/linux-wsl.md). This branch is a release candidate, not a new public GA release. See [third-party audit](THIRD_PARTY_NOTICES.md) and [model policy](docs/oss/model-policy.md) before redistributing an image. Existing Mac operations remain unchanged.

# Berth

Berth is the **Kubernetes-native Cynovela runtime**. It preserves shared Cynovela/Chewie RAG semantics where appropriate while keeping its own Kubernetes platform assets: PostgreSQL with pgvector, Redis-backed workers, multi-replica API/worker deployments, backup/restore, provider switching, CLI, and HTTP MCP.

The current reference host is macOS Apple Silicon using Podman + k3d, but the runtime contract is Kubernetes/Linux rather than a Mac-specific application package.

> Start here: [`START-HERE.md`](START-HERE.md)

## Current GA line

| Item | Value |
|---|---|
| Berth version | `v1.0.0-ga` |
| Chewie semantic baseline | `v1.2.0` |
| Runtime contract | Kubernetes / Linux workloads |
| Reference host substrate | k3d on Podman |
| Local entry | `http://127.0.0.1:18765` |
| API deployment | `cynovela`, 2 replicas |
| Worker deployment | `cynovela-worker`, 2 replicas |
| Database/vector | PostgreSQL + pgvector |
| Queue/cache | Redis |
| GA application image | `localhost/cynovela-hansolo:dd0144-ga` |
| Retained rollback image | `localhost/cynovela-hansolo:dd0143n` |

`v1.0.0-ga` is the immutable GA anchor. Post-GA portability/documentation work proceeds on `main`.

## Adding an ingest folder

On the Linux/WSL2 path the ingest area is a host directory (`$HAN_SOLO_DATA_DIR/ingest`, mounted read-only into the pods as `/app/ingest`), so a new ingest folder is just a new host folder: no restart, rollout, PVC or Deployment change is needed. The helper creates the folder, optionally copies documents into it, registers it as a source and scans it; the folder then also appears in the folder picker of "Add source" and Quick Start.

```bash
./scripts/add-ingest-folder.sh contracts --from ~/Documents/contracts   # add --no-register to only create the folder
```

## Operations

Use the supported scripts from the repository root:

```bash
./ops/status.sh
./ops/start.sh
./ops/verify.sh
./ops/restart.sh
./ops/stop.sh
```

`ops/start.sh` restores or creates the supported Kubernetes path, waits for PostgreSQL/pgvector, Redis, API and workers, then runs verification.

## Validation

The GA acceptance matrix includes:

- API `2/2` Ready and worker `2/2` Ready
- `/api/health` = ok
- `/api/ready` = ready
- pgvector consistency
- Admin RAG / Viewer RAG
- unauthorized workspace denial
- masking / restricted-data non-leakage
- external LM Studio and Ollama provider connections
- provider switching persistence
- CLI
- HTTP MCP
- PostgreSQL backup verification and scratch restore

## Fresh deploy

Use a neutral scratch name and a model directory belonging to the Berth tree or portable bundle.

```bash
export CYNOVELA_CLUSTER=hansolo-scratch
export ENTRY_PORT=18766
export IMAGE=localhost/cynovela-hansolo:dd0144-ga
export HAN_SOLO_MODELS_DIR="$PWD/store/models"
export HAN_SOLO_SKIP_INDEX_WAIT=1
export HAN_SOLO_SKIP_FINAL_VERIFY=1
export ADMIN_INITIAL_PASSWORD='<choose-a-strong-admin-password>'
export VIEWER_INITIAL_PASSWORD='<choose-a-strong-viewer-password>'
./deploy/k8s/rebuild-all.sh --recreate
```

For a portable bundle, `HAN_SOLO_MODELS_DIR` must resolve to the expanded bundle's own `models/` directory. Do not point it to another product tree or an original-machine absolute path.

## Backup / restore

```bash
./deploy/k8s/pg-backup.sh backup
./deploy/k8s/pg-backup.sh verify <dump-file>
```

Retain at least one restore-verified backup.

## Models

Portable Berth packages include only models required by Berth itself for RAG / guardrail processing, such as embedding, reranking, and active PII/NER/classifier models.

**Answer-generation LLM model files are not bundled.** Berth connects to an external LM Studio, Ollama, or other OpenAI-compatible provider.

## Portable distribution and platform targets

See [`docs/portable-distribution.md`](docs/portable-distribution.md).

Target order:

1. macOS Apple Silicon / arm64 — current reference
2. Linux arm64
3. Linux amd64 with multi-arch application images
4. Windows host running a Linux Kubernetes environment — future target

The final portable artifact must be vendor-neutral and free of original-machine absolute paths, fixed personal host addresses, old Chewie model-tree dependencies, and vendor/product-specific test fixtures.

## CLI / MCP

See [`docs/CLI-MCP.md`](docs/CLI-MCP.md).

Recommended Berth CLI setup:

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

Kyber-style integrations should prefer HTTP MCP with a least-privilege scoped API key rather than a long-lived admin credential.

## Known limitations

- Physical host cold boot remains a post-GA operational validation item; after a human-triggered reboot, run `./ops/start.sh && ./ops/verify.sh`.
- Existing k3d clusters may restart with changed container IPs; supported startup logic repairs recorded node network state before health checks.
- Source code still contains standalone compatibility paths inherited from shared Cynovela history; they are not the active Berth GA architecture.
- Portable multi-architecture support is not considered PASS until the application image and native dependencies are built and acceptance-tested on each declared architecture.

## Documentation

- [`START-HERE.md`](START-HERE.md) — single entry document
- [`STARTUP.md`](STARTUP.md) — startup / recovery sequence
- [`docs/portable-distribution.md`](docs/portable-distribution.md) — portability / backup / vendor neutrality
- [`docs/CLI-MCP.md`](docs/CLI-MCP.md) — CLI / MCP / Kyber integration
- [`docs/operations.md`](docs/operations.md) — operations
- [`docs/oss/requirements.md`](docs/oss/requirements.md) — system requirements
- [`docs/oss/troubleshooting.md`](docs/oss/troubleshooting.md) — troubleshooting
- [`docs/oss/uninstall-upgrade.md`](docs/oss/uninstall-upgrade.md) — update / uninstall
- [`docs/oss/before-distributing.md`](docs/oss/before-distributing.md) — redistribution checklist
- [`docs/release-notes-v1.0.0-ga.md`](docs/release-notes-v1.0.0-ga.md) — GA release
- [`SECURITY.md`](SECURITY.md) — security

## License

MIT

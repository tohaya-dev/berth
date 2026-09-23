English | [日本語](README.ja.md)

# Berth

Berth is a **Kubernetes-native, self-hosted RAG runtime** (document ingestion, retrieval and answer generation over your own files): PostgreSQL with pgvector, Redis-backed workers, two-replica API and worker Deployments, backup/restore, external LLM provider switching, a CLI and HTTP MCP. The runtime contract is Kubernetes on Linux. This repository ships the Linux amd64 / Windows WSL2 path (K3s on Ubuntu 24.04) as a **release candidate**, not a new public GA release. Read the [third-party notices](THIRD_PARTY_NOTICES.md) and the [model policy](docs/oss/model-policy.md) before redistributing an image.

The application engine inside Berth is called *Cynovela*. That name appears in Kubernetes object names (`cynovela`, `cynovela-svc`, ...), the configuration file `cynovela.yaml`, the CLI `cynovela_cli.py` and the initial administrator account `cynovela`; it is not a separate product you need to install.

> Start here: [the start page](docs/oss/start-here.md) (from an empty machine to a first answer, with timings), then
> [the Linux / WSL2 guide](docs/oss/linux-wsl.md) (fresh installation, operations, recovery) and
> [the Windows launcher](docs/oss/windows-launcher.md).

## What this repository provides

| Item | Value |
|---|---|
| Version | `v1.0.0-ga` (`VERSION`; `/api/health` reports `1.0.0-ga`) |
| Runtime contract | Kubernetes / Linux workloads |
| Tested substrate | K3s on Ubuntu 24.04 amd64 under Windows 11 WSL2 (`deploy/k8s/linux/install-k3s.sh`) |
| Local entry | `http://127.0.0.1:18765` via `./ops/linux.sh connect` or `serve` |
| API deployment | `cynovela`, 2 replicas |
| Worker deployment | `cynovela-worker`, 2 replicas |
| Database/vector | PostgreSQL + pgvector |
| Queue/cache | Redis |
| Application image | built locally from `deploy/container/Containerfile` (no image is published); `IMAGE` must name the imported reference |
| Initial administrator | `cynovela`; password generated at install time in `$BERTH_DATA_DIR/secrets/admin_password` |

## System requirements

In short: x86_64 Ubuntu 24.04 with systemd (a dedicated WSL2 distribution on Windows 11, or native), non-interactive `sudo`, Docker Engine or Podman to build the image, about 16–24 GiB of memory (the API and worker pods request 5.5 GiB in total and may grow to 24 GiB at their limits) and 40 GiB of disk (the pinned models alone are ≈4.3 GiB). Ports: 18765 on loopback for the UI/API, 26443 for the Kubernetes API. Internet access is needed once for K3s, the model download and the image build; at runtime models are loaded offline. Details and sources: [docs/oss/requirements.md](docs/oss/requirements.md).

## Installation

Follow [docs/oss/start-here.md](docs/oss/start-here.md) (step by step) or [docs/oss/linux-wsl.md](docs/oss/linux-wsl.md) (reference) from the repository root. Export the same `BERTH_*` variables in every shell, including `BERTH_NAMESPACE` if you install into a namespace other than the default `berth`. In short: install the pinned K3s with `./deploy/k8s/linux/install-k3s.sh` (or point `KUBECONFIG`/`BERTH_CONTEXT` at an existing non-production cluster), download the pinned embedding/reranker models with `tools/download-rag-models.py`, build the application image with Docker or Podman and import it into K3s, then:

```bash
./ops/linux.sh install    # renders manifests into $BERTH_DATA_DIR/rendered, applies them, waits for the rollouts
./ops/linux.sh connect    # foreground port-forward to http://127.0.0.1:18765 (keep it running)
./ops/linux.sh verify     # in another terminal with the same environment
```

Every `ops/linux.sh` subcommand requires `BERTH_CONTEXT`; `install`/`start` also require `IMAGE`. The `HAN_SOLO_*` names remain accepted as a deprecated alias of the `BERTH_*` variables (when both are set, `BERTH_*` wins). Log in as `cynovela` with the generated password, then configure an external OpenAI-compatible or Ollama provider in Settings: a fresh install has no reachable answer-generation endpoint, so RAG chat returns a guarded provider error until one is configured (retrieval and ingestion work without it).

## Operations

```bash
./ops/linux.sh status     # pods, deployments, services, PVCs of the namespace
./ops/linux.sh verify     # API 2/2 and worker 2/2 Ready, /api/health ok, /api/ready, pgvector extension present
./ops/linux.sh restart    # rolling restart of API and workers
./ops/linux.sh stop       # scales API, workers, Redis and PostgreSQL to 0
./ops/linux.sh start      # same path as install: re-renders, re-applies, waits for the rollouts (needs IMAGE)
./ops/linux.sh serve      # port-forward that reconnects after pod replacement (used by the Windows launcher)
```

`connect` must stay running; restart it (or use `serve`) after its selected pod exits. For a persistent Windows session use [`ops/windows-wsl.ps1`](docs/oss/windows-launcher.md).

## Adding an ingest folder

On the Linux/WSL2 path the ingest area is a host directory (`$BERTH_DATA_DIR/ingest`, mounted read-only into the pods as `/app/ingest`), so a new ingest folder is just a new host folder: no restart, rollout, PVC or Deployment change is needed. The pods run as the non-root uid `10001`, so the folder must be traversable and its files world-readable (directories `o+rx`, files `o+r`); the helper creates the folder with those permissions, optionally copies documents into it (making them `o+r`), registers it as a source and scans it. The folder then also appears in the folder picker of "Add source" and Quick Start.

```bash
./scripts/add-ingest-folder.sh contracts --from ~/Documents/contracts   # add --no-register to only create the folder
```

If you create the folder by hand instead, run `chmod o+rx "$BERTH_DATA_DIR/ingest/<folder>"` and `chmod o+r` on the files; the helper needs `./ops/linux.sh connect` (or `serve`) running to register the source.

## Backup / restore

`ops/linux.sh backup|restore` wraps `deploy/k8s/pg-backup.sh` with the configured context, namespace and secret key:

```bash
export PG_BACKUP_DIR="$PWD/_oss-rc-backups"
./ops/linux.sh backup
./ops/linux.sh restore "$PG_BACKUP_DIR/pgdump-TIMESTAMP.dump" --yes   # scale API/workers to 0 first, see the guide
```

`backup` writes a dump, a checksum sidecar and a paired `secret.key-TIMESTAMP`; `restore` validates the dump in a temporary database and checks the destination key before touching the live database. Retain at least one restore-verified backup together with its key; models and original ingest files need separate retention.

## Models

`tools/download-rag-models.py --cache "$BERTH_DATA_DIR/models"` downloads only the pinned embedding and reranker models (`locks/models.json`) that Berth needs for retrieval; PII/NER models come with the image's Python dependencies. See the [model policy](docs/oss/model-policy.md) for versions and licenses.

**Answer-generation LLM model files are not bundled or downloaded.** Berth connects to an external LM Studio, Ollama, or other OpenAI-compatible provider that you configure.

## Source snapshot

This repository is a source snapshot of the runtime. `manifest.json` lists every shipped file with its SHA-256, the executable files (`modes`), the `product` name and `commit`, the development-tree commit the snapshot was built from. Only Linux amd64 has been build- and acceptance-tested; see the platform table in the [Linux / WSL2 guide](docs/oss/linux-wsl.md).

## CLI / MCP

`cynovela_cli.py --help` lists every CLI subcommand; the HTTP MCP endpoint is served by the API
(see `routers/mcp_http.py` for the tool list and the API-key scopes it enforces).

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

Integrations should prefer HTTP MCP with a least-privilege scoped API key rather than a long-lived admin credential.

## Known limitations

- Single node only: the model cache and ingest area are hostPath volumes on the K3s node; multi-node clusters are not validated.
- A fresh install has no answer-generation provider; configure an external LLM before expecting chat answers.
- Native Ubuntu 24.04 bare-metal acceptance and a Windows host reboot test are pending (see the guide and the launcher document).
- Source code still contains standalone compatibility paths inherited from shared Cynovela history; they are not the active Berth architecture.
- Other architectures are not considered PASS until the application image and native dependencies are built and acceptance-tested on them.

See [docs/oss/limitations.md](docs/oss/limitations.md) for the full list.

## Documentation

- [`docs/oss/start-here.md`](docs/oss/start-here.md) — start here: installation sequence, first login, what each step does
- [`docs/oss/requirements.md`](docs/oss/requirements.md) — system requirements: OS, ports, CPU/memory, disk, network
- [`docs/oss/linux-wsl.md`](docs/oss/linux-wsl.md) — Linux amd64 / Windows WSL2: installation, operations, recovery
- [`docs/oss/windows-launcher.md`](docs/oss/windows-launcher.md) — Windows launcher for the WSL2 runtime
- [`docs/oss/troubleshooting.md`](docs/oss/troubleshooting.md) — symptoms, causes, fixes
- [`docs/oss/uninstall-upgrade.md`](docs/oss/uninstall-upgrade.md) — uninstall, upgrade, reinstall
- [`docs/oss/limitations.md`](docs/oss/limitations.md) — known limitations
- [`docs/oss/faq.md`](docs/oss/faq.md) — frequently asked questions
- [`docs/oss/before-distributing.md`](docs/oss/before-distributing.md) — read before distributing an image or a deployment
- [`docs/oss/model-policy.md`](docs/oss/model-policy.md) — embedding / reranker model policy
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and [`docs/oss/dependency-licenses.csv`](docs/oss/dependency-licenses.csv) — third-party licenses
- [`SECURITY.md`](SECURITY.md) — security
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)

## License

MIT

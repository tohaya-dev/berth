English | [日本語](start-here.ja.md)

# Start here

Berth runs as a set of Kubernetes workloads (two API pods, two worker pods, PostgreSQL with pgvector, Redis) on a single-node K3s inside Ubuntu 24.04 — on Windows 11 in a dedicated WSL2 distribution, or on native Ubuntu. This page is the path from an empty machine to a first answer. The [Linux / WSL2 guide](linux-wsl.md) is the reference for every command below; if the two ever disagree, follow the guide.

Before you begin, check the [system requirements](requirements.md): x86_64, systemd, non-interactive `sudo`, about 16–24 GiB of memory and 40 GiB of disk, and Internet access for the one-time downloads.

## The sequence at a glance

| # | Step | What happens | Time (basis) |
|---|---|---|---|
| 1 | Prepare Ubuntu | packages, Python venv with `PyYAML`, `cryptography`, `huggingface_hub` | a few minutes, network-bound (not measured) |
| 2 | Set the environment | `BERTH_*` variables, `PYBIN` | — |
| 3 | `install-k3s.sh` | installs K3s `v1.35.5+k3s1` as systemd unit `k3s-<context>`, data in `$BERTH_DATA_DIR/k3s`, API on port 26443; writes `$BERTH_DATA_DIR/client.yaml` and renames its context to `$BERTH_CONTEXT` | waits up to 300 s for the node to become Ready (`install-k3s.sh`); not measured on a fresh host |
| 4 | Download models | `tools/download-rag-models.py` fetches BAAI/bge-m3 and BAAI/bge-reranker-v2-m3 at pinned revisions into `$BERTH_DATA_DIR/models` and verifies every file's SHA-256; 4,586,590,960 bytes (≈4.3 GiB) | ≈4.5 min in a recorded fresh-clone run; ≈6.5 min in another (mostly download plus hash verification); depends on Hugging Face throughput |
| 5 | Build and import the image | Docker or Podman builds `deploy/container/Containerfile`; `docker save` / `podman save`; `sudo k3s ctr images import` | usually the longest step (pip install incl. CPU torch); a cold build was not timed. `podman save` took 39 s in the recorded run |
| 6 | `ops/linux.sh install` | see "What `install` creates" below | 70 s in the recorded run on an already running cluster with the image imported; each API pod was Ready ≈50 s after start. Wait limits: PostgreSQL 300 s, Redis 180 s, bootstrap Job 300 s, API 900 s, worker 600 s |
| 7 | `connect` or `serve` | port-forward `127.0.0.1:18765` → `service/cynovela-svc:8765` | immediate |
| 8 | `verify` | replicas API 2/2, worker 2/2, pgvector 1/1, Redis 1/1; `/api/health` status ok; `/api/ready`; `vector` extension present | seconds |
| 9 | First login and LLM provider | log in as `cynovela`; configure an external OpenAI-compatible or Ollama provider in Settings | — |

Measured times come from one fresh-clone validation on a WSL2 test host (models, image save, install) and from pod timestamps on the same host (≈50 s to Ready). They are indications, not guarantees.

## 1. Prepare Ubuntu

On Windows, create or pick a dedicated Ubuntu 24.04 WSL2 distribution with systemd enabled ([linux-wsl.md](linux-wsl.md), "Windows preparation"). Clone the repository inside the Linux filesystem, for example `~/Projects/berth`, and run everything from its root:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv curl git
python3 -m venv .venv
.venv/bin/pip install PyYAML cryptography huggingface_hub
```

## 2. Set the environment

```bash
export PYBIN="$PWD/.venv/bin/python"
export BERTH_CONTEXT=berth-local
export BERTH_NAMESPACE=berth
export BERTH_DATA_DIR="$HOME/.local/share/berth"
export BERTH_ENTRY_PORT=18765
export BERTH_KUBE_PORT=26443
```

Every later command, in every new shell, needs the same values. Keep these lines (plus `KUBECONFIG` and `IMAGE` from the next steps) in a file outside the repository and `source` it in each terminal.

**Namespace.** `ops/linux.sh` and the Windows launcher default to the namespace `berth`. If you install into another namespace, export `BERTH_NAMESPACE` with that value in every shell; a shell without it looks at a different, usually empty, namespace (`status` shows nothing, `connect` fails).

`BERTH_CONTEXT` is required by every `ops/linux.sh` subcommand and must match `^berth-[a-z0-9-]+$` for the K3s installer. The `HAN_SOLO_*` names are still accepted as deprecated aliases; `BERTH_*` wins when both are set.

## 3. Install K3s

```bash
./deploy/k8s/linux/install-k3s.sh
export KUBECONFIG="$BERTH_DATA_DIR/client.yaml"
```

The installer refuses an existing K3s installation. To use an existing non-production cluster instead, skip it, export `KUBECONFIG` to a kubeconfig you own and set `BERTH_CONTEXT` to a context in it ([linux-wsl.md](linux-wsl.md)). On cgroup v1 (the tested WSL kernel) it adds `failCgroupV1: false`; it also scopes `GODEBUG=netdns=go` to the K3s unit.

## 4. Download the models

```bash
.venv/bin/python tools/download-rag-models.py --cache "$BERTH_DATA_DIR/models"
```

Expected output ends with `BAAI/bge-m3 pinned files verified`, `BAAI/bge-reranker-v2-m3 pinned files verified` and `No generative model downloaded`. A size or hash mismatch stops with `FAIL: model checksum mismatch`. This is the only step that contacts Hugging Face; the pods mount the directory read-only and run with `HF_HUB_OFFLINE=1`. No answer-generation (LLM) model is downloaded. See the [model policy](model-policy.md).

## 5. Build and import the image

No image is published; build it locally. Docker example (Podman variant in [linux-wsl.md](linux-wsl.md)):

```bash
docker build --platform linux/amd64 --build-arg APT_UPGRADE=1 \
  --build-arg REQUIREMENTS_FILE=locks/requirements-linux-amd64.txt \
  -f deploy/container/Containerfile -t berth:rc-amd64 .
docker save berth:rc-amd64 -o berth-amd64.tar
sudo k3s ctr images import berth-amd64.tar
sudo k3s ctr images ls | grep berth
export IMAGE=docker.io/library/berth:rc-amd64   # Podman: localhost/berth:rc-amd64
```

`IMAGE` must exactly match the reference printed by `sudo k3s ctr images ls`, or the pods end in `ImagePullBackOff`. You can delete the `.tar` after the import.

## 6. Install

```bash
./ops/linux.sh install
```

### What `install` creates

| Order | Object or file | Notes |
|---|---|---|
| 1 | `$BERTH_DATA_DIR/models`, `ingest` (mode 755) and `secrets` | created by `render.py` if missing |
| 2 | `$BERTH_DATA_DIR/secrets/pg_password`, `admin_password`, `secret_key` | generated once (mode 600) and kept on later runs |
| 3 | `$BERTH_DATA_DIR/rendered/infrastructure.yaml`, `bootstrap.yaml`, `workloads.yaml` | rendered manifests |
| 4 | Namespace, Secret `cynovela-secret`, ConfigMap `cynovela-config` | the ConfigMap holds the rendered `cynovela.yaml` (PostgreSQL, pgvector, Redis queue, masking parallelism 1, LLM pointing at an unreachable loopback address) |
| 5 | `cynovela-pgvector` (PVC 2Gi) and `cynovela-redis` (PVC 1Gi) | waits for their rollouts |
| 6 | Job `hansolo-bootstrap` | creates the schema and the initial administrator; `backoffLimit: 0`, so a failure is not retried — read its log |
| 7 | Deployments `cynovela` (API, 2) and `cynovela-worker` (2), Service `cynovela-svc` (ClusterIP) | waits for the rollouts, then prints `Runtime ready.` |

Running `install` (or `start`) again re-renders and re-applies; existing secrets and users are kept. Volumes live under `$BERTH_DATA_DIR/volumes`.

## 7. Connect

```bash
./ops/linux.sh connect      # foreground; keep this terminal open
```

`connect` exits when its selected pod is replaced; `./ops/linux.sh serve` reconnects automatically. On Windows, the [launcher](windows-launcher.md) runs `serve` in a hidden WSL client and keeps the distribution alive.

## 8. Verify

In another terminal with the same environment:

```bash
./ops/linux.sh verify
```

Expected: `replicas PASS`, `health PASS`, `pgvector PASS`.

## 9. First login and first use

1. Open `http://127.0.0.1:18765`.
2. Log in as `cynovela`. The password was generated at install time; read it locally from `$BERTH_DATA_DIR/secrets/admin_password`. Do not paste it into logs, tickets or commits. No password change is forced at first login.
3. The UI may open in Japanese; switch the language in the UI.
4. In Settings, configure an external OpenAI-compatible or Ollama provider. Until you do, chat returns a guarded provider error; retrieval and ingestion already work.
5. Add documents: put them in a folder under `$BERTH_DATA_DIR/ingest` (directories `o+rx`, files `o+r`, because the pods run as uid 10001), or use `./scripts/add-ingest-folder.sh` ([README](../../README.md#adding-an-ingest-folder)). Then use "Add source" or Quick Start.

## Next

- [System requirements](requirements.md)
- [Linux / WSL2 guide](linux-wsl.md) — full reference, backup/restore
- [Windows launcher](windows-launcher.md)
- [Model policy](model-policy.md)
- [Troubleshooting](troubleshooting.md)
- [Uninstall and upgrade](uninstall-upgrade.md)
- [Limitations](limitations.md)
- [FAQ](faq.md)
- [Before distributing](before-distributing.md)

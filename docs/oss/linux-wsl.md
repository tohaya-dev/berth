English | [日本語](linux-wsl.ja.md)

# Linux amd64 / Windows WSL2 reference

This release adds a direct K3s adapter (`ops/linux.sh`, `deploy/k8s/linux/`) to the PostgreSQL/pgvector + Redis + two API + two worker architecture. Podman and Docker are interchangeable image builders; Kubernetes is the runtime contract.

| Platform | Evidence |
|---|---|
| Windows 11 + WSL2 Ubuntu 24.04 x86_64 | Actual image build, deploy, retrieval and security acceptance |
| Native Ubuntu 24.04 amd64 | Adapter available; separate bare-metal acceptance pending |
| Ubuntu 22.04 | Not tested in this run |
| Native Windows containers | Not supported by this adapter |
| Multi-node Kubernetes | Not validated; model/ingest hostPath assumes one node |

## Windows preparation

Use a dedicated Ubuntu 24.04 WSL2 distribution with systemd enabled. Check `wsl --list --verbose` in PowerShell. Install a new distribution if needed with `wsl --install -d Ubuntu-24.04`. Windows feature enablement may require a user-controlled reboot. Do not terminate or modify unrelated distributions.

Clone inside the Linux filesystem, for example `~/Projects/berth`, and keep runtime data there. Reserve approximately 16–24 GiB memory and 40 GiB disk for building, model cache and live pods; actual use varies.

## Fresh installation

Run inside Ubuntu from the repository root:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv curl git
python3 -m venv .venv
.venv/bin/pip install PyYAML cryptography huggingface_hub
export PYBIN="$PWD/.venv/bin/python"
export BERTH_CONTEXT=berth-local
export BERTH_NAMESPACE=berth
export BERTH_DATA_DIR="$HOME/.local/share/berth"
export BERTH_ENTRY_PORT=18765
export BERTH_KUBE_PORT=26443
./deploy/k8s/linux/install-k3s.sh
export KUBECONFIG="$BERTH_DATA_DIR/client.yaml"
.venv/bin/python tools/download-rag-models.py --cache "$BERTH_DATA_DIR/models"
```

The substrate installer refuses an existing K3s installation. It is also what creates the `berth-local` context and writes `$BERTH_DATA_DIR/client.yaml`; neither exists if you skip it. For an existing cluster, skip the installer and instead export `KUBECONFIG` to a kubeconfig file you own for that cluster (for a K3s installed by other means, a user-readable copy of `/etc/rancher/k3s/k3s.yaml`) and set `BERTH_CONTEXT` to a context name that exists in it (`kubectl config get-contexts`). `ops/linux.sh` passes `--context "$BERTH_CONTEXT"` on every call, so the pair must match. The `HAN_SOLO_*` names remain accepted as a deprecated alias of the `BERTH_*` variables (when both are set, `BERTH_*` wins). Never select production. The official installer verifies the pinned release binary checksum. Docker Desktop and Podman machines are unnecessary.

Build with a Linux OCI builder. Example using Docker Engine on a build host:

```bash
docker build --platform linux/amd64 --build-arg APT_UPGRADE=1 \
  --build-arg REQUIREMENTS_FILE=locks/requirements-linux-amd64.txt \
  -f deploy/container/Containerfile -t berth:rc-amd64 .
docker save berth:rc-amd64 -o berth-amd64.tar
sudo k3s ctr images import berth-amd64.tar
sudo k3s ctr images ls | grep berth      # shows the reference containerd stored
export IMAGE=docker.io/library/berth:rc-amd64
./ops/linux.sh install
./ops/linux.sh connect
```

Native Linux Podman is equivalent; the imported reference differs, and `IMAGE` must exactly match what `sudo k3s ctr images ls` prints or the pods end in `ImagePullBackOff`:

```bash
podman build --build-arg APT_UPGRADE=1 \
  --build-arg REQUIREMENTS_FILE=locks/requirements-linux-amd64.txt \
  -f deploy/container/Containerfile -t berth:rc-amd64 .
podman save --format docker-archive -o berth-amd64.tar localhost/berth:rc-amd64
sudo k3s ctr images import berth-amd64.tar
sudo k3s ctr images ls | grep berth
export IMAGE=localhost/berth:rc-amd64
```

| Builder and transport | Imported reference to use as `IMAGE` |
|---|---|
| `docker build` + `docker save` | `docker.io/library/berth:rc-amd64` |
| `podman build` + `podman save --format docker-archive` | `localhost/berth:rc-amd64` |
| a registry | the fully qualified pull reference |

The foreground connection exposes `http://127.0.0.1:18765`. In another terminal with the same environment run `./ops/linux.sh verify`. Kubernetes TLS listens on the WSL node interface for cluster-internal API access; protect that control-plane port with host/network controls.

Initial administrator: `cynovela`. Read the generated password locally from `$BERTH_DATA_DIR/secrets/admin_password`; never paste it into logs or commit it. Secret files and rendered Secrets are private. Reinstallation preserves existing users and keys. No production/demo database is imported.

Configure an external OpenAI-compatible or Ollama provider through the existing settings API/UI. Fresh defaults point at an unavailable loopback endpoint. BGE runs locally. The test provider is a deterministic contract fixture, not a production answer model.

For a persistent Windows session, use the [Windows launcher](windows-launcher.md). A systemd service alone does not keep WSL running.

## Adding an ingest folder

`$BERTH_DATA_DIR/ingest` is mounted read-only into the API and worker pods as `/app/ingest`, so a new subfolder is visible at once: no restart, rollout, PVC or Deployment change. The pods run as the non-root uid `10001` and a read-only hostPath cannot be re-owned by `fsGroup`, so every folder must be traversable and every file world-readable on the node (directories `o+rx`, files `o+r`); `render.py` sets `ingest/` itself to `755`, and the helper creates subfolders and copies files with those permissions:

```bash
./scripts/add-ingest-folder.sh contracts --from ~/Documents/contracts   # --no-register: only create the folder
```

The helper registers `/app/ingest/<folder>` as a source through the local API (it needs `connect` or `serve` running and reads the admin password from `$BERTH_DATA_DIR/secrets/admin_password`) and scans it. A folder created by hand needs `chmod o+rx` on the directory and `chmod o+r` on the files before it can be scanned; it can then be registered in the GUI folder picker of "Add source" or Quick Start.

## Operations and recovery

`ops/linux.sh status`, `verify`, `restart`, `stop`, `start`, `backup`, and `restore DUMP --yes` target the explicit context/namespace. `connect` must remain running and be restarted after its selected pod exits; `serve` does that reconnect loop for you.

```bash
export PG_BACKUP_DIR="$PWD/_oss-rc-backups"
./ops/linux.sh backup
kubectl --context "$BERTH_CONTEXT" -n "$BERTH_NAMESPACE" \
  scale deploy/cynovela deploy/cynovela-worker --replicas=0
./ops/linux.sh restore "$PG_BACKUP_DIR/pgdump-TIMESTAMP.dump" --yes
kubectl --context "$BERTH_CONTEXT" -n "$BERTH_NAMESPACE" \
  scale deploy/cynovela deploy/cynovela-worker --replicas=2
```

Retain dump, checksum sidecar and paired secret.key-TIMESTAMP together in protected storage. Restore first validates the dump in a temporary database and checks the destination key. Copy the paired key to the destination secret file and update the Kubernetes Secret before starting restored workloads. Never regenerate a key to repair a restore. The SQL dump includes relational state and vectors; models and original ingestion files need independent retention.

The restore rehearsal for this release used a separate namespace, PostgreSQL PVC and Redis with a paired synthetic key, sharing the read-only model cache and synthetic ingestion files. This is database recovery, not a physically separate host test.

## WSL findings

The tested WSL kernel exposed cgroup v1. Kubernetes 1.35 requires its documented `failCgroupV1: false` compatibility option there. The installer writes this only for cgroup v1. Prefer cgroup v2 on new hosts. No global Windows .wslconfig change is required.

The tested containerd resolver needed `GODEBUG=netdns=go`, scoped to the dedicated K3s unit. K3s must bind the node interface for supervisor and Kubernetes Service access.

References: [K3s configuration](https://docs.k3s.io/installation/configuration), [Kubernetes cgroups](https://v1-35.docs.kubernetes.io/docs/concepts/architecture/cgroups/), [Go resolver](https://github.com/golang/go/blob/master/src/net/net.go).

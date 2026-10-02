# Linux amd64 / Windows WSL2 reference

This branch adds a direct K3s adapter to the existing PostgreSQL/pgvector + Redis + two API + two worker architecture. Podman is one optional image builder; Kubernetes is the runtime contract. Existing Mac entry points remain in STARTUP.md and ops/.

| Platform | Evidence |
|---|---|
| Windows 11 + WSL2 Ubuntu 24.04 x86_64 | Actual image build, deploy, retrieval and security acceptance |
| Native Ubuntu 24.04 amd64 | Adapter available; separate bare-metal acceptance pending |
| Ubuntu 22.04 | Not tested in this run |
| macOS arm64 Podman + k3d | Existing GA reference; not retested or contacted |
| Native Windows containers | Not supported by this adapter |
| Multi-node Kubernetes | Not validated; model/ingest hostPath assumes one node |

## Windows preparation

Use a dedicated Ubuntu 24.04 WSL2 distribution with systemd enabled. Check `wsl --list --verbose` in PowerShell. Install a new distribution if needed with `wsl --install -d Ubuntu-24.04`. Windows feature enablement may require a user-controlled reboot. Do not terminate or modify unrelated distributions.

Clone inside the Linux filesystem, for example `~/Projects/hansolo`, and keep runtime data there. Reserve approximately 16–24 GiB memory and 40 GiB disk for building, model cache and live pods; actual use varies.

## Fresh installation

Run inside Ubuntu from the repository root:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv curl git
python3 -m venv .venv
.venv/bin/pip install PyYAML cryptography huggingface_hub
export PYBIN="$PWD/.venv/bin/python"
export HAN_SOLO_CONTEXT=hansolo-local
export HAN_SOLO_NAMESPACE=hansolo
export HAN_SOLO_DATA_DIR="$HOME/.local/share/hansolo"
export HAN_SOLO_ENTRY_PORT=18765
export HAN_SOLO_KUBE_PORT=26443
./deploy/k8s/linux/install-k3s.sh
export KUBECONFIG="$HAN_SOLO_DATA_DIR/client.yaml"
.venv/bin/python tools/download-rag-models.py --cache "$HAN_SOLO_DATA_DIR/models"
```

The substrate installer refuses an existing K3s installation. For an existing cluster, skip it and select an explicit context. Never select production. The official installer verifies the pinned release binary checksum. Docker Desktop and Podman machines are unnecessary.

Build with a Linux OCI builder. Example using Docker Engine on a build host:

```bash
docker build --platform linux/amd64 --build-arg APT_UPGRADE=1 \
  --build-arg REQUIREMENTS_FILE=locks/requirements-linux-amd64.txt \
  -f deploy/container/Containerfile -t hansolo:rc-amd64 .
docker save hansolo:rc-amd64 -o hansolo-amd64.tar
sudo k3s ctr images import hansolo-amd64.tar
export IMAGE=docker.io/library/hansolo:rc-amd64
./ops/linux.sh install
./ops/linux.sh connect
```

Native Linux Podman supports equivalent build and `save --format docker-archive` commands. A registry is another transport. IMAGE must exactly match the imported reference.

The foreground connection exposes `http://127.0.0.1:18765`. In another terminal with the same environment run `./ops/linux.sh verify`. Kubernetes TLS listens on the WSL node interface for cluster-internal API access; protect that control-plane port with host/network controls.

Initial administrator: `cynovela`. Read the generated password locally from `$HAN_SOLO_DATA_DIR/secrets/admin_password`; never paste it into logs or commit it. Secret files and rendered Secrets are private. Reinstallation preserves existing users and keys. No production/demo database is imported.

Configure an external OpenAI-compatible or Ollama provider through the existing settings API/UI. Fresh defaults point at an unavailable loopback endpoint. BGE runs locally. The test provider is a deterministic contract fixture, not a production answer model.

For a persistent Windows session, use the [Windows launcher](windows-launcher.md). A systemd service alone does not keep WSL running.

## Operations and recovery

`ops/linux.sh status`, `verify`, `restart`, `stop`, `start`, `backup`, and `restore DUMP --yes` target the explicit context/namespace. `connect` must remain running and be restarted after its selected pod exits; a local supervisor can do this.

```bash
export PG_BACKUP_DIR="$PWD/_oss-rc-backups"
./ops/linux.sh backup
kubectl --context "$HAN_SOLO_CONTEXT" -n "$HAN_SOLO_NAMESPACE" \
  scale deploy/cynovela deploy/cynovela-worker --replicas=0
./ops/linux.sh restore "$PG_BACKUP_DIR/pgdump-TIMESTAMP.dump" --yes
kubectl --context "$HAN_SOLO_CONTEXT" -n "$HAN_SOLO_NAMESPACE" \
  scale deploy/cynovela deploy/cynovela-worker --replicas=2
```

Retain dump, checksum sidecar and paired secret.key-TIMESTAMP together in protected storage. Restore first validates the dump in a temporary database and checks the destination key. Copy the paired key to the destination secret file and update the Kubernetes Secret before starting restored workloads. Never regenerate a key to repair a restore. The SQL dump includes relational state and vectors; models and original ingestion files need independent retention.

The scratch test uses a separate namespace, PostgreSQL PVC and Redis, with the lab's paired synthetic key. It shares read-only model cache and synthetic ingestion files. This is database recovery, not a physically separate host test.

## WSL findings

The tested WSL kernel exposed cgroup v1. Kubernetes 1.35 requires its documented `failCgroupV1: false` compatibility option there. The installer writes this only for cgroup v1. Prefer cgroup v2 on new hosts. No global Windows .wslconfig change is required.

The tested containerd resolver needed `GODEBUG=netdns=go`, scoped to the dedicated K3s unit. K3s must bind the node interface for supervisor and Kubernetes Service access.

References: [K3s configuration](https://docs.k3s.io/installation/configuration), [Kubernetes cgroups](https://v1-35.docs.kubernetes.io/docs/concepts/architecture/cgroups/), [Go resolver](https://github.com/golang/go/blob/master/src/net/net.go).

English | [日本語](requirements.ja.md)

# System requirements

What a host needs before you follow [Start here](start-here.md) or the [Linux / WSL2 guide](linux-wsl.md). Values come from the shipped scripts and manifests; the file that sets each one is named so you can re-check it.

## Operating system and platform

| Item | Requirement | Source |
|---|---|---|
| Host | Windows 11 with a dedicated WSL2 Ubuntu 24.04 distribution, or native Ubuntu 24.04 (native bare-metal acceptance is still pending) | [linux-wsl.md](linux-wsl.md) platform table |
| CPU architecture | x86_64 / amd64 only; the K3s installer refuses anything else | `deploy/k8s/linux/install-k3s.sh` (`uname -m` check) |
| Init system | systemd must be PID 1 (enable systemd in the WSL distribution) | `install-k3s.sh` (`ps -p 1` check) |
| sudo | non-interactive sudo: `sudo -n true` must succeed (passwordless, or a freshly cached `sudo` timestamp) | `install-k3s.sh` (`sudo -n true`) |
| Kubernetes | K3s `v1.35.5+k3s1` installed by `install-k3s.sh` (override with `K3S_VERSION`); one K3s per distribution — the installer refuses if `/usr/local/bin/k3s` or any `k3s*` systemd unit already exists | `install-k3s.sh` |
| Existing cluster (alternative) | any non-production cluster reachable through a kubeconfig you own, with `BERTH_CONTEXT` naming a context in it | [linux-wsl.md](linux-wsl.md) "Fresh installation" |
| Data directory | on the Linux filesystem: not under `/mnt/`, not `/`, not `$HOME` itself (default `$HOME/.local/share/berth`) | `install-k3s.sh`, `deploy/k8s/linux/render.py` (`/mnt/` refusal), `ops/linux.sh` (default) |
| cgroups | v2 preferred; on cgroup v1 the installer adds the Kubernetes `failCgroupV1: false` option | `install-k3s.sh`, [linux-wsl.md](linux-wsl.md) "WSL findings" |
| Not supported | native Windows containers; multi-node Kubernetes is not validated (models and ingest are hostPath volumes on one node) | [linux-wsl.md](linux-wsl.md) |

## Software

| Tool | Why | Notes |
|---|---|---|
| `python3-venv`, `curl`, `git` | installation steps | `sudo apt-get install -y python3-venv curl git` ([linux-wsl.md](linux-wsl.md)) |
| Python venv with `PyYAML`, `cryptography`, `huggingface_hub` | `render.py` (manifests, secrets) and `tools/download-rag-models.py` | `PYBIN` points `ops/linux.sh` at this interpreter |
| Docker Engine or Podman | builds the application image from `deploy/container/Containerfile`; no image is published | Not checked by any script. The image is then imported with `sudo k3s ctr images import`. Docker Desktop and Podman machines are unnecessary. |
| `kubectl` | used by `ops/linux.sh` and `install-k3s.sh` | provided by the K3s installation |

## Ports

| Port | Bound to | Set by | Purpose |
|---|---|---|---|
| 18765 | `127.0.0.1` by default | `ops/linux.sh` (`BERTH_ENTRY_PORT`; address `BERTH_ENTRY_ADDRESS` or the one-line file `$BERTH_DATA_DIR/entry-address`, IPv4 only) | loopback port-forward created by `ops/linux.sh connect` / `serve` to `service/cynovela-svc` port 8765; the Windows launcher uses the same default (`-Port 18765`) |
| 26443 | `0.0.0.0` (node interface) | `install-k3s.sh` (`BERTH_KUBE_PORT`, `--bind-address 0.0.0.0`) | Kubernetes API (TLS). Protect it with host/network controls. |

The port-forward does no source filtering; if you widen `BERTH_ENTRY_ADDRESS`, restrict the port with a host firewall (`ops/linux.sh` comment). K3s opens further node ports of its own; see the [K3s requirements](https://docs.k3s.io/installation/requirements). Port 18765 must be free before `connect`/`serve`.

## CPU and memory

Resources declared by the shipped manifests (applied unchanged by `render.py`):

| Workload | Replicas | CPU request | Memory request | Memory limit | Source |
|---|---|---|---|---|---|
| API `cynovela` | 2 | 500m | 2Gi | 6Gi | `deploy/k8s/phase4a/20-api-deployment.yaml` (lines 13, 83–84) |
| Worker `cynovela-worker` | 2 | 250m | 768Mi | 6Gi | `deploy/k8s/phase4a/70-worker-deployment.yaml` (lines 13, 57–58) |
| PostgreSQL + pgvector `cynovela-pgvector` | 1 | none | none | none | `deploy/k8s/phase2/50-pgvector.yaml` |
| Redis `cynovela-redis` | 1 | none | none | none | `deploy/k8s/phase2/40-redis.yaml` |
| **Total (API + worker)** | | **1.5 CPU** | **5.5 GiB** | **24 GiB** | |

How this relates to the "reserve approximately 16–24 GiB memory" guidance in [linux-wsl.md](linux-wsl.md):

- **5.5 GiB** is only what the scheduler reserves. Pods stay `Pending` if the node cannot offer it, and PostgreSQL, Redis, K3s itself and the image build are not included.
- **24 GiB** is the sum of the API and worker limits, the worst case before the kernel kills a pod (OOMKilled, exit 137). PostgreSQL and Redis have no limit and come on top.
- 16 GiB is therefore a practical floor for a node that also runs the database, K3s and occasional builds; 24 GiB covers all four application pods at their limits. Actual use depends on the documents and queries and was not profiled per pod for this release.
- There are no CPU limits. The 1.5 CPU requests plus the K3s system pods need more than 1.5 allocatable CPUs; plan for at least 2.
- Under WSL2 the Linux VM only gets part of the Windows memory by default (see Microsoft's [WSL configuration](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)). Check the memory Ubuntu sees with `free -g`; the guide requires no global `.wslconfig` change, but the VM must actually have the memory you plan for.

Memory-pressure settings baked into the rendered configuration (`deploy/k8s/linux/render.py`):

- `masking.parallelism = 1` (line 51) — masking runs one task at a time, the setting previously recommended for machines where the worker ran into OOM restart loops with higher parallelism.
- `worker.embedding_mode = minimal` (line 50).

If pods restart with `OOMKilled`, give the node more memory rather than raising parallelism. See [Troubleshooting](troubleshooting.md) and [Limitations](limitations.md).

## Disk

| Item | Size | Location | Basis |
|---|---|---|---|
| Embedding + reranker models | 4,586,590,960 bytes (≈4.3 GiB) | `$BERTH_DATA_DIR/models` | sum of the pinned files in `locks/models.json` |
| Application image in K3s | ≈2.4–2.5 GiB per imported tag | K3s containerd under `$BERTH_DATA_DIR/k3s` | size column of `sudo k3s ctr images ls` on the test host; every additional tag you import adds to it |
| Image build | builder cache and the saved `.tar` archive | builder storage and the current directory | not measured; delete the archive after import |
| PostgreSQL volume | 2Gi requested | `$BERTH_DATA_DIR/volumes` (K3s local-path storage) | `50-pgvector.yaml` PVC |
| Redis volume | 1Gi requested | `$BERTH_DATA_DIR/volumes` | `40-redis.yaml` PVC |
| Ingest documents, backups | your data | `$BERTH_DATA_DIR/ingest`, `PG_BACKUP_DIR` | — |

The guide's "40 GiB disk" reservation covers the models, the K3s data directory with one or two image tags and the pgvector/Redis images, the build cache and room for ingest data and backups. Remove old image tags you no longer run.

## Network

| When | What is contacted |
|---|---|
| K3s installation (once) | `https://get.k3s.io` and the K3s release download done by that official installer (`install-k3s.sh`) |
| Model download (once) | Hugging Face (`huggingface.co`) via `tools/download-rag-models.py`; ≈4.3 GiB, pinned revisions, SHA-256 verified |
| Image build | base image `python:3.12-slim`, Debian packages, PyPI, `download.pytorch.org` (CPU torch) and the spaCy model wheels on GitHub (`deploy/container/Containerfile`, `locks/requirements-linux-amd64.txt`) |
| First `install` | K3s pulls `docker.io/pgvector/pgvector:pg16` and `docker.io/library/redis:7.2-alpine` |
| Runtime | no model downloads: the image sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` and the models are mounted read-only. The only planned outbound traffic is to the LLM provider you configure. |

See the [model policy](model-policy.md) for model versions and licenses.

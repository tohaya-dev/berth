English | [日本語](troubleshooting.ja.md)

# Troubleshooting

Each entry lists the symptom (with the exact message where the scripts print one), the cause, a command to confirm it, and the fix. Commands assume the environment from [linux-wsl.md](linux-wsl.md), run from the repository root inside Ubuntu:

```bash
export BERTH_CONTEXT=berth-local
export BERTH_NAMESPACE=berth
export BERTH_DATA_DIR="$HOME/.local/share/berth"
export KUBECONFIG="$BERTH_DATA_DIR/client.yaml"
export PYBIN="$PWD/.venv/bin/python"
```

A short alias saves typing in the checks below:

```bash
alias kb='kubectl --context "$BERTH_CONTEXT" -n "$BERTH_NAMESPACE"'
```

Related pages: [Windows launcher](windows-launcher.md), [Uninstall and upgrade](uninstall-upgrade.md), [Requirements](requirements.md), [Limitations](limitations.md), [FAQ](faq.md).

## Contents

1. [Environment not set: context, namespace, data directory](#1-environment-not-set-context-namespace-data-directory)
2. [`install-k3s.sh` refuses to run](#2-install-k3ssh-refuses-to-run)
3. [Model download fails](#3-model-download-fails)
4. [`ops/linux.sh install` stops or times out](#4-opslinuxsh-install-stops-or-times-out)
5. [API pods never become Ready (the 900 s wait)](#5-api-pods-never-become-ready-the-900-s-wait)
6. [Memory pressure and OOMKilled](#6-memory-pressure-and-oomkilled)
7. [The browser loses the connection](#7-the-browser-loses-the-connection)
8. [WSL was shut down; pods restarted](#8-wsl-was-shut-down-pods-restarted)
9. [Windows launcher behaviour](#9-windows-launcher-behaviour)
10. [Backup and restore errors](#10-backup-and-restore-errors)

---

## 1. Environment not set: context, namespace, data directory

### `Set BERTH_CONTEXT ...`

```text
ops/linux.sh: line 18: CONTEXT: Set BERTH_CONTEXT (deprecated alias: HAN_SOLO_CONTEXT) to the target Kubernetes context
```

- **Cause:** every `ops/linux.sh` subcommand requires an explicit context; there is no default. A new terminal does not inherit `export` lines from another one.
- **Fix:** export `BERTH_CONTEXT` (and the rest of the block above) in this shell. To avoid repeating it, keep the `export` lines in a private file and `source` it in each new shell.

### `status` shows nothing, or a second copy appears in another namespace

```text
No resources found in berth namespace.
```

- **Cause:** the shell's `BERTH_NAMESPACE` differs from the namespace you installed into (for example, you installed with a custom value and this shell has no export, so `ops/linux.sh` uses its default `berth`). The shell therefore looks at an empty namespace. `install`/`start` run in such a shell would deploy a **second, separate** instance into that namespace.
- **Check:** `echo "$BERTH_NAMESPACE"`; `kubectl --context "$BERTH_CONTEXT" get namespaces`.
- **Fix:** export `BERTH_NAMESPACE` with the namespace you installed into before any subcommand. If a stray namespace was created by mistake and holds nothing you need, it can be removed as described in [Uninstall and upgrade](uninstall-upgrade.md#remove-berth-from-the-cluster) (destructive).

### Wrong or missing context in the kubeconfig

```text
Error in configuration: context was not found for specified context: berth-local
```

- **Cause:** `KUBECONFIG` does not point at a file containing `BERTH_CONTEXT`. `install-k3s.sh` creates the context and `$BERTH_DATA_DIR/client.yaml`; if you skipped it (existing cluster), the context name is whatever your own kubeconfig uses.
- **Check:** `echo "$KUBECONFIG"; kubectl config get-contexts`.
- **Fix:** `export KUBECONFIG="$BERTH_DATA_DIR/client.yaml"`, or set `BERTH_CONTEXT` to a context listed by `kubectl config get-contexts`.

### Other messages from `ops/linux.sh`

| Message | Cause | Fix |
|---|---|---|
| `IMAGE: Set IMAGE to a built/imported Berth image` | `install`/`start` need the image reference | `export IMAGE=...` exactly as `sudo k3s ctr images ls` prints it |
| `Invalid entry address: ...` | `BERTH_ENTRY_ADDRESS` or `$BERTH_DATA_DIR/entry-address` is not an IPv4 address | use an IPv4 address or unset it (default `127.0.0.1`) |
| `Usage: linux.sh install\|start\|connect\|...` | unknown or missing subcommand | there is no `uninstall` subcommand; see [Uninstall and upgrade](uninstall-upgrade.md) |
| `notice: using legacy default data directory ...` | neither `BERTH_DATA_DIR` nor the legacy name is set, `~/.local/share/berth` does not exist, but the earlier default directory does | set `BERTH_DATA_DIR` explicitly (see [migration](uninstall-upgrade.md#migrating-from-legacy-names)) |

## 2. `install-k3s.sh` refuses to run

The installer checks its preconditions before changing anything and exits with status 2 (or 1 for `sudo`).

| Message | Cause | Fix |
|---|---|---|
| `Set BERTH_DATA_DIR (deprecated alias: HAN_SOLO_DATA_DIR) to a dedicated Linux data directory` | variable not set | `export BERTH_DATA_DIR="$HOME/.local/share/berth"` |
| `Set BERTH_CONTEXT (deprecated alias: HAN_SOLO_CONTEXT) to a dedicated name` | variable not set | `export BERTH_CONTEXT=berth-local` |
| `Use a dedicated berth-* context (legacy hansolo-* is still accepted)` | context name does not match `berth-[a-z0-9-]+` | use e.g. `berth-local` (lowercase, digits, hyphens) |
| *(no message, exit status 2)* right after start | `BERTH_DATA_DIR` resolves to a path under `/mnt/`, to `/`, or to `$HOME` itself | use a dedicated directory on the Linux filesystem, e.g. `$HOME/.local/share/berth` |
| `This reference installer was validated on amd64 only` | the machine is not x86_64 | not supported by this installer |
| `Enable systemd in the dedicated WSL distribution` | PID 1 is not systemd | enable systemd for the distribution ([Microsoft: systemd in WSL](https://learn.microsoft.com/en-us/windows/wsl/systemd)), restart that distribution, confirm with `ps -p 1 -o comm=` |
| `sudo: a password is required` | the installer runs `sudo -n true` and needs passwordless or already-cached sudo | run `sudo -v` in the same terminal, then start the installer right away |
| `K3s already exists. Reuse the explicitly configured context; this installer will not overwrite it.` | `/usr/local/bin/k3s` or a `k3s*` systemd unit already exists; the installer never reuses or overwrites another K3s | use the existing-cluster path in [linux-wsl.md](linux-wsl.md) (export `KUBECONFIG` and `BERTH_CONTEXT` for that cluster), or remove the old K3s first if it is yours and unused ([Uninstall and upgrade](uninstall-upgrade.md#remove-k3s)) |

On a kernel that exposes cgroup v1 the installer writes a kubelet setting (`failCgroupV1: false`) by itself; this is expected and not an error. See [WSL findings](linux-wsl.md#wsl-findings).

`render.py` (called by `install`/`start`) applies the same rule to the data directory and reports it as `runtime data must be on the Linux filesystem`.

## 3. Model download fails

`tools/download-rag-models.py` fetches only the pinned revisions listed in `locks/models.json` and checks the size and SHA256 of every file.

| Output | Meaning |
|---|---|
| `BAAI/bge-m3 pinned files verified` (and the same for `BAAI/bge-reranker-v2-m3`) then `No generative model downloaded` | success |
| `FAIL: model checksum mismatch: <file>` | a downloaded file differs in size or hash from the pin |
| a Python traceback from `huggingface_hub` | the download itself failed (network, proxy, Hugging Face unavailable) |

- **Check:** free disk space (`df -h "$BERTH_DATA_DIR"`; the pinned files are about 4.3 GiB) and network access to `huggingface.co`.
- **Fix for a mismatch:** remove only the affected model folder, for example `rm -rf "$BERTH_DATA_DIR/models/models--BAAI--bge-m3"`, and run the tool again. Do not copy files from elsewhere to "make it pass".
- The tool never downloads a generative (answer) model; an LLM provider is configured separately after installation.

## 4. `ops/linux.sh install` stops or times out

`install` (identical to `start`) runs these steps and stops at the first one that fails:

| Step | Wait limit | Check when it fails |
|---|---|---|
| render manifests, apply namespace/Secret/ConfigMap/PostgreSQL/Redis | — | error text from `render.py` or `kubectl apply` |
| PostgreSQL (`cynovela-pgvector`) rollout | 300 s | `kb describe pod -l app=cynovela-pgvector` |
| Redis (`cynovela-redis`) rollout | 180 s | `kb describe pod -l app=cynovela-redis` |
| bootstrap Job `hansolo-bootstrap` | 300 s | `kb logs job/hansolo-bootstrap` |
| API (`cynovela`) rollout | 900 s | see [section 5](#5-api-pods-never-become-ready-the-900-s-wait) |
| worker (`cynovela-worker`) rollout | 600 s | `kb describe pod -l app=cynovela-worker`, `kb logs deploy/cynovela-worker` |

On success the last line is `Runtime ready. Run './ops/linux.sh connect' in a terminal; endpoint http://127.0.0.1:18765`.

**Bootstrap Job.** It is created with `backoffLimit: 0`, so a single failure is final for that run. Messages it can print:

| Log line | Meaning |
|---|---|
| `Fresh PostgreSQL administrator initialized; no source database imported` | first install succeeded |
| `Existing users retained; bootstrap is a no-op` | re-install; users and passwords are never changed |
| `Fresh install requires an initial admin password of at least 12 characters` | `$BERTH_DATA_DIR/secrets/admin_password` was edited to something shorter |
| `Required PostgreSQL schema was not applied` | the schema step failed; read the preceding lines |

`install` deletes and recreates the Job every time, so after fixing the cause simply run `./ops/linux.sh install` again.

**`ImagePullBackOff` / `ErrImagePull`.** The pods use `imagePullPolicy: IfNotPresent`. If `IMAGE` does not exactly match the reference stored in K3s's containerd, Kubernetes tries to pull it from a registry and fails. Check with `sudo k3s ctr images ls | grep berth` and `kb get deploy cynovela -o jsonpath='{.spec.template.spec.containers[0].image}'`. A Docker `save` imports as `docker.io/library/<name>:<tag>`, a Podman `save --format docker-archive` as `localhost/<name>:<tag>` (table in [linux-wsl.md](linux-wsl.md)). Export the right `IMAGE` and run `install` again.

## 5. API pods never become Ready (the 900 s wait)

The API has a startup allowance of 90 × 10 s (15 minutes); `install` waits 900 s for its rollout. Normal start takes about a minute once the image and models are in place.

```bash
kb get pods -o wide
kb describe pod -l app=cynovela          # Events: scheduling, image, probe failures
kb logs deploy/cynovela --tail=100
kb logs deploy/cynovela --previous --tail=100   # the run before the last restart
kb get events --sort-by=.lastTimestamp | tail -20
```

| What you see | Cause | Fix |
|---|---|---|
| `Pending`; events mention insufficient memory or CPU | the node cannot fit the resource requests (each API pod requests 2 GiB, each worker 768 MiB) | free memory in WSL or give WSL more; see [section 6](#6-memory-pressure-and-oomkilled) |
| `ImagePullBackOff` | image reference mismatch | see [section 4](#4-opslinuxsh-install-stops-or-times-out) |
| restarts, `CrashLoopBackOff`; the log ends with `モデル不在のため起動中止 (exit 2)` ("model missing, start aborted") after a list of missing models | the API runs with `HF_HUB_OFFLINE=1` and never downloads models; `bge-m3` is not found under `$BERTH_DATA_DIR/models` (mounted read-only at `/app/store/models`) | run `tools/download-rag-models.py --cache "$BERTH_DATA_DIR/models"` (step in [linux-wsl.md](linux-wsl.md)); confirm `ls "$BERTH_DATA_DIR"/models/models--BAAI--bge-m3/snapshots/` is not empty; then `./ops/linux.sh restart` |
| same as above although the files exist | the pods run as uid `10001` and read the hostPath read-only; directories must be `o+rx` and files `o+r` on the node | `find "$BERTH_DATA_DIR/models" -type d -exec chmod o+rx {} +` and `find "$BERTH_DATA_DIR/models" -type f -exec chmod o+r {} +`, then `./ops/linux.sh restart` |
| `Running` but `0/1` for many minutes right after a WSL restart | slow cold recovery on cgroup v1 | see [section 8](#8-wsl-was-shut-down-pods-restarted) |

After the fix, `./ops/linux.sh restart` waits again (900 s API, 600 s worker); `./ops/linux.sh verify` must print `replicas PASS`, `health PASS` and `pgvector PASS` (it needs `connect` or `serve` running).

## 6. Memory pressure and OOMKilled

Resource settings as shipped: API 2 replicas × (request 2 GiB, limit 6 GiB); worker 2 replicas × (request 768 MiB, limit 6 GiB); PostgreSQL and Redis have no limits. Reserve 16–24 GiB for WSL as stated in [linux-wsl.md](linux-wsl.md).

Checks:

```bash
kb get pods                                   # RESTARTS column
kb get pod -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].lastState.terminated.reason}{" "}{.status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}{end}'
kubectl --context "$BERTH_CONTEXT" describe node | grep -A6 Conditions   # MemoryPressure
free -h                                       # memory seen by WSL
```

- `OOMKilled` (exit code 137) in the last state means the container hit its memory limit or the node ran out of memory.
- `kubectl top` needs the K3s metrics server; if it prints `error: Metrics API not available`, use the commands above instead.
- A last state of `Unknown` with exit code 255 is **not** an out-of-memory event; it is what a WSL shutdown leaves behind ([section 8](#8-wsl-was-shut-down-pods-restarted)).

Fix: give WSL more memory, stop other workloads in the distribution, or stop Berth when not in use (`./ops/linux.sh stop`). The shipped configuration already limits masking to one parallel task and keeps the worker on the lightweight embedding mode.

## 7. The browser loses the connection

`http://127.0.0.1:18765` is served by `kubectl port-forward` to one API pod. When that pod is replaced (restart, rollout, upgrade, WSL restart) the forward breaks. Messages seen in that case include:

```text
error: lost connection to pod
error: error upgrading connection: unable to upgrade connection: pod does not exist
```

- `./ops/linux.sh connect` exits then; start it again.
- `./ops/linux.sh serve` (used by the Windows launcher) restarts the forward after 3 seconds by itself; these lines in its log are expected during pod replacement.
- **Port already in use:** if another program or an old forward holds 18765, the new forward cannot listen. Check with `ss -ltnp | grep 18765` in Ubuntu; stop the old forward, or pick another port with `BERTH_ENTRY_PORT` (and `-Port` for the launcher).
- `ops/linux.sh verify` checks `http://127.0.0.1:$BERTH_ENTRY_PORT`; it fails with a `curl` error when no forward is running.

## 8. WSL was shut down; pods restarted

WSL stops a distribution when no Windows client keeps it alive; systemd services alone do not. After `wsl --terminate`, `wsl --shutdown`, idle shutdown or a host restart:

- The K3s unit `k3s-<context>` starts again through systemd as soon as the distribution starts, and the pods restart. `kb get pods` shows an increased `RESTARTS` count, and the last state of the containers is `Unknown` with exit code 255.
- On a cgroup-v1 kernel, cold recovery can take up to 15 minutes. Do not restart K3s repeatedly while it is making progress; that restarts the scan.
- Check: `systemctl status k3s-$BERTH_CONTEXT`, `kb get pods -w`, then `./ops/linux.sh verify`.
- Keep the distribution running with the [Windows launcher](windows-launcher.md), and start it again after a shutdown.

## 9. Windows launcher behaviour

| Output / situation | Meaning |
|---|---|
| `Started dedicated WSL launcher: PID <n>, endpoint http://127.0.0.1:<port>` | a hidden `wsl.exe` now runs `ops/linux.sh serve` |
| `Already running: PID <n>` | the recorded process (same PID, name `wsl`, same start time) is still alive; nothing new is started |
| `Dedicated launcher stopped; no WSL distribution was terminated.` | `-Action stop` stopped only the recorded process and removed `launcher.json`; K3s and the pods keep running |
| `Migrated launcher state from ...\HanSolo\<context> to ...\Berth\<context>` | state from an earlier launcher version was copied once; the old folder is left in place |
| PowerShell refuses to run the script from `\\wsl$\...` | the execution policy treats a UNC share as remote; copy the script to a local folder (see [Windows launcher](windows-launcher.md)) |
| parameter validation error | `-Repository` and `-Data` must be absolute Linux paths without spaces; `-Context`/`-Namespace` lowercase letters, digits and hyphens |

Logs and state live in `%LOCALAPPDATA%\Berth\<context>\`: `launcher.json`, `forward.log`, `forward-error.log`. Forward errors (section 7) appear in `forward-error.log`.

- If the page does not load although the launcher says it is running, look at `forward-error.log`, then run `ops/linux.sh status` inside Ubuntu with the same context and namespace.
- If `launcher.json` points at a process that no longer exists (for example after a Windows restart), `start` simply starts a new launcher.
- The launcher passes `KUBECONFIG=<Data>/client.yaml`. For a cluster not installed by `install-k3s.sh`, place a kubeconfig containing the context at that path or use `ops/linux.sh serve` directly.

## 10. Backup and restore errors

Messages from `deploy/k8s/pg-backup.sh` (called by `ops/linux.sh backup` / `restore`):

| Message | Cause | Fix |
|---|---|---|
| `FAIL: paired encryption key required` | `$BERTH_DATA_DIR/secrets/secret_key` is missing or empty | check `BERTH_DATA_DIR`; the key is created by the first `install` |
| `FAIL: PostgreSQL pod missing`, or an exit status 1 with no message | no PostgreSQL pod exists (for example after `ops/linux.sh stop`, which scales PostgreSQL to 0) | start the runtime again (`./ops/linux.sh start`) before backup/restore; check with `kb get pods -l app=cynovela-pgvector` |
| `Usage: pg-backup.sh restore DUMP --yes` | the dump path or the `--yes` confirmation is missing | `./ops/linux.sh restore <dump> --yes` |
| `FAIL: nonempty dump and checksum sidecar required` / `FAIL: dump checksum mismatch` | the `.sha256` file next to the dump is missing, or the dump changed | copy the dump together with its `.sha256` and `secret.key-<timestamp>` files |
| `FAIL: paired backup key and checksum required` / `FAIL: paired backup key checksum mismatch` | the `secret.key-<timestamp>` file of this backup is missing or altered | restore the complete backup set |
| `FAIL: restore the paired key first; never regenerate it` | destination key file is missing | copy the paired key to `$BERTH_DATA_DIR/secrets/secret_key` |
| `FAIL: destination encryption key does not match backup` | the installation uses a different key | copy the paired key to the destination secret file and update the Kubernetes Secret before starting restored workloads ([linux-wsl.md](linux-wsl.md#operations-and-recovery)); never generate a new key |
| `FAIL: required tables missing` / `FAIL: pgvector missing` | the dump is not a Berth database dump | use a dump created by `ops/linux.sh backup` |

A successful backup ends with `backup: <path>`; a successful restore with `restore: PASS`. Every restore first loads the dump into a temporary database for validation before it touches the live database.

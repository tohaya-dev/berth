# Stop, upgrade and uninstall

`ops/linux.sh` has no `uninstall` subcommand. This page shows how to stop Berth, upgrade it, and remove it by hand, using only what the scripts provide. Steps marked **DESTRUCTIVE** delete data that cannot be recovered without a backup.

Commands assume the environment from [linux-wsl.md](linux-wsl.md), run inside Ubuntu from the repository root:

```bash
export BERTH_CONTEXT=berth-local
export BERTH_NAMESPACE=berth
export BERTH_DATA_DIR="$HOME/.local/share/berth"
export KUBECONFIG="$BERTH_DATA_DIR/client.yaml"
export PYBIN="$PWD/.venv/bin/python"
```

Export `BERTH_NAMESPACE` if you installed into a namespace other than the default `berth` (see [Troubleshooting](troubleshooting.md#1-environment-not-set-context-namespace-data-directory)).

## What lives where

| Item | Location | Created by |
|---|---|---|
| Database (users, workspaces, chunks, vectors) | PostgreSQL volume (PVC `cynovela-pg-data`) under `$BERTH_DATA_DIR/volumes` | `ops/linux.sh install` |
| Redis queue data | PVC `cynovela-redis-data` under `$BERTH_DATA_DIR/volumes` | `ops/linux.sh install` |
| Secrets: `admin_password`, `pg_password`, `secret_key` | `$BERTH_DATA_DIR/secrets/` (mode 600) | first `install`; never regenerated while present |
| Rendered manifests | `$BERTH_DATA_DIR/rendered/` | every `install`/`start` |
| RAG models (about 4.3 GiB) | `$BERTH_DATA_DIR/models/` | `tools/download-rag-models.py` |
| Ingest folders | `$BERTH_DATA_DIR/ingest/` | you / `scripts/add-ingest-folder.sh` |
| K3s state and kubeconfigs | `$BERTH_DATA_DIR/k3s/`, `$BERTH_DATA_DIR/kubeconfig`, `$BERTH_DATA_DIR/client.yaml` | `install-k3s.sh` |
| K3s binary, service and uninstall script | `/usr/local/bin/k3s`, `/etc/systemd/system/k3s-<context>.service` (+ `.service.d/10-dns.conf`), `/usr/local/bin/k3s-<context>-uninstall.sh` | `install-k3s.sh` via the official K3s installer |
| Imported image | K3s containerd (`sudo k3s ctr images ls`) | `sudo k3s ctr images import` |
| Windows launcher state | `%LOCALAPPDATA%\Berth\<context>\` | `ops/windows-wsl.ps1` |
| Backups | `$PG_BACKUP_DIR` (default `~/dt-backups/k8s-pg`) | `ops/linux.sh backup` |

`$BERTH_DATA_DIR/k3s` and `$BERTH_DATA_DIR/volumes` are owned by root.

## Stop and start

Stop the Windows launcher (PowerShell, same parameters as for start; `$linuxHome` as in [Windows launcher](windows-launcher.md)):

```powershell
.\windows-wsl.ps1 -Action stop -Repository "$linuxHome/Projects/berth" `
  -Data "$linuxHome/.local/share/berth" -Context berth-local -Namespace berth
```

It stops only its own recorded process and prints `Dedicated launcher stopped; no WSL distribution was terminated.` K3s and the pods keep running.

Stop the Berth workloads (data is kept):

```bash
./ops/linux.sh stop     # scales API, worker, Redis and PostgreSQL to 0 replicas
```

K3s itself keeps running. Start again with the same image reference; `start` is the same as `install` and re-renders the manifests:

```bash
kubectl --context "$BERTH_CONTEXT" -n "$BERTH_NAMESPACE" get deploy cynovela \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'   # current IMAGE (run before stop, or read it now)
export IMAGE=<that reference>
./ops/linux.sh start
```

`./ops/linux.sh restart` only restarts the API and worker; it does not scale anything back up after `stop`.

To stop Kubernetes as well, stop the dedicated unit: `sudo systemctl stop k3s-$BERTH_CONTEXT`. It is enabled and starts again with the distribution. K3s documents that stopping the service can leave containers running and provides `/usr/local/bin/k3s-killall.sh` to stop them ([K3s: stopping K3s](https://docs.k3s.io/upgrades/killall)); that script stops every K3s service on the machine.

## Back up first

Before an upgrade or removal:

```bash
export PG_BACKUP_DIR="$HOME/berth-backups"
./ops/linux.sh backup
```

The runtime must be started (the backup needs the PostgreSQL pod). A backup set is three files: `pgdump-<ts>.dump`, `pgdump-<ts>.sha256` and `secret.key-<ts>`; keep them together in protected storage. The dump contains relational data and vectors. It does **not** contain the models (download again), your original ingest files, or `$BERTH_DATA_DIR/secrets/admin_password` and `pg_password`. Restore is described in [linux-wsl.md](linux-wsl.md#operations-and-recovery).

## Upgrade

1. Back up (above).
2. Update the source (for example `git pull` in the clone) and read the release notes of the new version.
3. Build and import the new image with a **new tag**, as in [linux-wsl.md](linux-wsl.md):
   ```bash
   docker build --platform linux/amd64 --build-arg APT_UPGRADE=1 \
     --build-arg REQUIREMENTS_FILE=locks/requirements-linux-amd64.txt \
     -f deploy/container/Containerfile -t berth:<new-tag> .
   docker save berth:<new-tag> -o berth-<new-tag>.tar
   sudo k3s ctr images import berth-<new-tag>.tar
   sudo k3s ctr images ls | grep berth
   export IMAGE=docker.io/library/berth:<new-tag>
   ```
4. If `locks/models.json` changed, run `tools/download-rag-models.py --cache "$BERTH_DATA_DIR/models"` again.
5. Run `./ops/linux.sh install`.
6. Reconnect (`./ops/linux.sh connect`, or let `serve`/the launcher reconnect) and run `./ops/linux.sh verify`.

What a repeated `install` does:

- keeps the existing files in `$BERTH_DATA_DIR/secrets` (they are only created when missing);
- re-applies the namespace, Secret, ConfigMap, PostgreSQL and Redis;
- deletes and re-runs the bootstrap Job, which applies the database schema and, because users exist, prints `Existing users retained; bootstrap is a no-op` (users and passwords are not changed);
- applies the API and worker Deployments with the new `IMAGE`; the API is replaced as a rolling update (one extra pod at a time, none removed before its replacement is ready), then `install` waits up to 900 s for the API and 600 s for the worker.

Why a new tag: the pods use `imagePullPolicy: IfNotPresent`, and if `IMAGE` is unchanged the Deployments do not change, so no new pods start. If you did re-use a tag, run `./ops/linux.sh restart` after the import.

Rolling back: export the previous `IMAGE` (still listed by `sudo k3s ctr images ls` unless you removed it) and run `./ops/linux.sh install` again. If the newer version changed the database in a way the older one cannot use, restore the backup taken in step 1.

## Uninstall

Do the steps in this order. Skip the ones that do not apply.

### Windows side

1. Stop the launcher (`-Action stop`, above).
2. Delete its state folder `%LOCALAPPDATA%\Berth\<context>` (only PID metadata and forward logs). If a folder `%LOCALAPPDATA%\HanSolo\<context>` from an earlier launcher version exists, it was copied, not moved, and can be deleted as well.
3. Delete your local copy of `windows-wsl.ps1` and any wrapper script you wrote for it.

### Remove Berth from the cluster

**DESTRUCTIVE.** Deleting the namespace deletes the PostgreSQL and Redis volumes. The `local-path` storage class of K3s uses the reclaim policy `Delete`, so the database files under `$BERTH_DATA_DIR/volumes` are removed too.

```bash
kubectl --context "$BERTH_CONTEXT" get namespaces           # confirm the name first
kubectl --context "$BERTH_CONTEXT" delete namespace "$BERTH_NAMESPACE"
```

This is the only step needed on a cluster that you did not create with `install-k3s.sh`; stop here in that case and keep that cluster.

### Remove K3s

**DESTRUCTIVE.** Only for a K3s created by `deploy/k8s/linux/install-k3s.sh`. It removes the whole cluster, including everything else running on it.

`install-k3s.sh` uses the official K3s installer with the service name `k3s-<context>`, which places an uninstall script at `/usr/local/bin/k3s-<context>-uninstall.sh`. Read it before running it (`less /usr/local/bin/k3s-$BERTH_CONTEXT-uninstall.sh`).

The script cleans `/var/lib/rancher/k3s` unless `K3S_DATA_DIR` is set, but this installation keeps its K3s data in `$BERTH_DATA_DIR/k3s`. Pass that path (the script re-runs itself with `sudo` and keeps this variable):

```bash
K3S_DATA_DIR="$BERTH_DATA_DIR/k3s" /usr/local/bin/k3s-$BERTH_CONTEXT-uninstall.sh
```

It stops K3s and its containers, disables and removes the service, and removes `/usr/local/bin/k3s`, the `kubectl`/`crictl`/`ctr` links, `/etc/rancher/k3s`, `/var/lib/kubelet` and the data directory given. If other `k3s*` services exist it prints `Additional k3s services installed, skipping uninstall of k3s` and leaves the binary in place.

The DNS drop-in written by `install-k3s.sh` is not part of the official installer; remove it yourself:

```bash
sudo rm -rf "/etc/systemd/system/k3s-$BERTH_CONTEXT.service.d"
sudo systemctl daemon-reload
```

### Remove the data directory

**DESTRUCTIVE.** This removes secrets, models, ingest copies, rendered manifests, kubeconfigs and any remaining volumes. Copy anything you still need (backups, `secrets/secret_key` paired with a backup, ingest files) first. Parts of the directory are owned by root.

```bash
echo "$BERTH_DATA_DIR"                 # must print the intended directory, never empty
sudo rm -rf -- "$BERTH_DATA_DIR"
```

### Remaining items

- Image archives (`berth-*.tar`) and images in your builder (`docker image rm berth:<tag>` or `podman image rm localhost/berth:<tag>`).
- Backups in `$PG_BACKUP_DIR` (default `~/dt-backups/k8s-pg`) — delete only when no longer needed.
- The clone, including `.venv`.
- Only if the WSL distribution was dedicated to Berth and holds nothing else: `wsl --unregister Ubuntu-24.04` in PowerShell. **DESTRUCTIVE:** it deletes the entire distribution and all files in it.

## Migrating from legacy names

Earlier versions used `HAN_SOLO_*` variables and `HanSolo` folder names. The current scripts still accept them:

| Earlier | Current | Behaviour |
|---|---|---|
| `HAN_SOLO_CONTEXT`, `HAN_SOLO_NAMESPACE`, `HAN_SOLO_DATA_DIR`, `HAN_SOLO_ENTRY_PORT`, `HAN_SOLO_ENTRY_ADDRESS` | `BERTH_*` with the same suffix | `ops/linux.sh` reads both; when both are set, `BERTH_*` wins |
| `HAN_SOLO_DATA_DIR`, `HAN_SOLO_CONTEXT`, `HAN_SOLO_KUBE_PORT` | `BERTH_*` | same rule in `install-k3s.sh` |
| `HAN_SOLO_SECRET_FILE` | `BERTH_SECRET_FILE` | same rule in `pg-backup.sh`; `ops/linux.sh` sets both |
| default data directory `~/.local/share/hansolo` | `~/.local/share/berth` | used only if neither variable is set and `~/.local/share/berth` does not exist; `ops/linux.sh` prints `notice: using legacy default data directory ...` |
| context names `hansolo-*` | `berth-*` | still accepted by `install-k3s.sh` |
| `%LOCALAPPDATA%\HanSolo\<context>` | `%LOCALAPPDATA%\Berth\<context>` | the launcher copies the files over on its next start or stop; the old folder is left in place |

Recommended migration:

1. Replace `HAN_SOLO_*` with `BERTH_*` in your own scripts and env files. The Windows launcher already passes both.
2. Keep the existing data directory and point `BERTH_DATA_DIR` at it explicitly (this also silences the notice). Do **not** move or rename it: the K3s service (`--data-dir`, local volume path) and the rendered hostPath mounts refer to the absolute path.
3. Keep the existing context name; renaming it would also rename the K3s service and uninstall script, which are created once by the installer.
4. After the launcher has started once and `%LOCALAPPDATA%\Berth\<context>\launcher.json` exists, delete `%LOCALAPPDATA%\HanSolo\<context>`.

Some Kubernetes object names keep their original form (for example the bootstrap Job `hansolo-bootstrap` and the Deployments `cynovela*`); they are internal names and need no action.

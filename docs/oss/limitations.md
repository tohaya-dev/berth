English | [日本語](limitations.ja.md)

# Known limitations

What this release of Berth does not do, or does only in part. Every item below follows from the shipped scripts, manifests or application code. For host sizing see [System requirements](requirements.md); for fixes to concrete symptoms see [Troubleshooting](troubleshooting.md).

## Platform scope

- **Linux x86_64 only.** `deploy/k8s/linux/install-k3s.sh` stops on any other architecture, and only the amd64 image has been built and acceptance-tested. macOS, arm64 and native Windows containers are not supported by this release.
- **Tested on Windows 11 + WSL2 Ubuntu 24.04.** Native Ubuntu 24.04 uses the same adapter, but its bare-metal acceptance is still pending; Ubuntu 22.04 was not tested. A Windows host reboot has not been tested (see the [Windows launcher](windows-launcher.md)).
- **systemd and non-interactive sudo are required.** The installer checks that PID 1 is systemd and runs `sudo -n true`.
- **One K3s per distribution.** The installer refuses to run when `/usr/local/bin/k3s` or any `k3s*` systemd unit already exists; it never reuses or overwrites another cluster.
- **Single node.** Models and ingest documents are `hostPath` volumes on the node, and PostgreSQL/Redis use K3s local-path storage. Multi-node clusters are not validated.

## Availability (what is not HA)

- PostgreSQL + pgvector and Redis each run as **one replica** with the `Recreate` strategy. When either pod is restarted, the application is unavailable until it is back.
- The API and the worker run two replicas each on the same node. This allows rolling restarts; it does not protect against the loss of the node, the WSL distribution or the Windows host.
- `ops/linux.sh stop` scales the application, Redis and PostgreSQL to 0. K3s itself keeps running.
- The entry point is a `kubectl port-forward`. `connect` ends when its selected pod exits and must be started again; `serve` (used by the Windows launcher) reconnects in a loop. Under WSL2 the distribution stays up only while a Windows WSL client is running, which is what the launcher provides.
- After `wsl --terminate`, cold recovery on the tested cgroup-v1 host took up to 15 minutes.

## Resources

- API pods: 2Gi memory request, 6Gi limit each. Worker pods: 768Mi request, 6Gi limit each. PostgreSQL and Redis have no requests or limits. There are no CPU limits. Details and totals: [System requirements](requirements.md).
- To keep memory use down, the renderer fixes `masking.parallelism = 1` and `worker.embedding_mode = minimal`. Documents are masked one task at a time, so large ingests take longer.
- The PostgreSQL volume requests 2Gi and the Redis volume 1Gi.

## Models and network

- **Models must be downloaded before installation.** `tools/download-rag-models.py` fetches the pinned embedding and reranker models from Hugging Face into `$BERTH_DATA_DIR/models`. The image runs with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`. Pods mount the model directory read-only and never download a model, so missing models cannot be fetched at runtime. See the [model policy](model-policy.md).
- **Installation needs internet access**: the K3s installer (`get.k3s.io`), Hugging Face, the image build (base image, Debian packages, PyPI, the PyTorch CPU index, spaCy model wheels) and the `pgvector/pgvector:pg16` and `redis:7.2-alpine` images.
- **No answer-generation model is included.** A fresh install points at an unreachable loopback endpoint, so chat returns a provider error until you configure an external provider. When you set one, retrieved document text is sent to it. If the provider is remote, that text leaves the machine.
- **CPU only.** The image installs the CPU build of PyTorch, and the manifests request no GPU.

## Ingest formats and limits

Documents enter only through folders under `$BERTH_DATA_DIR/ingest`, which the pods mount read-only. There is no browser upload for source documents. Symbolic links inside those folders are followed and resolved inside the container, so do not place links there.

| Format | Extensions | Limitation |
|---|---|---|
| Text | `.txt` `.md` | read as UTF-8; undecodable bytes are dropped |
| CSV | `.csv` | only the **first 50 rows** are indexed (at most 10,000 rows are read) |
| PDF | `.pdf` | default ("fast") mode reads the text layer only. **Scanned PDFs without a text layer yield no text.** The "vision" mode needs a vision-capable model endpoint. |
| Word | `.docx` | paragraphs and tables. Legacy `.doc` is **not** supported. |
| PowerPoint | `.pptx` | slide text and speaker notes. Legacy `.ppt` is not supported. |
| Excel | `.xlsx` | sheets with more than 100 rows are indexed as their **first 50 rows**. `.xls` is accepted by extension but opened with the `.xlsx` reader, which cannot read the legacy binary format. Convert such files to `.xlsx`. |
| HTML / e-mail | `.html` `.htm` `.eml` | text only; scripts and styles are removed |
| ZIP | `.zip` | supported files inside are extracted one level deep; nested archives are skipped |
| Images | `.jpg` `.jpeg` `.png` `.heic` `.webp` `.gif` | by default only the **file name** is indexed. Image description needs `image.processing_mode = lm_studio` and a vision endpoint. Remote vision endpoints are blocked unless `allow_remote_vlm` is set. The `caption` mode uses an Apple-silicon library and does not apply to this Linux image. |

Other extensions are skipped as unsupported. The scan enforces no per-file size limit and no extraction timeout, so a very large file takes as long as it needs (a stop request is still honoured) and uses worker memory within the 6Gi limit.

## Masking and personal data

- Detection uses Microsoft Presidio with the spaCy models `en_core_web_sm` / `ja_core_news_sm`. In `lite` mode it covers e-mail addresses, phone numbers and dates/times. `standard` and `quality` add Japanese person, organisation, location and address names. If Presidio cannot be loaded, the code falls back to regular expressions for e-mail, phone, IP address, card-like and 12-digit ID-like numbers and internal URLs. Detection is statistical and pattern-based. **It can miss personal data and can mask text that is not personal data.** Do not treat masking as a guarantee.
- The mode (`lite`, `standard`, `quality`) comes from `pii_mode` in `cynovela.yaml` (default `standard`). On Kubernetes that file is mounted from a ConfigMap. A change made in Settings applies only to the API process that handled the request and is not persisted (the response reports `yaml_persisted: false`). To change the mode permanently, edit `pii_mode` in the repository's `cynovela.yaml` and run `./ops/linux.sh start`, which re-renders the ConfigMap.

## Authentication and access control

- There are two roles, `admin` and `viewer`, plus workspace/collection access rules and scoped API keys for the CLI and HTTP MCP.
- A fresh install creates one administrator, `cynovela`, with a generated password (`$BERTH_DATA_DIR/secrets/admin_password`). It is not forced to change it at first login.
- Passwords are hashed with PBKDF2-HMAC-SHA256 at 100,000 iterations. This is below current OWASP guidance for PBKDF2.
- Login tokens are JWTs valid for 8 hours by default. Login, token refresh, password change and password verification are each limited to 5 requests per minute per client address and API pod; other endpoints have no rate limit. Through the default `kubectl port-forward` entry every client reaches the pod from the loopback address, so all clients share that limit (repeated failed logins can block logins for everyone for a minute) and audit records show `127.0.0.1` as the client address.
- An administrator's password reset does not force the user to change the new password. API keys do not expire; revoke keys that are no longer needed.
- An LLM API key is stored encrypted in the database only for the `openai_compat` provider. For other providers, a key typed into Settings is kept only in the memory of the API pod that received it. It is not shared with the second API replica and is lost when that pod restarts.

## Backup and restore

- `ops/linux.sh backup` dumps the **PostgreSQL database only** (relational data and vectors) and copies the paired `secret_key`. It does **not** include the models, the original ingest files, the Redis queue, the other secret files or the rendered manifests.
- The dump is not encrypted. It sits next to a copy of the key that decrypts the stored document text, so protect the backup directory accordingly.
- There is no scheduled backup. Without `PG_BACKUP_DIR`, dumps go to `$HOME/dt-backups/k8s-pg`.
- `restore DUMP --yes` requires the destination key to match the backup key, and you must scale the API and workers to 0 yourself first. See the [Linux / WSL2 guide](linux-wsl.md#operations-and-recovery).
- Application logs and other runtime files in the API/worker container layer are lost when a pod is replaced; use `kubectl logs` while the pod exists.

## Network exposure

- The entry port-forward binds `127.0.0.1:18765` by default. `BERTH_ENTRY_ADDRESS` (or `$BERTH_DATA_DIR/entry-address`) widens it, and the forward does no source filtering.
- The K3s API server listens on **`0.0.0.0:26443`** (`--bind-address 0.0.0.0`). Whether other machines can reach it depends on your WSL networking mode and firewall. Protect it with host and network controls. K3s also opens its own node ports.
- The application Service is `ClusterIP`. No Kubernetes NetworkPolicy is rendered.

## Operations

- **There is no uninstall command.** Removal is manual; see [Uninstall and upgrade](uninstall-upgrade.md).
- `ops/linux.sh` does not remember the namespace you installed into: it uses `BERTH_NAMESPACE`, default `berth`. If you installed into another namespace, export `BERTH_NAMESPACE` in every shell, or commands act on a different (empty) namespace.
- The API and worker pods have `TZ=Asia/Tokyo` set in the manifests.
- Several Kubernetes object names keep their historical names: the Deployments `cynovela`, `cynovela-worker`, `cynovela-pgvector`, `cynovela-redis`, the Service `cynovela-svc`, the Secret `cynovela-secret` and the bootstrap Job `hansolo-bootstrap`.

## Legacy name fallbacks

These keep installations made under the former name working and may be removed later:

- `HAN_SOLO_*` environment variables are accepted as deprecated aliases of `BERTH_*`; `BERTH_*` wins when both are set.
- When neither `BERTH_DATA_DIR` nor `HAN_SOLO_DATA_DIR` is set and `~/.local/share/berth` does not exist, `ops/linux.sh` uses `~/.local/share/hansolo` if present and prints a notice.
- The installer accepts context names `hansolo-*` as well as `berth-*`.
- The Windows launcher copies state from `%LOCALAPPDATA%\HanSolo\<context>` into `%LOCALAPPDATA%\Berth\<context>` once; the old directory is left in place.
- The source tree still contains standalone paths (SQLite/Chroma defaults in `cynovela.yaml`). The Kubernetes renderer overrides them with PostgreSQL/pgvector and Redis; they are not the active Berth architecture.

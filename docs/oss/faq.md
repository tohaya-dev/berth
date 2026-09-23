English | [日本語](faq.ja.md)

# Frequently asked questions

Answers are limited to what the shipped scripts, manifests and code do. Start with [Start here](start-here.md) if you have not installed Berth yet.

## Installation and platform

### Can I run Berth on macOS or on native Windows?

Not in this release. The shipped path is K3s on Linux x86_64: Windows 11 with a WSL2 Ubuntu 24.04 distribution (tested), or native Ubuntu 24.04 (adapter available, bare-metal acceptance pending). The K3s installer stops on other architectures and requires systemd. Native Windows containers are not supported. See [Known limitations](limitations.md#platform-scope).

### Why does installation need internet access?

The installer and the image build fetch components from the network:

- the K3s installer from `get.k3s.io` and the pinned K3s release;
- the embedding and reranker models from Hugging Face (`tools/download-rag-models.py`);
- for the image build, the `python:3.12-slim` base image, Debian packages, Python packages from PyPI and the PyTorch CPU index, and the spaCy model wheels;
- the `pgvector/pgvector:pg16` and `redis:7.2-alpine` images, pulled by K3s at deployment.

Once installed, Berth does not download models at runtime (the image sets `HF_HUB_OFFLINE=1`). Its outbound traffic goes to the LLM provider you configure and to any optional integration you enable.

### How big is the download?

The pinned models are 4,586,590,960 bytes (about 4.3 GiB) in total; the per-model sizes are in the [model policy](model-policy.md#size-revisions-and-storage). The imported application image and the disk to reserve are described in [System requirements](requirements.md#disk). The source tree itself contains no model weights.

### Can I use an existing Kubernetes cluster?

Yes, if it is a non-production cluster you control. Skip `install-k3s.sh`, export `KUBECONFIG` to your kubeconfig and set `BERTH_CONTEXT` to a context in it ([Linux / WSL2 guide](linux-wsl.md#fresh-installation)). Models and ingest documents are `hostPath` volumes, so only a single node is supported.

### Can I use a GPU?

Not for Berth itself. The image installs the CPU build of PyTorch, and the manifests request no GPU. The embedding and reranker models run on CPU. An external LLM provider can use its own GPU.

## Models and LLM

### Is an answer-generation LLM included?

No. Berth downloads only the embedding and reranker models used for retrieval; the PII/NER models are part of the image's Python dependencies. After installation, configure an external provider in Settings. Until then, chat returns a provider error, while ingestion and retrieval work.

### Which LLM providers can I connect?

The settings API accepts `lmstudio` (the default type), `ollama`, `openai_compat`, `openrouter` and `vllm`. All except `lmstudio` use the OpenAI-compatible adapter. The endpoint must start with `http://` or `https://`; link-local and cloud metadata addresses are refused. The endpoint is called from inside the API pod, so `localhost` or `127.0.0.1` there means the pod itself. Use an address that the pod can reach.

When you connect a provider, retrieved document text is sent to it. With a remote provider, that text leaves your machine.

### Where are the models stored?

In `$BERTH_DATA_DIR/models` (for example `$HOME/.local/share/berth/models`), in the Hugging Face cache layout (`models--BAAI--bge-m3/…`, `models--BAAI--bge-reranker-v2-m3/…`). The API and worker pods mount it read-only at `/app/store/models`. See the [model policy](model-policy.md).

### Can I replace the models with other ones?

This release pins the two models by revision and SHA256 in `locks/models.json`, and acceptance was run with them. Other models are not tested.

## Data

### Where is my data?

| Data | Location |
|---|---|
| Database (users, settings, chunks, vectors) | PostgreSQL volume under `$BERTH_DATA_DIR/volumes` (K3s local-path storage) |
| Job queue | Redis volume under `$BERTH_DATA_DIR/volumes` |
| Your original documents | `$BERTH_DATA_DIR/ingest/<folder>`, mounted read-only into the pods |
| Models | `$BERTH_DATA_DIR/models` |
| Generated secrets | `$BERTH_DATA_DIR/secrets/` (`admin_password`, `pg_password`, `secret_key`) |
| Rendered manifests | `$BERTH_DATA_DIR/rendered/` (includes the Secret) |
| K3s state and kubeconfig | `$BERTH_DATA_DIR/k3s/`, `$BERTH_DATA_DIR/client.yaml` |
| Backups | `PG_BACKUP_DIR` (default `$HOME/dt-backups/k8s-pg`) |
| Windows launcher state | `%LOCALAPPDATA%\Berth\<context>` (PID record and port-forward logs only) |

The database stores the original text of your documents encrypted with `secret_key`, next to a masked copy.

### Is there sample data?

No sample corpus is installed and no existing database is imported: the bootstrap Job creates the administrator in a fresh PostgreSQL database. The repository contains four small fixture files (`demo_data/d.txt`, `demo_data/p25/d.txt`, `ingest/test_pii_ingest.md`, `ingest/sub1/nested.txt`). They are not copied into `$BERTH_DATA_DIR/ingest`. The PII fixture contains only fictitious values. See [Before distributing](before-distributing.md#bundled-data).

### Which file formats can I ingest?

`.txt`, `.md`, `.csv`, `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.html`, `.htm`, `.eml`, `.zip` and common image formats, with limits (for example, only the first 50 rows of a CSV, and no text from scanned PDFs). See the [format table](limitations.md#ingest-formats-and-limits).

### How do I back up?

Set `PG_BACKUP_DIR` and run `./ops/linux.sh backup`. It writes a PostgreSQL dump, a checksum file and a copy of the paired encryption key, then verifies the dump in a temporary database. To restore, scale the API and workers to 0 and run `./ops/linux.sh restore DUMP --yes` ([Linux / WSL2 guide](linux-wsl.md#operations-and-recovery)). Keep models and original documents separately; the backup does not contain them.

## Access

### How do I sign in the first time?

Open `http://127.0.0.1:18765` while `./ops/linux.sh connect` (or the Windows launcher) is running. Sign in as `cynovela` with the password in `$BERTH_DATA_DIR/secrets/admin_password`.

### Can other computers reach Berth?

Not by default: the port-forward binds `127.0.0.1`. You can widen it with `BERTH_ENTRY_ADDRESS`, but the forward does no source filtering, so restrict the port with a firewall. The Kubernetes API port 26443 is bound on all interfaces; see [Known limitations](limitations.md#network-exposure).

### How do I remove Berth?

There is no uninstall command. See [Uninstall and upgrade](uninstall-upgrade.md).

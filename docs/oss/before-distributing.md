English | [日本語](before-distributing.ja.md)

# Before distributing or deploying for others

A checklist for anyone who redistributes Berth (source, a built image or a model cache) or runs it for other people. Berth is published as a source release candidate. **No application image is published**, so each operator builds their own image, and whoever passes that image on takes on the obligations below.

## Know what you are passing on

| Item | Included in this repository? | Your obligation when you pass it on |
|---|---|---|
| Berth source | yes | MIT: keep `LICENSE`, `NOTICE.md` and `THIRD_PARTY_NOTICES.md` |
| Vendored browser libraries (Chart.js, its plugins, marked) | yes | MIT; license texts are in [`docs/oss/licenses/`](licenses/) |
| Application image | no, you build it | it contains Debian packages, Python packages (for example LGPL-3.0 psycopg, see [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) and [`dependency-licenses.csv`](dependency-licenses.csv)) and the spaCy models below |
| spaCy `en_core_web_sm` 3.8.0 | inside the image (pip wheel at build time) | MIT |
| spaCy `ja_core_news_sm` 3.8.0 | inside the image (pip wheel at build time) | model data under **CC BY-SA 4.0** (derived from UD Japanese GSD): keep the attribution and the share-alike terms |
| BAAI/bge-m3 | no, downloaded by the user | MIT: include the copyright and permission notice if you redistribute the files |
| BAAI/bge-reranker-v2-m3 | no, downloaded by the user | Apache-2.0: include the license text and any NOTICE if you redistribute the files |
| Answer-generation LLM | no, never bundled | the provider's own terms |

**Models.** Berth does not bundle model weights. Each user runs `tools/download-rag-models.py`, which fetches pinned revisions from Hugging Face and verifies every file's SHA256 ([model policy](model-policy.md)). If you ship a model cache yourself, for example for an offline site, include the license texts above with it. Keep the pinned revisions so that `locks/models.json` still verifies. Never copy a model cache out of someone else's live deployment.

**Image vulnerabilities.** The audit of the release-candidate image still lists open findings, including ChromaDB advisories ([`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md)). Do not publish an image as cleared just because functional tests pass. Scan and triage the image you built.

## Keep secrets out of what you distribute

Each installation generates its own secrets the first time `ops/linux.sh install` renders the manifests. Never copy another installation's secrets.

| Secret | Where it lives |
|---|---|
| `admin_password`, `pg_password`, `secret_key` | `$BERTH_DATA_DIR/secrets/` (mode 600) |
| Kubernetes Secret `cynovela-secret` | in the cluster, and as plain `stringData` in `$BERTH_DATA_DIR/rendered/infrastructure.yaml` |
| PostgreSQL password inside the pods | mounted read-only at `/run/berth-secrets/pg_password` |
| `secret_key` inside the pods | environment variable `CYNOVELA_SECRET_KEY` from the Secret. It encrypts stored document originals, stored API tokens, and is used to derive the login-token signing key. |
| Cluster-admin kubeconfig | `$BERTH_DATA_DIR/client.yaml` and `$BERTH_DATA_DIR/kubeconfig` (mode 600) |
| Backups | `pgdump-*.dump` plus `secret.key-*` in `PG_BACKUP_DIR` |

Checklist:

- [ ] Never package, sync or share `$BERTH_DATA_DIR` or a backup directory. A backup contains the unencrypted SQL dump together with the key that decrypts the stored documents.
- [ ] Build the image from a clean checkout. The build copies the repository directory into the image, minus the entries in `.containerignore`. That file excludes `store/models`, `**/secret.key`, `_oss-rc-backups`, `.venv-oss` and `.env`. It does **not** exclude a `.venv` directory, a saved image archive such as `berth-amd64.tar`, or dumps kept elsewhere in the tree. Save archives and backups outside the repository.
- [ ] Never paste the generated passwords, `secret_key` or a scoped API key into issues, logs or commits.
- [ ] Scan what you publish for secrets and personal paths. The scanners used to produce this source release are part of the development tree and are not included here, so use a secret scanner of your choice. Look for private keys, GitHub, OpenAI-style and `cyn_` API keys, AWS keys, JWT literals, `secret.key*`, `*.dump` and database files.

## Accounts and initial passwords

- [ ] A fresh install creates one administrator, `cynovela`, with a random password in `$BERTH_DATA_DIR/secrets/admin_password`. Nothing forces a change at first login. Change it, or hand it over through a secure channel, before other people use the system.
- [ ] Create a personal account for each user with the least role they need (`viewer` unless they administer). Review the user list after installation and remove access for any account you did not create.
- [ ] For CLI and MCP integrations, issue scoped API keys instead of sharing the administrator password.
- [ ] Reinstalling keeps existing users and keys. The bootstrap never changes an existing password.

## Network exposure

- [ ] The web entry binds `127.0.0.1:18765` by default. If you widen it with `BERTH_ENTRY_ADDRESS`, restrict the port with a host firewall: the port-forward does no source filtering, and the traffic is plain HTTP.
- [ ] The K3s API server listens on `0.0.0.0:26443`. Protect it with host and network controls, and do not expose it to untrusted networks.
- [ ] Login and the other password endpoints are limited to 5 requests per minute per client address, and behind `kubectl port-forward` all clients share one address. For anything beyond a local machine, put a reverse proxy with TLS and stricter limits in front.
- [ ] Once you connect an external LLM provider, retrieved document text is sent to it. Tell your users which provider receives their data.

## Bundled data

No sample corpus or database is shipped. The repository contains four small fixture files from development testing. They are not copied into `$BERTH_DATA_DIR/ingest`, and they are only indexed if you place them there yourself:

| File | Content |
|---|---|
| `demo_data/d.txt`, `demo_data/p25/d.txt` | short Japanese sample product descriptions (about 1.2 KB and 0.6 KB) |
| `ingest/test_pii_ingest.md` | a Japanese masking test document. The name, phone number, e-mail address (`example.co.jp`) and ID number are fictitious and labelled as such. |
| `ingest/sub1/nested.txt` | a one-line Japanese test for recursive folder ingestion, with a placeholder name |

Before you give a deployment to other people, make sure that `$BERTH_DATA_DIR/ingest` contains only documents they are allowed to read. Workspace and collection access rules limit what each user can retrieve. They do not replace choosing what to ingest.

## Tell your users what they get

- [ ] Point them to [Known limitations](limitations.md), especially masking accuracy, backup scope and the single-node design.
- [ ] Tell them where their data lives and how it is backed up ([FAQ](faq.md#where-is-my-data)).
- [ ] Report security issues as described in [`SECURITY.md`](../../SECURITY.md).

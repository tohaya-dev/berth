# Berth Portable Distribution / Restore Specification

## 0. Purpose

This document defines the post-GA portability contract for Berth.

The goal is not necessarily public distribution. The goal is to keep a **vendor-neutral, relocatable, restore-capable Berth package** that can be copied to another machine without depending on the original Mac's absolute paths, local caches, or Chewie tree.

Berth remains **Kubernetes-native**. The current reference host uses Podman + k3d on macOS, but Podman itself is not the product runtime contract.

## 1. Runtime contract

```text
host OS
  ↓
container substrate
  ↓
Linux Kubernetes
  ↓
Berth application + workers + PostgreSQL/pgvector + Redis
```

### Current reference

- macOS Apple Silicon / arm64
- Podman
- k3d
- Linux Kubernetes

### Future portability targets

| Priority | Target | Status |
|---|---|---|
| P0 | macOS Apple Silicon / arm64 | reference / required |
| P1 | Linux arm64 | target |
| P2 | Linux amd64 | target after multi-arch app image build |
| P3 | Windows host running a Linux Kubernetes environment | future target |

Windows-native containers are not required. The desired portability model is to preserve the Linux/Kubernetes workload contract while widening the host OS.

## 2. Model policy

### Bundle

Bundle only models that are part of Berth's own RAG / guardrail function:

- embedding model(s)
- reranker model(s)
- local PII / NER / classifier model(s), if the active configuration requires them

These must live under the portable tree, for example:

```text
models/
  embedding/
  reranker/
  pii/
```

The runtime must resolve them relative to the expanded bundle, not by an original-machine absolute path.

### Do not bundle

Do **not** include any answer-generation LLM model files.

Examples of things that are intentionally external:

- LM Studio models
- Ollama model blobs
- GGUF answer models
- remote/OpenAI-compatible provider model files

The portable package carries provider configuration templates, not the provider's generative model payload.

## 3. Portable artifact layout

Target layout:

```text
HanSolo-<version>-portable/
├── START-HERE.md
├── VERSION
├── MANIFEST.json
├── SHA256SUMS
├── app/
├── images/
├── k8s/
├── models/
├── config/
├── ops/
├── docs/
└── state/              # optional restore payload; not required for clean install
```

### `images/`

For restore without relying on the original host's local image cache, store OCI archives for the required runtime images where practical:

- Berth application image
- PostgreSQL/pgvector image
- Redis image
- Kubernetes/k3s image used by the reference bundle when offline restore is a requirement

The build must record image names, tags, architecture, digest, and source in `MANIFEST.json`.

### `models/`

Contains only Berth-required local RAG / guardrail models.

### `state/`

Optional restore payload generated from a known-good live environment. It is logically separate from the runtime package even when stored in the same top-level archive.

Typical contents:

- restore-verified PostgreSQL dump
- checksum
- configuration export needed to recreate the environment
- encrypted secret/key backup only when restoration requires the original cryptographic material

Never commit live secret material to Git.

## 4. Startup sequence

The supported first-start sequence must be one coherent path, comparable in clarity to Chewie's launcher experience.

1. **Preflight**
   - required host commands available
   - architecture supported
   - enough free disk
   - portable manifest/checksums valid
2. **Start host container substrate** if needed.
3. **Create or reuse Linux Kubernetes cluster**.
4. **Load/import OCI images** from the bundle or approved registry source.
5. **Resolve models from the bundle-relative `models/` path**.
6. **Deploy PostgreSQL/pgvector and Redis**.
7. **Deploy Berth API and worker replicas**.
8. **Wait for `/api/ready` and Kubernetes readiness**.
9. **Run `ops/verify.sh`**.
10. **Print the local/LAN endpoint and next action**.

### Restart after host reboot

Restart must favor reuse over rebuild:

1. start container substrate
2. recover/reuse the existing cluster if healthy
3. repair transient networking/runtime state only as necessary
4. wait for database, Redis, API and workers
5. run verification

A healthy persisted database must never be silently replaced with a fresh one merely because the host rebooted.

## 5. Local-path neutrality

Portable staging must fail if it contains original-machine dependencies such as:

- a personal macOS home directory fixed as an absolute path
- a personal Linux home directory fixed as an absolute path
- an original Chewie model path
- original host socket paths
- original host-specific IP addresses used as mandatory defaults
- dated cluster names used as mandatory defaults

Acceptable values are placeholders, environment variables, or bundle-relative paths.

Preferred patterns:

```bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="${HAN_SOLO_MODELS_DIR:-$REPO_ROOT/models}"
```

or equivalent logic appropriate to the final portable tree.

## 6. Vendor-neutrality gate

The final portable artifact must be vendor-neutral even if the source repository keeps historical or test-only fixtures.

### Exclude from the portable artifact by default

- `_artifacts/`
- `instructions/`
- `tests/`
- old historical reports
- benchmark/evidence trees not required to run Berth
- vendor/product-specific sample fixtures
- `sample_data/` unless an individual file is explicitly approved as neutral

Use only neutral demo material in the portable package.

### Text scan

The portable staging tree must be scanned before archive creation for:

- original user names
- absolute personal paths
- known vendor/product fixture strings
- private host names / addresses
- secret/token/private-key patterns

The scan is a packaging gate, not a claim that every historical source-repository test file is vendor-neutral.

## 7. Configuration portability

Do not hard-code the original machine's:

- cluster name
- namespace-specific local environment choice
- LLM endpoint IP
- model path
- backup directory
- Podman socket path

These must be derived, environment-configurable, or initialized from neutral defaults.

The external LLM provider should be selected after deployment or supplied through configuration. Berth must remain usable with different OpenAI-compatible local/remote providers.

## 8. Multi-architecture image policy

The Berth application image should move toward a multi-architecture image index with at least:

- `linux/arm64`
- `linux/amd64`

Kubernetes and OCI tooling already support architecture-specific image selection. The application image and every bundled native dependency/model runtime must still be tested on each declared target architecture before that target is marked supported.

Do not advertise an architecture merely because Kubernetes itself supports it.

## 9. Windows strategy

The preferred future Windows path is:

```text
Windows host
  ↓
Linux VM / Linux container-Kubernetes environment
  ↓
Kubernetes
  ↓
Berth Linux workloads
```

This keeps one Berth Linux image line instead of creating and maintaining a second Windows-container implementation.

A Windows host target is considered supported only after fresh deploy, persistence, RAG, backup/restore, CLI and MCP acceptance run successfully on that path.

## 10. Portable acceptance

A portable build is PASS only if all of the following are true:

- bundle builds from current `main`
- required Berth RAG/guardrail models are present
- answer-generation LLM model payload is absent
- bundle does not reference the original Chewie tree
- bundle does not require original-machine absolute paths
- required OCI images can be loaded without relying on the original host's cache when offline restore mode is selected
- fresh cluster deploy succeeds from the bundle
- optional saved state can restore into a scratch cluster
- API and worker replicas reach Ready
- `/api/health` and `/api/ready` pass
- pgvector consistency passes
- Admin/Viewer RAG and access controls pass
- CLI passes against the restored endpoint
- HTTP MCP discovery/tools/call passes
- vendor-neutral/path/secret scan passes

## 11. Relation to GA

`v1.0.0-ga` remains the immutable GA anchor. Portable-distribution work is post-GA work on `main` and must not rewrite or silently retag that anchor.

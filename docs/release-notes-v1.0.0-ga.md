# Han Solo v1.0.0-ga Release Notes

Date: 2026-09-05 JST

## Summary

Han Solo `v1.0.0-ga` is the Kubernetes-native GA line for the production host. It uses Chewie `v1.2.0` as the shared Cynovela semantic baseline for RAG/RBAC behavior where applicable, without importing Chewie-only macOS packaging or replacing Han Solo platform assets.

## Runtime

- k3d on Podman.
- API deployment `cynovela`: 2 replicas.
- worker deployment `cynovela-worker`: 2 replicas.
- PostgreSQL with pgvector for relational and vector state.
- Redis for queue/cache.
- Local entrypoint: `http://127.0.0.1:18765`.
- GA image: `localhost/cynovela-hansolo:dd0144-ga`.

## Changes Since dd0143n

- Promoted Han Solo product version to `v1.0.0-ga`.
- Updated README and operations documentation to describe the actual Kubernetes runtime.
- Hardened scratch deploy safety by allowing `cleanup-stale.sh` to preserve the live cluster while creating a scratch cluster.
- Retained the Chewie v1.2.0 `/api/rag/query` convergence from the corresponding Chewie development commit (`<development commit>`).
- Retained container MCP dependency parity (`mcp==1.27.0`).

## Acceptance Matrix

Required before final GA declaration:

- health/ready.
- API 2/2 and worker 2/2.
- Admin RAG.
- Viewer RAG.
- unauthorized workspace denial.
- masking and restricted-data non-leakage.
- LM Studio.
- Ollama.
- provider switching.
- restart persistence.
- pgvector consistency.
- latest backup verify.
- fresh scratch deploy from canonical main.
- CLI and MCP.
- secret scan.
- git clean.

## Known Limitations

- Physical host cold boot evidence must be collected by rebooting the production host and rerunning `./ops/start.sh && ./ops/verify.sh`. The supported recovery path is documented, but a cold boot interrupts an active automation session.
- Podman image object deletion remains deferred unless each exact image target can be tied to internal boot-disk storage under the strict cleanup rule.

# Berth MCP Guide

この文書は旧 standalone/conda/SQLite 時代の MCP ガイドを置き換えるものです。

現在の正本は [`CLI-MCP.md`](CLI-MCP.md) です。

## 現行 Berth MCP の要点

- Berth は Kubernetes-native runtime
- 分散構成では **HTTP MCP** を優先
- RPC endpoint: `POST /api/mcp/rpc`
- fingerprint endpoint: `GET /api/mcp/fingerprint`
- server: `cynovela-mcp`
- auth: JWT または用途限定 API key (`cyn_...`)
- Viewer / scoped API key には外向き利用可の read/search/RAG tools のみを公開
- workspace / collection scope を MCP 層でも強制
- 管理系 write は Admin JWT に加えて `CYNOVELA_MCP_ALLOW_ADMIN_WRITE=1` と `confirm=true` の二重 gate
- tool contract の変化は fingerprint で検出可能
- RAG tool の返りは answer だけでなく sources / provenance を保持

Kyber 等の integration では **HTTP MCP + scoped Viewer API key + least privilege** を基本にしてください。

詳細・curl例・CLIでの key 発行/失効・troubleshooting は [`CLI-MCP.md`](CLI-MCP.md) を参照してください。

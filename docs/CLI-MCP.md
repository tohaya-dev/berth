# Berth CLI / MCP Operator Guide

この文書は Berth を日常運用し、将来 Kyber 等から MCP 連携するための実用ガイドです。

Berth の参照 endpoint は `http://127.0.0.1:18765` です。CLI のコードには旧 standalone 互換の既定値が残るため、Berth では `CYNOVELA_URL` または `--url` を明示する運用を推奨します。

## 1. 最初の3行

```bash
export CYNOVELA_URL=http://127.0.0.1:18765
python3 cynovela_cli.py status
python3 cynovela_cli.py health
```

ログイン:

```bash
python3 cynovela_cli.py login --username cynovela
```

パスワードは対話入力を使うことを推奨します。ログイン成功後の token は `~/.cynovela_cli.env` に mode 0600 で保存され、token 値自体は通常表示しません。

ログアウト:

```bash
python3 cynovela_cli.py logout
```

## 2. URL / token の優先順位

CLI は次の順に接続情報を解決します。

1. `--url` / `--token`
2. `CYNOVELA_URL` / `CYNOVELA_TOKEN`
3. `~/.cynovela_cli.env`

Berth では、まず以下を shell profile または作業 shell に置くと分かりやすくなります。

```bash
export CYNOVELA_URL=http://127.0.0.1:18765
```

## 3. 状態確認

```bash
python3 cynovela_cli.py status
python3 cynovela_cli.py health
python3 cynovela_cli.py --json health
```

Kubernetes 側も同時に見る場合:

```bash
./ops/status.sh
./ops/verify.sh
```

## 4. Workspace / Collection を調べる

```bash
python3 cynovela_cli.py workspaces list
python3 cynovela_cli.py collections list
python3 cynovela_cli.py collections list --workspace <workspace_id>
```

機械処理するときは `--json` を付けます。

```bash
python3 cynovela_cli.py --json workspaces list
```

## 5. RAG 検索と回答

検索:

```bash
python3 cynovela_cli.py search \
  --workspace <workspace_id> \
  --query "調べたい内容"
```

回答 + 出典:

```bash
python3 cynovela_cli.py chat \
  --workspace <workspace_id> \
  --query "質問"
```

RAG preset:

```bash
python3 cynovela_cli.py chat \
  --workspace <workspace_id> \
  --query "質問" \
  --mode standard
```

`lite / standard / hq` を選べます。

Viewer 資格では access control と masking を通った内容が使用されます。CLI はサーバの認可を迂回しません。

## 6. Ingest / Publish / Job

取り込み元:

```bash
python3 cynovela_cli.py ingest-roots list
```

Source:

```bash
python3 cynovela_cli.py sources list --workspace <workspace_id>
python3 cynovela_cli.py sources show --id <source_id>
```

Publish 開始:

```bash
python3 cynovela_cli.py publish start --collection <collection_id>
```

進捗:

```bash
python3 cynovela_cli.py publish status --job <job_id>
python3 cynovela_cli.py jobs --kind all
```

再公開や recovery 等の危険操作では `--yes` が必要になる経路があります。まず preview/状態確認を行い、対象 ID を確認してから実行してください。

## 7. Provider / settings

設定確認:

```bash
python3 cynovela_cli.py settings show
python3 cynovela_cli.py settings models
python3 cynovela_cli.py settings test-connection
```

設定変更:

```bash
python3 cynovela_cli.py settings set \
  --key <key> \
  --value <value> \
  --yes
```

Berth の回答生成 LLM は外部 provider です。Portable bundle に LLM model file を入れるのではなく、移行先で LM Studio / Ollama / OpenAI-compatible endpoint 等へ接続します。

## 8. API key — Kyber 等の外部連携向け

MCP/automation 用には、管理者 JWT を恒常的に渡すより、用途限定 API key (`cyn_...`) を使う方を優先します。

一覧:

```bash
python3 cynovela_cli.py key list
```

Viewer key を発行:

```bash
python3 cynovela_cli.py key issue \
  --name kyber \
  --role viewer \
  --scope-workspace <workspace_id>
```

さらに collection を限定する場合:

```bash
python3 cynovela_cli.py key issue \
  --name kyber \
  --role viewer \
  --scope-workspace <workspace_id> \
  --scope-collection <collection_id>
```

発行時の平文 key は1回だけ表示されます。Git、Notion、通常ログ、スクリーンショットへ保存しないでください。

失効:

```bash
python3 cynovela_cli.py key revoke --id <key_id> --yes
```

## 9. Berth MCP の入口

Berth の分散構成では stdio MCP より **HTTP MCP** を基本にします。

- RPC: `POST /api/mcp/rpc`
- fingerprint: `GET /api/mcp/fingerprint`
- server name: `cynovela-mcp`
- auth: JWT または用途限定 API key

HTTP MCP は Kubernetes Service/LB の後ろの API replica で同じ入口を提供し、接続固有状態を持たない設計です。

## 10. MCP discovery

例では token を shell 変数に入れます。実運用では秘密管理方法に合わせてください。

```bash
export CYNOVELA_URL=http://127.0.0.1:18765
export HAN_SOLO_MCP_TOKEN='<JWT-or-cyn_API_key>'
```

Server discovery:

```bash
curl -sS "$CYNOVELA_URL/api/mcp/rpc" \
  -H "Authorization: Bearer $HAN_SOLO_MCP_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{}}'
```

旧 handshake 型クライアント向けに `initialize` 互換も残していますが、新規連携では状態を持たない discovery を優先します。

## 11. tools/list

```bash
curl -sS "$CYNOVELA_URL/api/mcp/rpc" \
  -H "Authorization: Bearer $HAN_SOLO_MCP_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

重要: tool 一覧は資格によって変わります。

- Viewer / scoped API key: 外向き利用可の read/search/RAG 系 tool のみ
- Admin JWT: 管理系 tool も見える
- scoped API key: workspace / collection scope 外への到達を MCP 層でも拒否

「固定の tool 数」をマニュアルへ焼き付けず、`tools/list` と fingerprint を正本にします。

## 12. 最初に呼ぶ MCP tools

外部 integration の疎通確認では次の順が安全です。

1. `whoami`
2. `list_workspaces`
3. `list_collections`
4. `search_collection` または `search_across_collections`

`whoami` は、現在の資格・role・API key scope を確認する用途にも使えます。

## 13. RAG tool の基本

`search_collection` の例:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "search_collection",
    "arguments": {
      "query": "質問",
      "workspace_id": "<workspace_id>",
      "collection_id": "<collection_id>",
      "preset": "standard"
    }
  }
}
```

返りには回答本文だけでなく、番号付き sources と provenance を保持する設計です。Kyber 側では回答本文だけを抽出せず、**sources / provenance も run evidence として保存**することを推奨します。

## 14. MCP fingerprint

Tool schema / description の変化検知には fingerprint を使います。

```bash
curl -sS "$CYNOVELA_URL/api/mcp/fingerprint" \
  -H "Authorization: Bearer $HAN_SOLO_MCP_TOKEN"
```

Kyber のような長期 integration では、接続時に fingerprint を記録しておくと「Berth 側の tool contract が変わったか」を判別しやすくなります。

## 15. Admin write は既定で閉じる

MCP の危険な管理操作は、Admin JWT を持っているだけでは実行できない設計があります。

次の **両方** が必要です。

1. Berth server environment で `CYNOVELA_MCP_ALLOW_ADMIN_WRITE=1`
2. tool call で `confirm=true`

したがって、Kyber の通常接続ではこの gate を開けないことを推奨します。

Kyber はまず read/search/RAG plane として接続し、管理操作が本当に必要な場合だけ別資格・別運用に分離します。

## 16. Kyber 連携の推奨形

```text
Kyber
  ↓ HTTP MCP + scoped API key
Berth MCP
  ↓ same auth / RBAC / masking
Berth API
  ↓
PostgreSQL / pgvector + governed RAG evidence
```

推奨:

- HTTP MCP
- Viewer role の用途限定 API key
- workspace scope を指定
- 必要なら collection scope も指定
- `whoami` で scope 確認
- `tools/list` を毎接続または契約更新時に確認
- fingerprint を run metadata に記録
- RAG answer と sources/provenance をセットで Kyber evidence に保存
- admin write gate は通常 CLOSED

## 17. Troubleshooting

### CLI が 8765 を見に行く

Berth では URL を明示してください。

```bash
export CYNOVELA_URL=http://127.0.0.1:18765
```

### MCP 401/403

確認順:

1. token/key が有効か
2. `whoami`
3. workspace scope
4. collection scope
5. role
6. admin tool を API key で呼ぼうとしていないか

### RAG が空

```bash
python3 cynovela_cli.py collections list --workspace <workspace_id>
python3 cynovela_cli.py index-status --workspace <workspace_id>
python3 cynovela_cli.py jobs --kind publish
```

Collection が ready か、chunk/index が存在するか、publish job が失敗していないかを確認します。

### Kubernetes 自体が怪しい

```bash
./ops/status.sh
./ops/verify.sh
```

## 18. Full command help

CLI は今後も増えるため、完全な command list はコード内 help を正本にします。

```bash
python3 cynovela_cli.py --help
python3 cynovela_cli.py <command> --help
```

この文書は「日常運用と Kyber integration で使う道」を優先して説明します。

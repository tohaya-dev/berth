#!/usr/bin/env python3
"""Cynovela MCP Server v2.0 — 全機能実装"""

import sys, json, argparse, os as _os_pre, requests

parser = argparse.ArgumentParser()
parser.add_argument("--cynovela-url", default="http://127.0.0.1:8765")
args = parser.parse_args()
# CYNOVELA_BASE 環境変数を優先、未設定なら CLI 引数を使用
BASE = _os_pre.environ.get("CYNOVELA_BASE", "").strip().rstrip("/") or args.cynovela_url.rstrip("/")

# ─────────────────────────────────────────
# ツール定義
# ─────────────────────────────────────────
TOOLS = [
    # ── RAG検索系 ──────────────────────────
    {
        "name": "search_collection",
        "description": (
            "CynovelaのRAGコレクションに対してクエリを実行します。"
            "workspace_idとcollection_idはlist_workspacesで取得してください。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "検索クエリ"},
                "workspace_id": {"type": "string", "description": "ワークスペースID"},
                "collection_id": {"type": "string", "description": "コレクションID"},
                "preset": {"type": "string", "description": "lite / standard / hq（デフォルト: standard）"},
            },
            "required": ["query", "workspace_id", "collection_id"],
        },
    },
    {
        "name": "search_across_collections",
        "description": (
            "複数のRAGコレクションを横断してクエリを実行します。"
            "collection_idsに複数のIDを渡すと全て検索して統合した結果を返します。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "検索クエリ"},
                "workspace_id": {"type": "string", "description": "ワークスペースID"},
                "collection_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "コレクションIDのリスト（複数指定可）",
                },
                "preset": {"type": "string", "description": "lite / standard / hq（デフォルト: standard）"},
            },
            "required": ["query", "workspace_id", "collection_ids"],
        },
    },
    {
        "name": "rag_with_role",
        "description": (
            "ユーザーのロールに応じて回答スタイルを変えてRAG検索を実行します。"
            "admin=技術詳細・reader=平易な説明。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "検索クエリ"},
                "workspace_id": {"type": "string", "description": "ワークスペースID"},
                "collection_id": {"type": "string", "description": "コレクションID"},
                "style_role": {"type": "string", "description": "admin / reader"},
                "preset": {"type": "string", "description": "lite / standard / hq（デフォルト: standard）"},
            },
            "required": ["query", "workspace_id", "collection_id", "style_role"],
        },
    },
    {
        "name": "rag_general",
        "description": (
            "RAGを使わずLLMの学習データで直接回答します（一般知識モード）。"
            "ワークスペース内のドキュメントを参照しないため、sourcesは空になります。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "質問"},
                "workspace_id": {"type": "string", "description": "ワークスペースID（ログ記録用）"},
            },
            "required": ["query", "workspace_id"],
        },
    },
    # ── ワークスペース・コレクション情報系 ──
    {
        "name": "list_workspaces",
        "description": (
            "利用可能なワークスペースとコレクション一覧を返します。"
            "search_collection / search_across_collections / rag_with_role を呼ぶ前に"
            "このツールでIDを確認してください。詳細は get_workspace_info / "
            "get_collection_info で確認できます。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_workspace_info",
        "description": "指定ワークスペースの詳細情報（名前・ガードレール設定・作成日時等）を返します。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "ワークスペースID"},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "get_collection_info",
        "description": "指定コレクションの詳細情報（ドキュメント数・ステータス・アクセスレベル等）を返します。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "ワークスペースID"},
                "collection_id": {"type": "string", "description": "コレクションID"},
            },
            "required": ["workspace_id", "collection_id"],
        },
    },
    # ── 監査・ログ系 ────────────────────────
    {
        "name": "get_audit_logs",
        "description": "ワークスペースの直近の監査ログを返します（RAG Chat履歴・PII検出・エラー等）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "ワークスペースID"},
                "limit": {"type": "integer", "description": "取得件数（デフォルト10・最大50）"},
            },
            "required": ["workspace_id"],
        },
    },
    # ── データソース系（STEP 0調査結果より追加: HTTP 200 ✅）──
    {
        "name": "list_sources",
        "description": (
            "登録済みデータソースの一覧を返します（ファイルパス・ステータス・ファイル数等）。"
            "返り: source の id・パス・状態・ファイル数の配列。"
            "scan の対象指定や get_status 相当の確認（走査の進み具合）は id で行います。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "ワークスペースID（フィルタ）"},
            },
            "required": ["workspace_id"],
        },
    },
    # ── Publish 系（STEP 0調査結果より追加: HTTP 200 ✅）──
    {
        "name": "publish_collection",
        "description": (
            "指定コレクションをPublish（公開）します。"
            "Publish後はRAG Chatで検索可能になります。既にready状態でも再Publishが可能です。"
            "公開は非同期で進みます。進み具合は HTTP 版 MCP の get_status か "
            "GET /api/jobs で確認してください。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string", "description": "コレクションID"},
            },
            "required": ["collection_id"],
        },
    },
    # ── ワークスペース作成（STEP 0調査結果より追加: HTTP 200 ✅）──
    {
        "name": "create_workspace",
        "description": "新しいワークスペースを作成します。作成後にデータソース登録・コレクション作成が必要です。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "ワークスペース名"},
                "description": {"type": "string", "description": "説明（任意）"},
            },
            "required": ["name"],
        },
    },
]

# ─────────────────────────────────────────
# Cynovela API ヘルパー
# ─────────────────────────────────────────
import os as _os


def _token() -> str:
    """API 呼び出しに使う Bearer トークンを CYNOVELA_TOKEN から取る。

    DD-CYN-0116 X-6 U-1 (姉妹系統の C-B5 の移植): 固定トークン demo-token-user-admin の
    フォールバックを撤去した。受け口側 (core/auth.py) で demo-token-* の受理を封鎖した
    ため、返しても必ず 401 になり、利用者には「トークンが無い」ではなく「認証に失敗した」
    としか見えず誤誘導になる。未設定は設定漏れなので、その場で理由の分かる例外にする。
    トークンは管理画面のログインで発行される値を CYNOVELA_TOKEN に入れて渡すこと。
    """
    env_tok = _os.environ.get("CYNOVELA_TOKEN", "").strip()
    if env_tok:
        return env_tok
    raise RuntimeError(
        "CYNOVELA_TOKEN が設定されていません。"
        "Cynovela にログインして発行されたトークンを CYNOVELA_TOKEN に設定してください"
    )


def _h() -> dict:
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


def _parse_response(r, expect_type: str = "dict"):
    """APIレスポンスを安全にパースする共通ヘルパー。"""
    try:
        data = r.json()
    except Exception as e:
        raise ValueError(f"JSONパース失敗: {e} / body={r.text[:200]}")
    if expect_type == "dict" and not isinstance(data, dict):
        raise ValueError(f"dict期待だが{type(data).__name__}が返った: {r.text[:200]}")
    if expect_type == "list" and not isinstance(data, list):
        raise ValueError(f"list期待だが{type(data).__name__}が返った: {r.text[:200]}")
    return data


def _parse_items(r, *keys):
    """mcp-empty-list-fix-20260727: 一覧を返す API の応答から要素の配列を取り出す。

    一覧 API の応答は素の配列のことも、{"items": [...]} のような包みのこともある。
    従来は `_parse_response(r, "dict")` で dict 決め打ちに受け、配列が返ると
    ValueError を握り潰して `payload = {}` に落とし、さらに包みのキー名も
    取り違えていた (`items` を `logs` で読む)。結果として list_sources と
    get_audit_logs が **成功扱いのまま常に空** を返していた。

    見つからないときは空を返さず例外にする。空を返すと「資料が無い」「監査記録が無い」と
    区別がつかず、監査の空振りをそのまま成功として報告することになるため。
    """
    try:
        data = r.json()
    except Exception as e:
        raise ValueError(f"JSONパース失敗: {e} / body={r.text[:200]}")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
        _lists = [v for v in data.values() if isinstance(v, list)]
        if len(_lists) == 1:
            return _lists[0]
        raise ValueError(
            f"一覧が見つかりません。期待キー={list(keys)} / 実際のキー={sorted(data.keys())}"
        )
    raise ValueError(f"一覧を取り出せません: {type(data).__name__} / body={r.text[:200]}")


# ─────────────────────────────────────────
# ツール実装
# ─────────────────────────────────────────
def _fmt_source_line(i: int, s) -> str:
    # sokessan-fix-a9-20260711: /api/chat の sources 要素は dict のことも str(ファイル名等)の
    # こともある。str 要素で s.get() が AttributeError になり search 系ツールが落ちるのを防ぐ。
    if isinstance(s, dict):
        return (
            f"[{i+1}] {s.get('file_name','?')} (score={float(s.get('score') or 0):.3f})\n"
            f"    {(s.get('text') or '')[:200]}"
        )
    return f"[{i+1}] {str(s)[:200]}"


def _fmt_sources_block(d: dict) -> tuple:
    """C-13: /api/chat 応答の出典を番号付きで全件列挙する（切り捨てない）。

    citations があればそれを優先し、[index] を尊重する（本文の [N] と同じ番号空間。
    振り直さない）。citations が無いときのみ従来どおり sources を列挙するが、
    その場合は番号が本文の引用番号と対応しない旨を1行出す。
    返り: (件数, 整形済みテキスト)。"""
    citations = d.get("citations", []) or []
    sources = d.get("sources", []) or []
    if citations:
        lines = []
        for c in citations:
            if not isinstance(c, dict):
                continue
            idx = c.get("index")
            fname = c.get("source_filename") or "不明"
            page = c.get("page_hint")
            line = f"[{idx}] {fname}"
            if page not in (None, ""):
                line += f" (p.{page})"
            preview = (c.get("chunk_preview") or "")[:200]
            if preview:
                line += f"\n    {preview}"
            lines.append(line)
        return len(lines), "\n".join(lines)
    lines = [_fmt_source_line(i, s) for i, s in enumerate(sources)]
    n = len(lines)
    if lines:
        lines.append("※番号は本文の引用番号と対応しない")
    return n, "\n".join(lines)


def _call_tool(name: str, args: dict) -> str:
    # Bridge は <server>.<tool> 形式でツール名を渡すので prefix を剥がす
    if "." in name:
        name = name.split(".", 1)[1]
    try:
        h = _h()
        # ── search_collection ──────────────────
        if name == "search_collection":
            r = requests.post(
                f"{BASE}/api/chat",
                headers=h,
                json={
                    "query": args["query"],
                    "workspace_id": args["workspace_id"],
                    "collection_ids": [args["collection_id"]],
                    "preset": args.get("preset", "standard"),
                },
                timeout=120,
            )
            r.raise_for_status()
            d = _parse_response(r, "dict")
            answer = d.get("answer", "")
            # C-13: citations 優先・[index] 尊重・全件列挙（切り捨てない）
            n_src, src_txt = _fmt_sources_block(d)
            return f"## 回答\n{answer}\n\n## 参照ソース（{n_src}件）\n{src_txt}"

        # ── search_across_collections ───────────
        elif name == "search_across_collections":
            col_ids = args["collection_ids"]
            r = requests.post(
                f"{BASE}/api/chat",
                headers=h,
                json={
                    "query": args["query"],
                    "workspace_id": args["workspace_id"],
                    "collection_ids": col_ids,
                    "preset": args.get("preset", "standard"),
                },
                timeout=120,
            )
            r.raise_for_status()
            d = _parse_response(r, "dict")
            answer = d.get("answer", "")
            # C-13: citations 優先・[index] 尊重・全件列挙（切り捨てない）
            n_src, src_txt = _fmt_sources_block(d)
            return (
                f"## 回答（{len(col_ids)}コレクション横断）\n{answer}\n\n"
                f"## 参照ソース（{n_src}件）\n{src_txt}"
            )

        # ── rag_with_role ───────────────────────
        elif name == "rag_with_role":
            r = requests.post(
                f"{BASE}/api/chat",
                headers=h,
                json={
                    "query": args["query"],
                    "workspace_id": args["workspace_id"],
                    "collection_ids": [args["collection_id"]],
                    "preset": args.get("preset", "standard"),
                    "style_role": args["style_role"],
                },
                timeout=120,
            )
            r.raise_for_status()
            d = _parse_response(r, "dict")
            answer = d.get("answer", "")
            sources = d.get("sources", []) or []
            return f"## 回答（{args['style_role']}ロール向け）\n{answer}\n\n" f"## ソース数: {len(sources)}件"

        # ── rag_general ─────────────────────────
        elif name == "rag_general":
            r = requests.post(
                f"{BASE}/api/chat",
                headers=h,
                json={
                    "query": args["query"],
                    "workspace_id": args["workspace_id"],
                    "collection_ids": [],
                    "rag_mode": "general",
                    "preset": "standard",
                },
                timeout=120,
            )
            r.raise_for_status()
            d = _parse_response(r, "dict")
            answer = d.get("answer", "")
            sources = d.get("sources", []) or []
            note = "（RAGなし・LLM直接回答）" if not sources else ""
            return f"## 回答{note}\n{answer}"

        # ── list_workspaces ─────────────────────
        elif name == "list_workspaces":
            r = requests.get(f"{BASE}/api/workspaces", headers=h, timeout=10)
            r.raise_for_status()
            result = []
            ws_data = _parse_response(r, "list")
            for ws in ws_data:
                ws_id = ws.get("id", "")
                rc = requests.get(f"{BASE}/api/collections?workspace_id={ws_id}", headers=h, timeout=10)
                try:
                    cols_payload = _parse_response(rc, "list") if rc.ok else []
                except Exception:
                    cols_payload = []
                cols = cols_payload if isinstance(cols_payload, list) else []
                result.append(
                    {
                        "id": ws_id,
                        "name": ws.get("name", ""),
                        "collections": [
                            {"id": c.get("id"), "name": c.get("name"), "status": c.get("status")}
                            for c in cols
                            if not c.get("archived_at")
                        ],
                    }
                )
            return json.dumps({"workspaces": result}, ensure_ascii=False, indent=2)

        # ── get_workspace_info ──────────────────
        elif name == "get_workspace_info":
            ws_id = args["workspace_id"]
            r = requests.get(f"{BASE}/api/workspaces/{ws_id}", headers=h, timeout=10)
            r.raise_for_status()
            d = _parse_response(r, "dict")
            return json.dumps(
                {
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "description": d.get("description"),
                    "created_at": d.get("created_at"),
                    "updated_at": d.get("updated_at"),
                    "guardrail_policy_id": d.get("guardrail_policy_id"),
                },
                ensure_ascii=False,
                indent=2,
            )

        # ── get_collection_info ─────────────────
        elif name == "get_collection_info":
            ws_id = args["workspace_id"]
            col_id = args["collection_id"]
            r = requests.get(f"{BASE}/api/collections?workspace_id={ws_id}", headers=h, timeout=10)
            r.raise_for_status()
            try:
                cols_p = _parse_response(r, "list")
            except Exception:
                cols_p = []
            cols = cols_p if isinstance(cols_p, list) else []
            col = next((c for c in cols if c.get("id") == col_id), None)
            if not col:
                return f"コレクション {col_id} が見つかりません"
            return json.dumps(
                {
                    "id": col.get("id"),
                    "name": col.get("name"),
                    "status": col.get("status"),
                    "chunk_count": col.get("chunk_count", 0),
                    "access_level": col.get("access_level"),
                    "created_at": col.get("created_at"),
                    "allowed_roles": col.get("allowed_roles_json"),
                },
                ensure_ascii=False,
                indent=2,
            )

        # ── get_audit_logs ──────────────────────
        elif name == "get_audit_logs":
            ws_id = args["workspace_id"]
            limit = min(int(args.get("limit", 10)), 50)
            r = requests.get(f"{BASE}/api/audit-logs?workspace_id={ws_id}&limit={limit}", headers=h, timeout=10)
            r.raise_for_status()
            # mcp-empty-list-fix-20260727: API 側のキーは "items"。従来は "logs" で読んでいた。
            logs = _parse_items(r, "items", "logs", "audit_logs")
            result = [
                {
                    "timestamp": l.get("timestamp"),
                    "action": l.get("action"),
                    "target": l.get("target"),
                    "detail": str(l.get("detail", ""))[:120],
                }
                for l in logs[:limit]
            ]
            return json.dumps({"logs": result, "count": len(result)}, ensure_ascii=False, indent=2)

        # ── list_sources ────────────────────────
        elif name == "list_sources":
            ws_id = args["workspace_id"]
            r = requests.get(f"{BASE}/api/sources?workspace_id={ws_id}", headers=h, timeout=10)
            r.raise_for_status()
            # mcp-empty-list-fix-20260727: /api/sources は素の JSON 配列を返す。
            # 従来は dict 決め打ちで受けて例外を握り潰し、常に空を返していた。
            sources = _parse_items(r, "sources", "items")
            result = [
                {
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "path": s.get("path"),
                    "status": s.get("status"),
                    "file_count": s.get("file_count", 0),
                }
                for s in sources
            ]
            return json.dumps({"sources": result, "count": len(result)}, ensure_ascii=False, indent=2)

        # ── publish_collection ──────────────────
        elif name == "publish_collection":
            col_id = args["collection_id"]
            # DD-CYN-0095 §6-6-2: Publish は埋め込みの計算を伴い 120 秒を超えることがある
            #   (ホスト直起動で実測)。答えが返らないまま期限切れになっていたため長くする。
            r = requests.post(f"{BASE}/api/collections/{col_id}/publish", headers=h, json={}, timeout=600)
            r.raise_for_status()
            d = _parse_response(r, "dict") if r.text else {}
            return json.dumps(
                {
                    "ok": True,
                    "collection_id": col_id,
                    "status": d.get("status"),
                    "message": d.get("message", "Publish成功"),
                },
                ensure_ascii=False,
                indent=2,
            )

        # ── create_workspace ────────────────────
        elif name == "create_workspace":
            r = requests.post(
                f"{BASE}/api/workspaces",
                headers=h,
                json={
                    "name": args["name"],
                    "description": args.get("description", ""),
                },
                timeout=10,
            )
            r.raise_for_status()
            d = _parse_response(r, "dict")
            return json.dumps(
                {
                    "ok": True,
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "description": d.get("description"),
                },
                ensure_ascii=False,
                indent=2,
            )

        else:
            return f"未知のツール: {name}"

    except requests.HTTPError as e:
        return f"APIエラー ({e.response.status_code}): {e.response.text[:200]}"
    except Exception as e:
        return f"エラー: {e}"


# ─────────────────────────────────────────
# JSON-RPC ループ
# ─────────────────────────────────────────
def handle(req: dict):
    method = req.get("method", "")
    rid = req.get("id")

    # JSON-RPC notification (id なし) には応答しない
    is_notification = ("id" not in req) or (rid is None)

    # DD-CYN-0143 M-1: server/discover (2026-07-28) = 状態を持たない発見。
    # initialize は旧世代クライアント互換で残し、同じ内容で応えるだけで握手の完了は待たない。
    # 交渉規則: 頼まれた版が日付形式で自分の版以下ならその版で応える。未来の版は名乗らない。
    if method in ("server/discover", "initialize"):
        _PROTO = "2026-07-28"
        _req_ver = str(((req.get("params") or {}).get("protocolVersion")) or "")
        import re as _re

        if method == "initialize" and _re.match(r"^\d{4}-\d{2}-\d{2}$", _req_ver) and _req_ver <= _PROTO:
            _ver = _req_ver
        else:
            _ver = _PROTO
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": _ver,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "cynovela-mcp", "version": "2.1"},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name", "")
        targs = params.get("arguments", {})
        content = _call_tool(name, targs)
        is_err = content.startswith(("エラー:", "APIエラー"))
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "content": [{"type": "text", "text": content}],
                "isError": is_err,
            },
        }

    # 不明 method: notification なら無応答、それ以外はエラー
    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}


if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle(req)
        except Exception as e:
            # パース失敗時のみ id=null で返す（仕様上許容）
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)

#!/usr/bin/env python3
"""Cynovela CLI クライアント (PHASE B-2 / DD-CYN-0132 Phase5 拡張).

Cynovela サーバーへ HTTP API でアクセスする薄いラッパー。
標準ライブラリのみで実装し、追加依存なしで動作する。

使用方法:
  export CYNOVELA_URL=http://100.x.x.x:8765
  export CYNOVELA_TOKEN=<ログインで発行されたトークン>   # Bearer Authorization に使う

  python cynovela_cli.py status
  python cynovela_cli.py health [--json]
  python cynovela_cli.py workspaces list [--json]
  python cynovela_cli.py workspaces register --name NAME [--description DESC] [--json]
  python cynovela_cli.py collections list [--workspace WS_ID] [--json]
  python cynovela_cli.py collections create --name NAME --workspace WS_ID [--json]
  python cynovela_cli.py search --workspace WS_ID --query "..." [--n-results 5] [--json]
  python cynovela_cli.py scan --source SRC_ID
  python cynovela_cli.py ingest-status (--source SRC_ID | --job JOB_ID) [--json]
  python cynovela_cli.py index-status [--workspace WS_ID] [--json]
  python cynovela_cli.py publish --collection COL_ID
  python cynovela_cli.py chat --workspace WS_ID --query "質問文" [--json]
  python cynovela_cli.py key list|issue|revoke ...   # 鍵 API 未実装サーバでは not_implemented を返す
  python cynovela_cli.py login --username NAME       # トークンを ~/.cynovela_cli.env へ保存 (logout で消す)
  python cynovela_cli.py jobs [--kind all|publish|scan] [--status ST] [--limit N]

グローバル引数 (どのコマンドの前後にも置ける):
  --json          機械可読の JSON を 1 本だけ出力する
  --url  URL      CYNOVELA_URL より優先 (クラスタ内外どちらからでも同じコマンドで)
  --token TOKEN   CYNOVELA_TOKEN より優先

設定ファイル:
  ~/.cynovela_cli.env  →  KEY=VALUE 形式で CYNOVELA_URL / CYNOVELA_TOKEN を読み込む
                         (環境変数より優先度は低い / --url --token が最優先)

終了コード:
  0 = 成功 / 1 = API 失敗 (非2xx) / 2 = 引数不正
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


# ─── 設定ロード ─────────────────────────────────────────────────
def _load_env_file(path: Path) -> dict:
    out: dict = {}
    if not path.is_file():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


_ENV_PATH = Path.home() / ".cynovela_cli.env"
_env_file = _load_env_file(_ENV_PATH)


def _update_env_file(updates: dict, removes: tuple = (), path: Path | None = None) -> Path:
    """~/.cynovela_cli.env の KEY=VALUE 行を置換/追記/削除する (N-2)。

    対象キー以外の行 (コメント含む) は保全し、書き込み後は 0600 にする。
    """
    path = path or _ENV_PATH
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    elif not updates:
        return path  # 消すべきファイルが無ければ何もしない
    done: set = set()
    out_lines: list[str] = []
    for line in lines:
        s = line.strip()
        key = s.split("=", 1)[0].strip() if ("=" in s and not s.startswith("#")) else None
        if key in removes:
            continue
        if key in updates:
            out_lines.append(f"{key}={updates[key]}")
            done.add(key)
            continue
        out_lines.append(line)
    for k, v in updates.items():
        if k not in done:
            out_lines.append(f"{k}={v}")
    path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _cfg(key: str, default: str = "") -> str:
    return os.environ.get(key) or _env_file.get(key) or default


BASE_URL = _cfg("CYNOVELA_URL", "http://127.0.0.1:8765").rstrip("/")
TOKEN = _cfg("CYNOVELA_TOKEN", "")


# ─── HTTP ヘルパ (urllib) ────────────────────────────────────────
def _request(method: str, path: str, body: Any = None, timeout: float = 60.0) -> tuple[int, Any]:
    url = f"{BASE_URL}{path}"
    headers = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    data: bytes | None = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw or b"null")
            except Exception:
                return r.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_text: Any = ""
        try:
            raw = e.read()
            try:
                body_text = json.loads(raw or b"null")
            except Exception:
                body_text = raw.decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body_text
    except Exception as e:
        return 0, f"connection error: {e}"


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _want_json(args) -> bool:
    return bool(getattr(args, "json", False))


def _fail(args, code: int, data: Any) -> int:
    """非2xx を一貫して報告し、終了コード 1 を返す。"""
    if _want_json(args):
        _print_json({"error": "api_error", "status": code, "detail": data})
    else:
        print(f"FAIL ({code}): {data}", file=sys.stderr)
    return 1


# ─── コマンド実装 ──────────────────────────────────────────────
def cmd_status(args) -> int:
    code, data = _request("GET", "/api/health", timeout=5)
    if _want_json(args):
        _print_json({"status_code": code, "ok": code == 200, "body": data})
        return 0 if code == 200 else 1
    if code == 200:
        print(f"✅ 接続OK: {BASE_URL}")
        _print_json(data)
        return 0
    print(f"❌ 接続失敗 ({code}): {data}", file=sys.stderr)
    return 1


def cmd_health(args) -> int:
    code, data = _request("GET", "/api/health", timeout=5)
    if _want_json(args):
        _print_json(data if isinstance(data, (dict, list)) else {"status_code": code, "body": data})
        return 0 if code == 200 else 1
    if code == 200:
        st = data.get("status") if isinstance(data, dict) else None
        ver = data.get("version") if isinstance(data, dict) else None
        print(f"ok  ({BASE_URL})  status={st}  version={ver}")
        return 0
    print(f"ng  ({BASE_URL})  code={code}  {data}", file=sys.stderr)
    return 1


def cmd_search(args) -> int:
    body = {"query": args.query, "workspace_id": args.workspace, "n_results": int(args.n_results)}
    code, data = _request("POST", "/api/rag/query", body=body, timeout=300)
    if code not in (200, 201):
        return _fail(args, code, data)
    if _want_json(args):
        _print_json(data)
        return 0
    if isinstance(data, dict):
        results = data.get("results") or data.get("chunks") or data.get("matches") or []
        answer = data.get("answer")
        if answer:
            print(answer)
        if isinstance(results, list):
            print(f"\n--- results ({len(results)}) ---")
            for r in results[:int(args.n_results)]:
                if isinstance(r, dict):
                    label = r.get("source_doc") or r.get("filename") or r.get("id") or ""
                    prev = (r.get("text") or r.get("content") or r.get("preview") or "")[:100]
                    print(f"  - {label}\t{prev}")
                else:
                    print(f"  - {r}")
        else:
            _print_json(data)
        return 0
    _print_json(data)
    return 0


def cmd_workspaces_list(args) -> int:
    code, data = _request("GET", "/api/workspaces")
    if code != 200:
        return _fail(args, code, data)
    if _want_json(args):
        _print_json(data)
        return 0
    if isinstance(data, list):
        for ws in data:
            wid = ws.get("id", "?")
            name = ws.get("name", "?")
            print(f"{wid}\t{name}")
        return 0
    _print_json(data)
    return 0


def cmd_workspaces_register(args) -> int:
    body = {"name": args.name, "description": args.description or ""}
    code, data = _request("POST", "/api/workspaces", body=body)
    if code not in (200, 201):
        return _fail(args, code, data)
    if _want_json(args):
        _print_json(data)
        return 0
    if isinstance(data, dict):
        print(f"✅ workspace 作成: {data.get('id','?')}\t{data.get('name', args.name)}")
    else:
        _print_json(data)
    return 0


def cmd_collections_list(args) -> int:
    qs = f"?workspace_id={args.workspace}" if args.workspace else ""
    code, data = _request("GET", f"/api/collections{qs}")
    if code != 200:
        return _fail(args, code, data)
    if _want_json(args):
        _print_json(data)
        return 0
    if isinstance(data, list):
        for c in data:
            print(f"{c.get('id','?')}\t{c.get('name','?')}\t{c.get('access_level','-')}\tchunks={c.get('chunk_count','?')}")
        return 0
    _print_json(data)
    return 0


def cmd_collections_create(args) -> int:
    body = {"name": args.name, "workspace_id": args.workspace}
    code, data = _request("POST", "/api/collections", body=body)
    if code not in (200, 201):
        return _fail(args, code, data)
    if _want_json(args):
        _print_json(data)
        return 0
    if isinstance(data, dict):
        print(f"✅ collection 作成: {data.get('id','?')}\t{data.get('name', args.name)}")
    else:
        _print_json(data)
    return 0


def cmd_scan(args) -> int:
    if not args.source:
        print("--source SRC_ID は必須です", file=sys.stderr)
        return 2
    code, data = _request("POST", f"/api/sources/{args.source}/scan", body={})
    if not _want_json(args):
        print(f"scan trigger: status={code}")
        _print_json(data)
    # 完了をポーリング
    deadline = time.time() + 120
    while time.time() < deadline:
        c2, d2 = _request("GET", f"/api/sources/{args.source}", timeout=10)
        # C-11: 実在の終端状態は completed / failed / canceled / idle ('error' は存在しない)
        if c2 == 200 and isinstance(d2, dict) and d2.get("status") in ("completed", "failed", "canceled", "idle"):
            st = d2.get("status")
            ok = st in ("completed", "idle")
            if _want_json(args):
                _print_json(d2)
            else:
                print(f"{'✅' if ok else '❌'} scan {st}: file_count={d2.get('file_count', '?')}")
            return 0 if ok else 1
        time.sleep(1)
    if _want_json(args):
        _print_json({"error": "timeout", "status": code, "detail": "scan not finished in 120s"})
    else:
        print("⏱️  scan タイムアウト (120s) — サーバー側でまだ進行中の可能性", file=sys.stderr)
    return 1


def cmd_ingest_status(args) -> int:
    if args.job:
        code, data = _request("GET", f"/api/jobs/{args.job}", timeout=15)
    elif args.source:
        code, data = _request("GET", f"/api/sources/{args.source}", timeout=15)
    else:
        print("--source SRC_ID または --job JOB_ID のいずれかが必須です", file=sys.stderr)
        return 2
    if code != 200:
        return _fail(args, code, data)
    if _want_json(args):
        _print_json(data)
        return 0
    if isinstance(data, dict):
        st = data.get("status", "?")
        fc = data.get("file_count", data.get("processed", "?"))
        prog = data.get("progress", "")
        print(f"status={st}\tfile_count={fc}\tprogress={prog}")
    else:
        _print_json(data)
    return 0


def cmd_index_status(args) -> int:
    # 専用の索引状態エンドポイントは存在しないため、collections の chunk_count 合計で代替する。
    qs = f"?workspace_id={args.workspace}" if args.workspace else ""
    code, data = _request("GET", f"/api/collections{qs}")
    if code != 200:
        return _fail(args, code, data)
    if not isinstance(data, list):
        if _want_json(args):
            _print_json(data)
        else:
            _print_json(data)
        return 0
    total = sum(int(c.get("chunk_count") or 0) for c in data if isinstance(c, dict))
    note = "専用の索引状態APIが無いため collections の chunk_count 合計で代替表示"
    if _want_json(args):
        _print_json({
            "source": "derived_from_collections",
            "collections": len(data),
            "total_chunks": total,
            "note": note,
        })
        return 0
    print(f"index-status (代替): collections={len(data)}  total_chunks={total}")
    print(f"  注記: {note}")
    return 0


def cmd_publish(args) -> int:
    if not args.collection:
        print("--collection COL_ID は必須です", file=sys.stderr)
        return 2
    code, data = _request("POST", f"/api/collections/{args.collection}/publish", body={}, timeout=600)
    if _want_json(args):
        _print_json(data if isinstance(data, (dict, list)) else {"status_code": code, "body": data})
        return 0 if code in (200, 201, 202) else 1
    print(f"publish: status={code}")
    _print_json(data)
    return 0 if code in (200, 201, 202) else 1


def cmd_key(args) -> int:
    """鍵 API の骨組み。/api/keys 系が未実装 (404) のサーバでは not_implemented を返す。"""
    action = args.key_cmd
    if action == "list":
        code, data = _request("GET", "/api/keys")
    elif action == "issue":
        body: dict = {"role": getattr(args, "role", "viewer") or "viewer"}
        if getattr(args, "name", ""):
            body["name"] = args.name
        if getattr(args, "scope_workspace", None):
            body["scope_workspace"] = args.scope_workspace
        if getattr(args, "scope_collection", None):
            body["scope_collection"] = args.scope_collection
        code, data = _request("POST", "/api/keys", body=body)
    elif action == "revoke":
        code, data = _request("DELETE", f"/api/keys/{args.id}")
    else:
        print("key サブコマンド: list | issue | revoke", file=sys.stderr)
        return 2
    if code == 404:
        if _want_json(args):
            _print_json({"error": "not_implemented", "status": 404})
        else:
            print("未実装 (このサーバは鍵APIを持たない)", file=sys.stderr)
        return 1
    if code not in (200, 201, 204):
        return _fail(args, code, data)
    if _want_json(args):
        _print_json(data)
    else:
        _print_json(data)
    return 0


def cmd_chat(args) -> int:
    if not args.workspace or not args.query:
        print("--workspace WS_ID と --query 'テキスト' は必須です", file=sys.stderr)
        return 2
    body: dict = {"query": args.query, "workspace_id": args.workspace, "temperature": float(args.temperature)}
    if args.mode:
        # PHASE A-7 の preset (lite/standard/hq)
        body["preset"] = args.mode
    if args.role:
        body["role_override"] = args.role
    if args.session:
        body["session_id"] = args.session
    code, data = _request("POST", "/api/chat", body=body, timeout=300)
    if code != 200:
        return _fail(args, code, data)
    if _want_json(args):
        _print_json(data)
        return 0
    if isinstance(data, dict):
        print(data.get("answer", "(empty answer)"))
        srcs = data.get("sources") or []
        if srcs and not args.no_sources:
            print("\n--- sources ---")
            for s in srcs[:10]:
                if isinstance(s, dict):
                    print(f"  - {s.get('source_doc') or s.get('filename') or s.get('preview','')[:80]}")
                else:
                    print(f"  - {s}")
        return 0
    _print_json(data)
    return 0


# ─── DD-CYN-0143 ステップ1 (P-2): CLI を API と同等にする ───────────
# 作業の単位で束ねる。API の口の 1 対 1 写しにしない。
# 危険な操作 (削除・再公開・鍵の失効・restore・reset-password・restart) は --yes 必須。


def _show(args, code: int, data: Any, ok=(200, 201, 202, 204)) -> int:
    """共通の返し: 2xx なら JSON を出して 0、それ以外は _fail。"""
    if code not in ok:
        return _fail(args, code, data)
    _print_json(data if data is not None else {"ok": True, "status_code": code})
    return 0


def _require_yes(args, what: str) -> int | None:
    """危険な操作の門。--yes が無ければ実行せず終了コード 2。"""
    if getattr(args, "yes", False):
        return None
    msg = f"{what} は危険な操作です。--yes を付けたときだけ実行します。"
    if _want_json(args):
        _print_json({"error": "confirmation_required", "detail": msg})
    else:
        print(msg, file=sys.stderr)
    return 2


# feature.* / exec.* は受信した Pod のプロセス内だけ即時反映される (他レプリカは再起動まで旧値)。
_RESTART_NOTICE_PREFIXES = ("feature.", "exec.")


def cmd_overview(args) -> int:
    """見る系をまとめて表示。--section で絞る。"""
    sections = {
        "dashboard": [("GET", "/api/dashboard/summary")],
        "stats": [("GET", "/api/stats/performance"), ("GET", "/api/stats/model"), ("GET", "/api/stats/rag-quality")],
        "catalog": [("GET", "/api/catalog")],
        "alerts": [("GET", "/api/alerts")],
        "storage": [("GET", "/api/admin/storage-info")],
        "feedback": [("GET", "/api/feedback/stats"), ("GET", "/api/feedback/negatives")],
        "queue": [("GET", "/api/queue/status")],
    }
    want = [args.section] if args.section != "all" else list(sections)
    out: dict = {}
    any_fail = False
    for s in want:
        for method, path in sections[s]:
            code, data = _request(method, path, timeout=30)
            out[path] = data if code == 200 else {"error": code, "detail": data}
            if code != 200:
                any_fail = True
    _print_json(out)
    return 1 if any_fail else 0


def cmd_settings(args) -> int:
    ac = args.settings_cmd
    if ac == "show":
        out: dict = {}
        code, data = _request("GET", "/api/settings/export", timeout=15)
        if code != 200:
            return _fail(args, code, data)
        out["settings(db)"] = data
        c2, d2 = _request("GET", "/api/chunking-config", timeout=15)
        out["chunking(実効値と出所)"] = d2 if c2 == 200 else {"error": c2}
        c3, d3 = _request("GET", "/api/features", timeout=15)
        out["features"] = d3 if c3 == 200 else {"error": c3}
        c4, d4 = _request("GET", "/api/execution-config", timeout=15)
        out["execution"] = d4 if c4 == 200 else {"error": c4}
        out["注記"] = (
            "settings(db) は DB 層 (最優先)。chunking の sources 欄が層の出所。"
            "feature.*/exec.* の変更は受信した Pod だけ即時で、全レプリカへは restart --yes が要る。"
        )
        _print_json(out)
        return 0
    if ac == "set":
        r = _require_yes(args, f"settings set ({args.key})")
        if r is not None:
            return r
        code, data = _request("PUT", "/api/settings", body={args.key: args.value}, timeout=15)
        if code != 200:
            return _fail(args, code, data)
        notice = None
        if args.key.startswith(_RESTART_NOTICE_PREFIXES):
            notice = (
                f"{args.key} は受信した Pod だけ即時反映。全レプリカへ効かせるには "
                "restart --yes をレプリカの数だけ実行する。"
            )
        _print_json({"ok": True, "key": args.key, "value": args.value, "restart_notice": notice})
        if notice and not _want_json(args):
            print(f"⚠️  {notice}", file=sys.stderr)
        return 0
    if ac == "chunking":
        body: dict = {}
        if args.size is not None:
            body["chunk_size"] = None if args.size == "unset" else int(args.size)
        if args.overlap is not None:
            body["chunk_overlap"] = None if args.overlap == "unset" else int(args.overlap)
        if not body:
            code, data = _request("GET", "/api/chunking-config", timeout=15)
            return _show(args, code, data)
        r = _require_yes(args, "settings chunking (グローバル上書きの変更)")
        if r is not None:
            return r
        code, data = _request("PATCH", "/api/chunking-config", body=body, timeout=15)
        return _show(args, code, data)
    if ac == "export":
        code, data = _request("GET", "/api/settings/export", timeout=15)
        if code != 200:
            return _fail(args, code, data)
        if args.out:
            Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            _print_json({"ok": True, "written": args.out, "keys": len((data or {}).get("settings") or {})})
            return 0
        _print_json(data)
        return 0
    if ac == "import":
        r = _require_yes(args, "settings import (設定の一括上書き)")
        if r is not None:
            return r
        try:
            payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"ファイルを読めません: {e}", file=sys.stderr)
            return 2
        code, data = _request("POST", "/api/settings/import", body=payload, timeout=30)
        return _show(args, code, data)
    if ac == "models":
        code, data = _request("GET", "/api/settings/models", timeout=30)
        return _show(args, code, data)
    if ac == "test-connection":
        code, data = _request("POST", "/api/settings/test-connection", body={}, timeout=60)
        return _show(args, code, data)
    print("settings サブコマンド: show | set | chunking | export | import | models | test-connection", file=sys.stderr)
    return 2


def cmd_ingest_roots(args) -> int:
    ac = args.roots_cmd
    if ac == "list":
        code, data = _request("GET", "/api/ingest-roots", timeout=15)
        return _show(args, code, data)
    if ac == "add":
        # POST /api/ingest-roots は body の "path" だけを読む (routers/sources.py add_ingest_root; 名前と表示名は
        # サーバー側がフォルダ名から決める)。"host_path" で送っていたため常に 400 "path が要ります" になっていた。
        body = {"path": args.path}
        code, data = _request("POST", "/api/ingest-roots", body=body, timeout=30)
        return _show(args, code, data)
    if ac == "remove":
        r = _require_yes(args, f"ingest-roots remove ({args.name})")
        if r is not None:
            return r
        code, data = _request("DELETE", f"/api/ingest-roots/{args.name}", timeout=15)
        return _show(args, code, data)
    print("ingest-roots サブコマンド: list | add | remove", file=sys.stderr)
    return 2


def cmd_sources(args) -> int:
    ac = args.sources_cmd
    if ac == "list":
        qs = f"?workspace_id={args.workspace}" if args.workspace else ""
        code, data = _request("GET", f"/api/sources{qs}", timeout=15)
        return _show(args, code, data)
    if ac == "show":
        code, data = _request("GET", f"/api/sources/{args.id}", timeout=15)
        return _show(args, code, data)
    if ac == "add":
        body = {"workspace_id": args.workspace, "path": args.path}
        if args.name:
            body["name"] = args.name
        code, data = _request("POST", "/api/sources", body=body, timeout=30)
        return _show(args, code, data)
    if ac == "remove":
        r = _require_yes(args, f"sources remove ({args.id})")
        if r is not None:
            return r
        code, data = _request("DELETE", f"/api/sources/{args.id}", timeout=60)
        return _show(args, code, data)
    if ac == "files":
        code, data = _request("GET", f"/api/sources/{args.id}/files", timeout=30)
        return _show(args, code, data)
    print("sources サブコマンド: list | show | add | remove | files", file=sys.stderr)
    return 2


def cmd_link_files(args) -> int:
    cid = args.collection
    file_ids = [x for x in (args.file_ids or "").split(",") if x]
    if not file_ids:
        code, data = _request("GET", f"/api/collections/{cid}/unlinked-files", timeout=30)
        if code != 200:
            return _fail(args, code, data)
        file_ids = [f.get("id") for f in ((data or {}).get("files") or []) if isinstance(f, dict) and f.get("id")]
        if not file_ids:
            _print_json({"ok": True, "linked": 0, "note": "unlinked-files は 0 件"})
            return 0
    code, data = _request("POST", f"/api/collections/{cid}/link-files", body={"file_ids": file_ids}, timeout=60)
    return _show(args, code, data)


def cmd_scan2(args) -> int:
    ac = args.scan_cmd
    if ac == "start":
        code, data = _request("POST", f"/api/sources/{args.source}/scan/async", body={}, timeout=30)
        if code != 200:
            return _fail(args, code, data)
        if not args.wait:
            return _show(args, code, data)
        deadline = time.time() + 300
        while time.time() < deadline:
            c2, d2 = _request("GET", f"/api/sources/{args.source}", timeout=10)
            # C-11: 実在の終端状態は completed / failed / canceled / idle ('error' は存在しない)
            if c2 == 200 and isinstance(d2, dict) and d2.get("status") in ("completed", "failed", "canceled", "idle"):
                r = _show(args, c2, d2)
                if r == 0 and d2.get("status") in ("failed", "canceled"):
                    return 1
                return r
            time.sleep(2)
        _print_json({"error": "timeout", "detail": "scan が 300 秒で完了しなかった (scan status で追う)"})
        return 1
    if ac == "status":
        code, data = _request("GET", f"/api/sources/{args.source}", timeout=15)
        return _show(args, code, data)
    if ac == "cancel":
        code, data = _request("POST", f"/api/sources/{args.source}/scan/cancel", body={}, timeout=15)
        # C-11: 非走査中 409・不存在 404 は人向けに整形して返す
        if code in (404, 409):
            detail = data.get("detail") if isinstance(data, dict) else data
            msg = detail or ("source が見つからない" if code == 404 else "この source は走査中ではない")
            if _want_json(args):
                _print_json({"error": "cancel_rejected", "status": code, "detail": msg})
            else:
                print(f"中止できない ({code}): {msg}", file=sys.stderr)
            return 1
        if code == 200 and isinstance(data, dict) and not _want_json(args):
            print(f"✅ 中止を受け付けた: {data.get('status', '?')}  file_count_so_far={data.get('file_count_so_far', '?')}")
            return 0
        return _show(args, code, data)
    print("scan サブコマンド: start | status | cancel (旧形式 scan --source も可)", file=sys.stderr)
    return 2


def cmd_publish2(args) -> int:
    ac = args.publish_cmd
    if ac == "start":
        cid = args.collection
        c0, d0 = _request("GET", f"/api/collections/{cid}", timeout=15)
        state = (d0 or {}).get("status") or (d0 or {}).get("state") if isinstance(d0, dict) else None
        if str(state) in ("ready", "published"):
            r = _require_yes(args, f"publish start (公開済み {state} への再公開 = 索引の作り直し)")
            if r is not None:
                return r
        code, data = _request("POST", f"/api/collections/{cid}/publish/async", body={}, timeout=30)
        if code not in (200, 201, 202):
            return _fail(args, code, data)
        if not args.wait:
            return _show(args, code, data)
        job_id = (data or {}).get("job_id") if isinstance(data, dict) else None
        deadline = time.time() + 3600
        while job_id and time.time() < deadline:
            c2, d2 = _request("GET", f"/api/jobs/{job_id}", timeout=15)
            if c2 == 200 and isinstance(d2, dict) and d2.get("status") in ("completed", "failed", "stopped"):
                return _show(args, c2, d2)
            time.sleep(3)
        _print_json({"error": "timeout", "job_id": job_id, "detail": "publish status --job で追う"})
        return 1
    if ac == "status":
        if args.job:
            code, data = _request("GET", f"/api/jobs/{args.job}", timeout=15)
        elif args.collection:
            code, data = _request("GET", f"/api/jobs?collection_id={args.collection}&limit=10", timeout=15)
        else:
            code, data = _request("GET", "/api/jobs?limit=20", timeout=15)
        return _show(args, code, data)
    if ac == "stop":
        code, data = _request("POST", f"/api/collections/{args.collection}/publish/stop", body={}, timeout=30)
        return _show(args, code, data)
    if ac == "recover":
        r = _require_yes(args, "publish recover (publishing 状態の作り直し)")
        if r is not None:
            return r
        code, data = _request("POST", f"/api/collections/{args.collection}/publish/recover", body={}, timeout=60)
        return _show(args, code, data)
    if ac == "diff":
        code, data = _request("GET", f"/api/collections/{args.collection}/publish-diff", timeout=60)
        return _show(args, code, data)
    if ac == "history":
        code, data = _request("GET", f"/api/workspaces/{args.workspace}/publish-history", timeout=30)
        return _show(args, code, data)
    print("publish サブコマンド: start | status | stop | recover | diff | history (旧形式 publish --collection も可)", file=sys.stderr)
    return 2


def cmd_users(args) -> int:
    ac = args.users_cmd
    if ac == "list":
        code, data = _request("GET", "/api/admin/users", timeout=15)
        return _show(args, code, data)
    if ac == "create":
        body = {"username": args.username, "password": args.password, "role": args.role, "name": args.name or args.username}
        code, data = _request("POST", "/api/admin/users", body=body, timeout=15)
        return _show(args, code, data)
    if ac == "update":
        body = {}
        if args.role:
            body["role"] = args.role
        if args.name:
            body["name"] = args.name
        if args.active is not None:
            body["is_active"] = int(args.active)
        code, data = _request("PATCH", f"/api/admin/users/{args.id}", body=body, timeout=15)
        return _show(args, code, data)
    if ac == "delete":
        purge = bool(getattr(args, "purge", False))
        what = (
            f"users delete --purge ({args.id} = 完全削除。従属行ごと物理削除され、元に戻せない)"
            if purge else f"users delete ({args.id})"
        )
        r = _require_yes(args, what)
        if r is not None:
            return r
        qs = "?purge=true" if purge else ""
        code, data = _request("DELETE", f"/api/admin/users/{args.id}{qs}", timeout=15)
        return _show(args, code, data)
    if ac == "reset-password":
        r = _require_yes(args, f"users reset-password ({args.id})")
        if r is not None:
            return r
        code, data = _request("POST", f"/api/admin/users/{args.id}/reset-password", body={"password": args.password}, timeout=15)
        return _show(args, code, data)
    print("users サブコマンド: list | create | update | delete | reset-password", file=sys.stderr)
    return 2


def cmd_backup(args) -> int:
    ac = args.backup_cmd
    if ac == "create":
        code, data = _request("POST", "/api/admin/backup", body={"label": args.label or ""}, timeout=600)
        return _show(args, code, data)
    if ac == "list":
        code, data = _request("GET", "/api/admin/backups", timeout=15)
        return _show(args, code, data)
    if ac == "restore":
        r = _require_yes(args, f"backup restore ({args.name} = 現行 DB の置き換え)")
        if r is not None:
            return r
        code, data = _request("POST", f"/api/admin/backups/{args.name}/restore", body={}, timeout=600)
        return _show(args, code, data)
    if ac == "delete":
        r = _require_yes(args, f"backup delete ({args.name})")
        if r is not None:
            return r
        code, data = _request("DELETE", f"/api/admin/backups/{args.name}", timeout=30)
        return _show(args, code, data)
    if ac == "verify":
        # 専用の verify API は無い。一覧に名前が在ること・メタ (サイズ等) を返すまで。
        code, data = _request("GET", "/api/admin/backups", timeout=15)
        if code != 200:
            return _fail(args, code, data)
        names = [b.get("name") for b in (data or []) if isinstance(b, dict)]
        found = args.name in names
        _print_json({
            "name": args.name,
            "found": found,
            "meta": next((b for b in (data or []) if isinstance(b, dict) and b.get("name") == args.name), None),
            "note": "サーバに verify 専用 API は無い。存在とメタの確認まで (中身の復元試験は restore で行う)。",
        })
        return 0 if found else 1
    print("backup サブコマンド: create | list | restore | delete | verify", file=sys.stderr)
    return 2


def cmd_cleanup(args) -> int:
    ac = args.cleanup_cmd
    if ac == "archived-list":
        code, data = _request("GET", "/api/archived", timeout=15)
        return _show(args, code, data)
    if ac == "restore":
        code, data = _request("POST", f"/api/archived/{args.kind}/{args.id}/restore", body={}, timeout=30)
        return _show(args, code, data)
    if ac == "purge":
        r = _require_yes(args, f"cleanup purge ({args.kind}/{args.id} = 完全削除)")
        if r is not None:
            return r
        code, data = _request("DELETE", f"/api/archived/{args.kind}/{args.id}", timeout=60)
        return _show(args, code, data)
    if ac == "vacuum":
        r = _require_yes(args, "cleanup vacuum (DB 圧縮)")
        if r is not None:
            return r
        code, data = _request("POST", "/api/admin/maintenance/vacuum", body={}, timeout=600)
        return _show(args, code, data)
    if ac == "orphans":
        r = _require_yes(args, "cleanup orphans (chromadb 孤児掃除)")
        if r is not None:
            return r
        code, data = _request("POST", "/api/admin/cleanup/chromadb-orphans", body={}, timeout=600)
        return _show(args, code, data)
    print("cleanup サブコマンド: archived-list | restore | purge | vacuum | orphans", file=sys.stderr)
    return 2


def cmd_audit(args) -> int:
    ac = args.audit_cmd
    if ac == "logs":
        qs = []
        if args.workspace:
            qs.append(f"workspace_id={args.workspace}")
        if args.limit:
            qs.append(f"limit={args.limit}")
        code, data = _request("GET", "/api/audit-logs" + (("?" + "&".join(qs)) if qs else ""), timeout=30)
        return _show(args, code, data)
    if ac == "export":
        code, data = _request("GET", "/api/audit-logs/export", timeout=60)
        if code != 200:
            return _fail(args, code, data)
        if args.out:
            text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
            Path(args.out).write_text(text, encoding="utf-8")
            _print_json({"ok": True, "written": args.out})
            return 0
        _print_json(data) if not isinstance(data, str) else print(data)
        return 0
    if ac == "change-log":
        code, data = _request("GET", "/api/admin/change-log", timeout=15)
        return _show(args, code, data)
    if ac == "compliance":
        out = {}
        for p in ("/api/compliance/checklist", "/api/compliance/report"):
            c, d = _request("GET", p, timeout=30)
            out[p] = d if c == 200 else {"error": c, "detail": d}
        _print_json(out)
        return 0
    print("audit サブコマンド: logs | export | change-log | compliance", file=sys.stderr)
    return 2


def cmd_sessions(args) -> int:
    ac = args.sessions_cmd
    if ac == "list":
        code, data = _request("GET", "/api/sessions", timeout=15)
        return _show(args, code, data)
    if ac == "show":
        code, data = _request("GET", f"/api/sessions/{args.id}", timeout=15)
        if code != 200:
            return _fail(args, code, data)
        c2, d2 = _request("GET", f"/api/sessions/{args.id}/messages", timeout=15)
        _print_json({"session": data, "messages": d2 if c2 == 200 else {"error": c2}})
        return 0
    if ac == "delete":
        r = _require_yes(args, f"sessions delete ({args.id})")
        if r is not None:
            return r
        code, data = _request("DELETE", f"/api/sessions/{args.id}", timeout=15)
        return _show(args, code, data)
    print("sessions サブコマンド: list | show | delete", file=sys.stderr)
    return 2


def cmd_policy(args) -> int:
    ac = args.policy_cmd
    if ac == "list":
        code, data = _request("GET", "/api/policies", timeout=15)
        return _show(args, code, data)
    if ac == "create":
        try:
            body = json.loads(args.body)
        except Exception as e:
            print(f"--body の JSON を読めません: {e}", file=sys.stderr)
            return 2
        code, data = _request("POST", "/api/policies", body=body, timeout=15)
        return _show(args, code, data)
    if ac == "update":
        try:
            body = json.loads(args.body)
        except Exception as e:
            print(f"--body の JSON を読めません: {e}", file=sys.stderr)
            return 2
        code, data = _request("PUT", f"/api/policies/{args.id}", body=body, timeout=15)
        return _show(args, code, data)
    if ac == "delete":
        r = _require_yes(args, f"policy delete ({args.id})")
        if r is not None:
            return r
        code, data = _request("DELETE", f"/api/policies/{args.id}", timeout=15)
        return _show(args, code, data)
    if ac == "matrix":
        code, data = _request("GET", "/api/policy-matrix", timeout=15)
        return _show(args, code, data)
    if ac == "pii":
        code, data = _request("GET", "/api/pii-detections", timeout=30)
        return _show(args, code, data)
    print("policy サブコマンド: list | create | update | delete | matrix | pii", file=sys.stderr)
    return 2


def cmd_mcp(args) -> int:
    ac = args.mcp_cmd
    if ac == "config":
        code, data = _request("GET", "/api/mcp/config", timeout=15)
        return _show(args, code, data)
    if ac == "fingerprint":
        code, data = _request("GET", "/api/mcp/fingerprint", timeout=15)
        return _show(args, code, data)
    if ac == "test":
        code, data = _request("GET", "/api/mcp/test-connection", timeout=30)
        return _show(args, code, data)
    print("mcp サブコマンド: config | fingerprint | test", file=sys.stderr)
    return 2


def cmd_report(args) -> int:
    ac = args.report_cmd
    if ac == "list":
        code, data = _request("GET", "/api/reports", timeout=15)
        return _show(args, code, data)
    if ac == "show":
        code, data = _request("GET", f"/api/reports/{args.id}", timeout=15)
        return _show(args, code, data)
    if ac == "generate":
        code, data = _request("POST", "/api/reports/generate", body={}, timeout=600)
        return _show(args, code, data)
    print("report サブコマンド: list | show | generate", file=sys.stderr)
    return 2


def cmd_restart(args) -> int:
    r = _require_yes(args, "restart (Pod の再起動)")
    if r is not None:
        return r
    pods: list = []
    times = max(1, int(args.times))
    for i in range(times):
        code, data = _request("POST", "/api/admin/restart", body={"confirm": True}, timeout=15)
        if code != 200:
            return _fail(args, code, data)
        pods.append((data or {}).get("pod") if isinstance(data, dict) else None)
        if i + 1 < times:
            time.sleep(float(args.interval))
    _print_json({"ok": True, "restarted_pods": pods,
                 "note": "複数レプリカでは応答した Pod だけが再起動する。全レプリカへは --times をレプリカ数以上に"})
    return 0


def cmd_workspaces_manage(args) -> int:
    ac = args.ws_cmd
    if ac == "update":
        body = {}
        if args.name:
            body["name"] = args.name
        if args.description is not None:
            body["description"] = args.description
        add_users = list(getattr(args, "add_user", None) or [])
        del_users = list(getattr(args, "remove_user", None) or [])
        if add_users or del_users:
            # N-4: PATCH の user_ids は全置換のため「現メンバーを読み→マージ/除去→全量を送る」。
            # username → id の解決は admin の利用者一覧を 1 回だけ引いて行う。
            cu, du = _request("GET", "/api/admin/users", timeout=15)
            if cu != 200 or not isinstance(du, list):
                return _fail(args, cu, du)
            by_username = {u.get("username"): u.get("id") for u in du if isinstance(u, dict)}
            known_ids = {u.get("id") for u in du if isinstance(u, dict)}

            def _resolve(x: str) -> str | None:
                return x if x in known_ids else by_username.get(x)

            # 現メンバー: 単体 GET に user_ids が無い作りなら一覧の埋め込みで補う
            cw, dw = _request("GET", f"/api/workspaces/{args.id}", timeout=15)
            if cw != 200 or not isinstance(dw, dict):
                return _fail(args, cw, dw)
            current = dw.get("user_ids")
            if not isinstance(current, list):
                cl, dl = _request("GET", "/api/workspaces", timeout=15)
                if cl == 200 and isinstance(dl, list):
                    current = next(
                        (w.get("user_ids") for w in dl if isinstance(w, dict) and w.get("id") == args.id), None
                    )
            if not isinstance(current, list):
                print("現メンバー (user_ids) を取得できないため中止 (user_ids は全置換のため)", file=sys.stderr)
                return 1
            members = list(dict.fromkeys(current))
            for x in add_users:
                uid = _resolve(x)
                if not uid:
                    print(f"--add-user {x}: 利用者が見つからない (username か user_id で指定)", file=sys.stderr)
                    return 2
                if uid not in members:
                    members.append(uid)
            for x in del_users:
                uid = _resolve(x)
                if not uid or uid not in members:
                    print(f"⚠️  --remove-user {x}: メンバーに存在しないため何もしない", file=sys.stderr)
                    continue
                members.remove(uid)
            body["user_ids"] = members
        if not body:
            print("--name / --description / --add-user / --remove-user のいずれかが必要です", file=sys.stderr)
            return 2
        code, data = _request("PATCH", f"/api/workspaces/{args.id}", body=body, timeout=15)
        return _show(args, code, data)
    if ac == "archive":
        code, data = _request("PATCH", f"/api/workspaces/{args.id}/archive", body={}, timeout=15)
        return _show(args, code, data)
    if ac == "unarchive":
        code, data = _request("PATCH", f"/api/workspaces/{args.id}/unarchive", body={}, timeout=15)
        return _show(args, code, data)
    if ac == "delete":
        r = _require_yes(args, f"workspaces delete ({args.id})")
        if r is not None:
            return r
        code, data = _request("DELETE", f"/api/workspaces/{args.id}", timeout=60)
        return _show(args, code, data)
    if ac == "export":
        code, data = _request("GET", f"/api/workspaces/{args.id}/export", timeout=120)
        return _show(args, code, data)
    return 2


def cmd_collections_manage(args) -> int:
    ac = args.col_cmd
    if ac == "update":
        try:
            body = json.loads(args.body)
        except Exception as e:
            print(f"--body の JSON を読めません: {e}", file=sys.stderr)
            return 2
        code, data = _request("PUT", f"/api/collections/{args.id}", body=body, timeout=15)
        return _show(args, code, data)
    if ac == "archive":
        code, data = _request("PATCH", f"/api/collections/{args.id}/archive", body={}, timeout=15)
        return _show(args, code, data)
    if ac == "unarchive":
        code, data = _request("PATCH", f"/api/collections/{args.id}/unarchive", body={}, timeout=15)
        return _show(args, code, data)
    if ac == "delete":
        r = _require_yes(args, f"collections delete ({args.id} = 結ばれた資料も連鎖削除される)")
        if r is not None:
            return r
        code, data = _request("DELETE", f"/api/collections/{args.id}", timeout=60)
        return _show(args, code, data)
    return 2


def cmd_login(args) -> int:
    """N-2: ログインしてトークンを ~/.cynovela_cli.env へ保存する。"""
    password = args.password
    if not password:
        import getpass

        try:
            password = getpass.getpass(f"パスワード ({args.username}): ")
        except (EOFError, KeyboardInterrupt):
            print("パスワードが入力されなかったため中止", file=sys.stderr)
            return 2
    if not password:
        print("パスワードが空です", file=sys.stderr)
        return 2
    body: dict = {"username": args.username, "password": password}
    if args.ttl_hours is not None:
        try:
            body["ttl_hours"] = float(args.ttl_hours)
        except ValueError:
            print("--ttl-hours は数値で指定してください", file=sys.stderr)
            return 2
    code, data = _request("POST", "/api/auth/login", body=body, timeout=30)
    if code != 200 or not isinstance(data, dict) or not data.get("access_token"):
        return _fail(args, code, data)
    updates = {"CYNOVELA_TOKEN": data["access_token"]}
    if not _env_file.get("CYNOVELA_URL"):
        updates["CYNOVELA_URL"] = BASE_URL
    env_path = _update_env_file(updates)
    role = data.get("role")
    expires_in = data.get("expires_in")
    # トークン値そのものは表示しない (--json でもマスク)
    if _want_json(args):
        _print_json({"saved": True, "env_file": str(env_path), "expires_in": expires_in, "role": role})
        return 0
    _life = "期限なし" if expires_in in (None, "") else f"{expires_in}s"
    print(f"✅ ログイン成功: role={role}  expires_in={_life}")
    print(f"   トークンの保存先: {env_path} (0600)")
    if data.get("must_change_password"):
        print("⚠️  初期パスワードのままです。パスワードの変更が必要です。", file=sys.stderr)
    return 0


def cmd_logout(args) -> int:
    """N-2: サーバの logout を呼び、~/.cynovela_cli.env の CYNOVELA_TOKEN 行を消す。"""
    server_result = None
    if TOKEN:
        code, data = _request("POST", "/api/auth/logout", body={}, timeout=15)
        server_result = {"status_code": code, "body": data}
    had_token = bool(_env_file.get("CYNOVELA_TOKEN"))
    env_path = _update_env_file({}, removes=("CYNOVELA_TOKEN",))
    if _want_json(args):
        _print_json({"ok": True, "token_removed": had_token, "env_file": str(env_path), "server": server_result})
        return 0
    if server_result:
        print(f"サーバ側 logout: status={server_result['status_code']}")
    if had_token:
        print(f"✅ {env_path} から CYNOVELA_TOKEN を消した")
    else:
        print(f"{env_path} に保存済みトークンは無かった (消すものなし)")
    return 0


def cmd_jobs(args) -> int:
    """N-3: GET /api/jobs をジョブの種類 (kind) 横断で一覧する。"""
    qs = [f"kind={args.kind}"]
    if args.status:
        qs.append(f"status={args.status}")
    if args.limit:
        qs.append(f"limit={args.limit}")
    code, data = _request("GET", "/api/jobs?" + "&".join(qs), timeout=15)
    if code != 200:
        return _fail(args, code, data)
    if _want_json(args):
        _print_json(data)
        return 0
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        _print_json(data)
        return 0
    print(f"jobs ({len(jobs)})")
    for j in jobs:
        if not isinstance(j, dict):
            print(f"  - {j}")
            continue
        if j.get("type") == "scan":
            print(f"{j.get('id', '?')}\tscan\t{j.get('status', '?')}\t{j.get('name', '?')}"
                  f"\tfiles={j.get('file_count', '?')}\t{j.get('last_scanned') or '-'}")
        else:
            print(f"{j.get('id', '?')}\t{j.get('type', 'publish')}\t{j.get('status', '?')}"
                  f"\tcol={j.get('collection_id', '?')}\t{j.get('updated_at') or j.get('created_at') or '-'}")
    return 0


def cmd_collections_status(args) -> int:
    """N-5: コレクションの状態・chunk数・伏字件数・直近ジョブを1画面に束ねる。"""
    cid = args.id
    code, data = _request("GET", f"/api/collections/{cid}", timeout=15)
    if code != 200:
        return _fail(args, code, data)
    col = data if isinstance(data, dict) else {}
    c2, d2 = _request("GET", f"/api/collections/{cid}/publish-summary", timeout=30)
    summary = d2 if (c2 == 200 and isinstance(d2, dict)) else None
    c3, d3 = _request("GET", f"/api/jobs?kind=publish&collection_id={cid}&limit=1", timeout=15)
    last_job = None
    if c3 == 200 and isinstance(d3, dict) and isinstance(d3.get("jobs"), list) and d3["jobs"]:
        last_job = d3["jobs"][0]
    if _want_json(args):
        _print_json({
            "collection": col,
            "publish_summary": summary if summary is not None else {
                "error": c2, "note": "管理者のみ" if c2 == 403 else None,
            },
            "last_job": last_job,
        })
        return 0
    print(f"collection: {col.get('id', '?')}  {col.get('name', '?')}")
    print(f"  状態: {col.get('status', '?')}  chunk数: {col.get('chunk_count', '?')}")
    if summary:
        print(f"  伏字件数: {summary.get('pii_count', '?')}"
              f"  (ファイル {summary.get('file_count', '?')} / 除外 {summary.get('excluded_count', '?')})")
    elif c2 == 403:
        print("  伏字件数: 管理者のみ (publish-summary は admin 限定)")
    else:
        print(f"  伏字件数: 取得できない ({c2})")
    if last_job:
        print(f"  直近ジョブ: {last_job.get('id', '?')}  status={last_job.get('status', '?')}"
              f"  {last_job.get('updated_at') or last_job.get('created_at') or ''}")
    else:
        print("  直近ジョブ: なし" + ("" if c3 == 200 else f" (取得できない {c3})"))
    return 0


def cmd_doctor(args) -> int:
    """R-3: この機材で足りないものを名指しする。サーバ側でなく手元 (ホスト) の検査。
    N-6: --remote はホスト検査を飛ばし、サーバ側の口を名指しで検査する。"""
    import shutil as _shutil
    import socket as _socket
    import subprocess as _sp

    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, fix: str = "") -> None:
        checks.append({"check": name, "ok": ok, "detail": detail, "fix": (fix if not ok else "")})

    if getattr(args, "remote", False):
        remote_eps = [
            ("health", "/api/health"),
            ("health_detailed(admin)", "/api/health/detailed"),
            ("ready", "/api/ready"),
            ("queue", "/api/queue/status"),
            ("mcp(admin)", "/api/mcp/test-connection"),
        ]
        for name, ep in remote_eps:
            code, data = _request("GET", ep, timeout=30)
            detail = f"{ep} -> {code}"
            if isinstance(data, dict) and data.get("status"):
                detail += f" status={data.get('status')}"
            add(name, code == 200, detail,
                "admin のトークンで再実行する" if code in (401, 403)
                else "サーバ側の当該コンポーネントとログを確かめる")
        ok_all = all(c["ok"] for c in checks)
        if _want_json(args):
            _print_json({"ok": ok_all, "remote": True, "checks": checks})
        else:
            for c in checks:
                mark = "✅" if c["ok"] else "❌"
                print(f"{mark} {c['check']}: {c['detail']}")
                if not c["ok"] and c["fix"]:
                    print(f"   直し方: {c['fix']}")
            print("doctor --remote:", "全ての口が応えている" if ok_all else "応えない口が上に ❌ で出ている")
        return 0 if ok_all else 1

    # 1. サーバ疎通
    code, data = _request("GET", "/api/health", timeout=5)
    add("server", code == 200,
        f"{BASE_URL} -> {code}" + (f" version={data.get('version')}" if isinstance(data, dict) else ""),
        f"サーバが {BASE_URL} で応えない。クラスタの稼働と --url を確かめる")
    context = os.environ.get("HAN_SOLO_CONTEXT", "")
    if context:
        executable = _shutil.which("kubectl")
        ok, detail = False, "kubectl missing"
        if executable:
            try:
                result = _sp.run([executable, "--context", context, "get", "nodes", "-o", "json"],
                                 capture_output=True, text=True, timeout=15)
                nodes = json.loads(result.stdout).get("items", []) if result.returncode == 0 else []
                ok = bool(nodes) and all(any(c.get("type") == "Ready" and c.get("status") == "True"
                       for c in node.get("status", {}).get("conditions", [])) for node in nodes)
                detail = f"context={context}, nodes={len(nodes)}, ready={ok}"
            except Exception as exc:
                detail = f"context={context}: {type(exc).__name__}"
        add("kubernetes", ok, detail, "Check the explicit Kubernetes context and node readiness")
    else:
        # 2. podman
        p = _shutil.which("podman")
        add("podman", bool(p), p or "見つからない", "podman をインストールする (brew install podman)")
        # 3. k3d
        k = _shutil.which("k3d")
        add("k3d", bool(k), k or "見つからない", "k3d をインストールする (brew install k3d)")
        # 4. DOCKER_HOST (k3d が podman を掴めるか)
        dh = os.environ.get("DOCKER_HOST", "")
        sock = ""
        if p:
            try:
                sock = _sp.run(
                    ["podman", "machine", "inspect", "--format", "{{.ConnectionInfo.PodmanSocket.Path}}"],
                    capture_output=True, text=True, timeout=10,
                ).stdout.strip()
            except Exception:
                sock = ""
        dh_ok = bool(dh) or bool(sock)
        add("DOCKER_HOST", dh_ok,
            dh or (f"未設定 (podman socket: {sock})" if sock else "未設定で podman socket も引けない"),
            'export DOCKER_HOST="unix://$(podman machine inspect --format \'{{.ConnectionInfo.PodmanSocket.Path}}\')" を設定する')
    # 5. LLM/MAS 接続先 (トークンがあればサーバ設定から実値を引く)
    llm_detail, llm_ok, llm_fix = "トークン無しのため該当なし (–token を渡すと実接続先を検査する)", True, ""
    if TOKEN and context:
        c2, d2 = _request("POST", "/api/settings/test-connection", body={}, timeout=30)
        llm_ok = c2 == 200 and isinstance(d2, dict) and d2.get("status") == "connected"
        llm_detail = f"server-side provider probe -> {c2}, connected={llm_ok}"
        llm_fix = "Check the provider endpoint from inside the cluster"
    elif TOKEN:
        c2, d2 = _request("GET", "/api/settings/export", timeout=10)
        if c2 == 200 and isinstance(d2, dict):
            ep = ((d2.get("settings") or {}).get("llm_endpoint") or "").strip()
            if ep:
                # 旧Podman gateway形式は doctor がホスト上で検査できるよう loopback へ読み替える。
                legacy_podman_gateway = "192.168." + "127.254"
                probe = ep.replace(f"://{legacy_podman_gateway}", "://127.0.0.1").replace(
                    "://host.containers.internal", "://127.0.0.1"
                )
                try:
                    import urllib.request as _ur

                    with _ur.urlopen(f"{probe.rstrip('/')}/v1/models", timeout=5) as r:
                        llm_ok = r.status == 200
                        llm_detail = f"{ep}" + (f" (ホストでは {probe} で検査)" if probe != ep else "") + f" -> {r.status}"
                except Exception as e:
                    llm_ok = False
                    llm_detail = f"{ep} (ホストでは {probe} で検査) -> 届かない ({e})"
                    llm_fix = "LLM サーバ (LM Studio/MAS) を起動し、llm_endpoint の先で応えることを確かめる"
            else:
                llm_detail = "llm_endpoint が未設定"
        else:
            llm_ok, llm_detail, llm_fix = True, f"設定を読めない ({c2}) — admin トークンで再実行", ""
    add("llm_endpoint", llm_ok, llm_detail, llm_fix)
    # 6. 空き容量
    du = _shutil.disk_usage(str(Path.home()))
    free_gb = du.free / (1024 ** 3)
    add("disk", free_gb >= 20, f"空き {free_gb:.1f} GiB",
        "空き容量が 20GiB を切っている。イメージ・控えの置き場を空ける")
    # 7. 入口ポートが応えるか
    try:
        host_port = BASE_URL.split("://", 1)[-1]
        host, _, port_s = host_port.partition(":")
        with _socket.create_connection((host or "127.0.0.1", int(port_s or "80")), timeout=3):
            add("port", True, f"{host}:{port_s} 接続可")
    except Exception as e:
        add("port", False, f"{BASE_URL} へ TCP 接続できない ({e})", "クラスタ (k3d) の稼働と port-forward を確かめる")
    # 8. store/secret.key (このリポジトリ配置で動かす場合)
    key_path = (Path(os.environ.get("HAN_SOLO_DATA_DIR", str(Path.home()/".local/share/hansolo")))/"secrets"/"secret_key"
                if context else Path(__file__).resolve().parent / "store" / "secret.key")
    add("secret.key", key_path.is_file(), str(key_path) + ("" if key_path.is_file() else " が無い"),
        "store/secret.key はコピー元から持ってくる。再生成すると金庫が開かずログイン署名も通らない")

    ok_all = all(c["ok"] for c in checks)
    if _want_json(args):
        _print_json({"ok": ok_all, "checks": checks})
    else:
        for c in checks:
            mark = "✅" if c["ok"] else "❌"
            print(f"{mark} {c['check']}: {c['detail']}")
            if not c["ok"] and c["fix"]:
                print(f"   直し方: {c['fix']}")
        print("doctor:", "全て揃っている" if ok_all else "足りないものが上に ❌ で出ている")
    return 0 if ok_all else 1


# ─── argparse 構築 ──────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    # グローバル引数 (コマンドの前後どちらにも置けるよう parents で全段に配る)。
    # default=SUPPRESS により、内側サブパーサの既定値が外側で指定された値を上書きしない。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="機械可読の JSON を1本だけ出力")
    common.add_argument("--url", default=argparse.SUPPRESS,
                        help="CYNOVELA_URL より優先する接続先")
    common.add_argument("--token", default=argparse.SUPPRESS,
                        help="CYNOVELA_TOKEN より優先する Bearer トークン")

    # DD-CYN-0143: --yes (危険な操作の明示同意) を要する命令に配る親パーサ
    yes_p = argparse.ArgumentParser(add_help=False)
    yes_p.add_argument("--yes", action="store_true", default=argparse.SUPPRESS,
                       help="危険な操作の明示同意 (無ければ実行しない)")

    p = argparse.ArgumentParser(
        prog="cynovela_cli",
        # DD-CYN-0143 P-4: 実装の既定 (127.0.0.1:8765) と説明の食い違いを直した。
        # クラスタ外からは k3d 入口 (例 http://localhost:18890) を --url か CYNOVELA_URL で渡す。
        description="Cynovela CLI クライアント。API サーバ (既定 http://127.0.0.1:8765。クラスタ外からは --url http://localhost:18890 等) を端末から操作する。",
        epilog=(
            "接続先とトークン: --url / --token か、環境変数 CYNOVELA_URL / CYNOVELA_TOKEN で渡す。\n"
            "トークンはログイン API の access_token (JWT) か、APIキー (cyn_...) のどちらでもよい。\n"
            "例:\n"
            "  TOKEN=$(curl -s -X POST $URL/api/auth/login -H 'Content-Type: application/json' \\\n"
            "    -d '{\"username\":\"demo\",\"password\":\"...\"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"access_token\"])')\n"
            "  python3 cynovela_cli.py --url $URL --token $TOKEN chat --workspace ws-xxxx --query '議事録の要点は?'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "status",
        help="サーバー疎通確認 (/api/health)",
        description="サーバーに届くかを確かめ、版と状態を表示する。トークンは不要。",
        parents=[common],
    )
    sub.add_parser(
        "health",
        help="ヘルスチェック (/api/health)",
        description="status と同じ /api/health を叩く別名。監視スクリプトからは --json をつけて使う。",
        parents=[common],
    )

    p_search = sub.add_parser(
        "search",
        help="RAG 検索 (回答は作らず出典 chunk を返す)",
        description="索引に対して検索だけを行い、当たった chunk (出典) を返す。回答文が欲しいときは chat を使う。"
        "例: python3 cynovela_cli.py search --workspace ws-xxxx --query '経費精算 締切'",
        parents=[common],
    )
    p_search.add_argument("--workspace", required=True, help="workspace_id (workspaces list で確認)")
    p_search.add_argument("--query", required=True, help="検索クエリ")
    p_search.add_argument("--n-results", dest="n_results", default=5, help="取得件数 (既定 5)")

    p_ws = sub.add_parser(
        "workspaces",
        help="ワークスペースの一覧・作成",
        description="ワークスペース (資料の置き場の単位) を一覧・作成する。作成は admin のトークンが必要。",
        parents=[common],
    )
    p_ws_sub = p_ws.add_subparsers(dest="ws_cmd", required=True)
    p_ws_sub.add_parser(
        "list",
        help="ワークスペース一覧",
        description="自分に見えるワークスペースの id と名前を一覧する。以降のコマンドの workspace_id はここで調べる。",
        parents=[common],
    )
    p_ws_reg = p_ws_sub.add_parser(
        "register",
        help="ワークスペース作成 (admin 限定)",
        description="ワークスペースを新規作成する (admin 限定)。例: workspaces register --name 営業部資料",
        parents=[common],
    )
    p_ws_reg.add_argument("--name", required=True, help="ワークスペース名")
    p_ws_reg.add_argument("--description", default="", help="説明 (任意)")
    # DD-CYN-0143: 管理系サブコマンドを足す
    p_ws_up = p_ws_sub.add_parser("update", help="名前・説明・メンバーの更新", parents=[common])
    p_ws_up.add_argument("--id", required=True)
    p_ws_up.add_argument("--name")
    p_ws_up.add_argument("--description")
    # N-4: メンバーの追加/除去 (username か user_id。複数指定可)
    p_ws_up.add_argument("--add-user", dest="add_user", action="append",
                         help="メンバー追加 (username か user_id。繰り返し指定可)")
    p_ws_up.add_argument("--remove-user", dest="remove_user", action="append",
                         help="メンバー除去 (username か user_id。繰り返し指定可。存在しない対象は警告のみ)")
    for _n, _h in (("archive", "保管庫へ移す"), ("unarchive", "保管庫から戻す"), ("export", "内容の書き出し")):
        _pp = p_ws_sub.add_parser(_n, help=_h, parents=[common])
        _pp.add_argument("--id", required=True)
    p_ws_del = p_ws_sub.add_parser("delete", help="削除 (--yes 必須)", parents=[common, yes_p])
    p_ws_del.add_argument("--id", required=True)

    p_col = sub.add_parser(
        "collections",
        help="コレクションの一覧・作成",
        description="コレクション (検索の単位。ワークスペース内の資料のまとまり) を一覧・作成する。作成は admin 限定。",
        parents=[common],
    )
    p_col_sub = p_col.add_subparsers(dest="col_cmd", required=True)
    p_col_list = p_col_sub.add_parser(
        "list",
        help="コレクション一覧",
        description="自分に見えるコレクションの id・名前・状態・chunk 数を一覧する。",
        parents=[common],
    )
    p_col_list.add_argument("--workspace", help="絞り込み workspace_id (任意)")
    p_col_create = p_col_sub.add_parser(
        "create",
        help="コレクション作成 (admin 限定)",
        description="コレクションを新規作成する (admin 限定)。作成後は publish で索引化して初めて検索できる。",
        parents=[common],
    )
    p_col_create.add_argument("--name", required=True, help="コレクション名")
    p_col_create.add_argument("--workspace", required=True, help="workspace_id")
    # DD-CYN-0143: 管理系サブコマンドを足す
    p_col_up = p_col_sub.add_parser("update", help="更新 (--body に JSON)", parents=[common])
    p_col_up.add_argument("--id", required=True)
    p_col_up.add_argument("--body", required=True, help='更新内容の JSON (例 \'{"name":"新名"}\')')
    for _n, _h in (("archive", "保管庫へ移す"), ("unarchive", "保管庫から戻す")):
        _pp = p_col_sub.add_parser(_n, help=_h, parents=[common])
        _pp.add_argument("--id", required=True)
    p_col_del = p_col_sub.add_parser(
        "delete", help="削除 (--yes 必須。結ばれた資料も連鎖削除)", parents=[common, yes_p]
    )
    p_col_del.add_argument("--id", required=True)
    # N-5: 状態を1画面に束ねる
    p_col_st = p_col_sub.add_parser(
        "status",
        help="状態・chunk数・伏字件数・直近ジョブを1画面で",
        description="GET /api/collections/{id}・publish-summary (admin 限定)・直近 publish ジョブを束ねて表示する。",
        parents=[common],
    )
    p_col_st.add_argument("--id", required=True)

    p_scan = sub.add_parser(
        "scan",
        help="データソースの走査 (start/status/cancel。旧形式 scan --source も可)",
        description="登録済みデータソース (取り込み元フォルダ) を走査して新しいファイルを拾う (admin 限定)。"
        "start は即座に返り (非同期)、status で進み具合、cancel で中止。"
        "旧形式 scan --source SRC_ID は同期ポーリング (120s) で残している。",
        parents=[common],
    )
    p_scan.add_argument("--source", help="(旧形式) ソース ID を渡すと同期ポーリングで走査")
    p_scan_sub = p_scan.add_subparsers(dest="scan_cmd", required=False)
    p_scan_start = p_scan_sub.add_parser("start", help="走査を開始 (非同期・即返し)", parents=[common])
    p_scan_start.add_argument("--source", required=True)
    p_scan_start.add_argument("--wait", action="store_true", help="完了までポーリングする (最大 300s)")
    p_scan_status = p_scan_sub.add_parser("status", help="走査の進み具合", parents=[common])
    p_scan_status.add_argument("--source", required=True)
    p_scan_cancel = p_scan_sub.add_parser("cancel", help="走査の中止", parents=[common])
    p_scan_cancel.add_argument("--source", required=True)

    p_ing = sub.add_parser(
        "ingest-status",
        help="取り込み状態の確認 (sources / jobs)",
        description="データソースの一覧と状態、または取り込みジョブの進行を表示する。"
        "引数なしでソース一覧、--source で1件の詳細、--job でジョブの進行。",
        parents=[common],
    )
    p_ing.add_argument("--source", help="ソース ID (GET /api/sources/{id})")
    p_ing.add_argument("--job", help="ジョブ ID (GET /api/jobs/{id})")

    p_idx = sub.add_parser(
        "index-status",
        help="索引の件数を確認",
        description="コレクションごとの chunk 数を集計して索引の状態を表示する。0 件なら検索は何も返さない "
        "(publish を忘れていないか確かめる)。",
        parents=[common],
    )
    p_idx.add_argument("--workspace", help="絞り込み workspace_id (任意)")

    p_pub = sub.add_parser(
        "publish",
        help="コレクションの索引化 (start/status/stop/recover/diff/history。旧形式 publish --collection も可)",
        description="コレクションに結ばれたファイルを取り込み・マスキング・索引化して検索可能にする (admin 限定)。"
        "start は非同期で job_id を即返す (再公開は --yes 必須)。進行は publish status --job で追う。"
        "旧形式 publish --collection COL_ID は同期実行 (600s) で残している。",
        parents=[common],
    )
    p_pub.add_argument("--collection", help="(旧形式) コレクション ID を渡すと同期実行")
    p_pub_sub = p_pub.add_subparsers(dest="publish_cmd", required=False)
    p_pub_start = p_pub_sub.add_parser("start", help="公開を開始 (非同期・job_id 即返し。再公開は --yes)", parents=[common, yes_p])
    p_pub_start.add_argument("--collection", required=True)
    p_pub_start.add_argument("--wait", action="store_true", help="完了までポーリングする (最大 3600s)")
    p_pub_status = p_pub_sub.add_parser("status", help="公開ジョブの進み具合 (指定なしで直近一覧)", parents=[common])
    p_pub_status.add_argument("--job", help="ジョブ ID")
    p_pub_status.add_argument("--collection", help="このコレクションのジョブに絞る")
    p_pub_stop = p_pub_sub.add_parser("stop", help="進行中の公開を止める", parents=[common])
    p_pub_stop.add_argument("--collection", required=True)
    p_pub_recover = p_pub_sub.add_parser("recover", help="publishing のまま固まった状態の作り直し (--yes 必須)", parents=[common, yes_p])
    p_pub_recover.add_argument("--collection", required=True)
    p_pub_diff = p_pub_sub.add_parser("diff", help="前回公開との差分", parents=[common])
    p_pub_diff.add_argument("--collection", required=True)
    p_pub_hist = p_pub_sub.add_parser("history", help="公開の履歴 (workspace 単位)", parents=[common])
    p_pub_hist.add_argument("--workspace", required=True)

    p_key = sub.add_parser(
        "key",
        help="APIキー (cyn_...) の発行・一覧・失効",
        description="用途を限定した APIキー (cyn_...) を扱う。発行と失効は admin のトークンが必要。"
        "発行時の平文キーは応答に1回だけ表示され、以後は再表示できない。",
        parents=[common],
    )
    p_key_sub = p_key.add_subparsers(dest="key_cmd", required=True)
    p_key_sub.add_parser(
        "list",
        help="鍵一覧 (GET /api/keys)",
        description="発行済みの APIキー一覧を表示する。admin は全件、その他は自分の鍵のみ。平文は表示されない。",
        parents=[common],
    )
    p_key_issue = p_key_sub.add_parser(
        "issue",
        help="鍵発行 (POST /api/keys・admin 限定)",
        description="APIキーを発行する (admin 限定)。応答の key 欄が平文キーで、表示はこの1回だけ。"
        "例: python3 cynovela_cli.py key issue --name goose用 --role viewer --scope-workspace ws-xxxx",
        parents=[common],
    )
    p_key_issue.add_argument("--name", default="", help="鍵の表示名 (何に使う鍵かを書く)")
    p_key_issue.add_argument("--role", default="viewer", choices=["viewer", "admin"],
                             help="鍵の役割 (既定 viewer。発行者より高くはできない)")
    p_key_issue.add_argument("--scope-workspace", dest="scope_workspace",
                             help="この workspace だけに絞る (省略で全域)")
    p_key_issue.add_argument("--scope-collection", dest="scope_collection",
                             help="この collection だけに絞る (省略で全域)")
    p_key_revoke = p_key_sub.add_parser(
        "revoke",
        help="鍵失効 (DELETE /api/keys/{id}・--yes 必須)",
        description="APIキーを失効する (--yes 必須)。行は監査のため残る。admin は任意の鍵、その他は自分の鍵のみ。",
        parents=[common, yes_p],
    )
    p_key_revoke.add_argument("--id", required=True, help="鍵 ID (key list で確認)")

    p_chat = sub.add_parser(
        "chat",
        help="資料に基づく質問応答 (回答+出典)",
        description="索引を検索し、LLM が資料に基づく回答を出典つきで返す。viewer のトークンでは本文は伏字後 "
        "(masked) で扱われる。例: chat --workspace ws-xxxx --query '7月の議事録の決定事項は?'",
        parents=[common],
    )
    p_chat.add_argument("--workspace", required=True, help="workspace_id (workspaces list で確認)")
    p_chat.add_argument("--query", required=True, help="質問文")
    p_chat.add_argument("--mode", choices=["lite", "standard", "hq"], default=None,
                        help="RAG プリセット (lite=速い / standard=既定 / hq=精度優先)")
    p_chat.add_argument("--role", help="role_override (admin/viewer 等)")
    p_chat.add_argument("--session", help="session_id (同じ値を渡すと会話が継続する)")
    p_chat.add_argument("--temperature", default=0.1, help="LLM temperature (既定 0.1)")
    p_chat.add_argument("--no-sources", action="store_true", help="出典一覧を表示しない")

    # ─── DD-CYN-0143 ステップ2 (N-2/N-3): login/logout・jobs ───
    p_login = sub.add_parser(
        "login",
        help="ログインしてトークンを ~/.cynovela_cli.env へ保存",
        description="POST /api/auth/login でトークンを取得し ~/.cynovela_cli.env に保存する (0600)。"
        "パスワードは対話で聞く。トークン値そのものは表示しない。",
        parents=[common],
    )
    p_login.add_argument("--username", required=True, help="利用者名")
    p_login.add_argument("--password", help="パスワード (シェル履歴に残るため非推奨。省略で対話入力)")
    p_login.add_argument("--ttl-hours", dest="ttl_hours",
                         help="トークンの有効時間 (時間。サーバ側の上限に丸められる)")
    sub.add_parser(
        "logout",
        help="ログアウトして保存済みトークンを消す",
        description="POST /api/auth/logout を呼び、~/.cynovela_cli.env の CYNOVELA_TOKEN 行を消す。",
        parents=[common],
    )
    p_jobs = sub.add_parser(
        "jobs",
        help="ジョブ横断一覧 (publish + scan 疑似ジョブ)",
        description="GET /api/jobs をジョブの種類 (kind) 横断で一覧する (admin 限定)。"
        "scan は sources 表から合成した疑似ジョブ (id は scan:SOURCE_ID)。",
        parents=[common],
    )
    p_jobs.add_argument("--kind", default="all", choices=["all", "publish", "scan"],
                        help="ジョブの種類 (既定 all)")
    p_jobs.add_argument("--status", help="status で絞る")
    p_jobs.add_argument("--limit", help="件数上限 (既定 50)")

    # ─── DD-CYN-0143 ステップ1 (P-2): 追加命令群 ───
    p_ov = sub.add_parser("overview", help="見る系をまとめて表示 (dashboard/stats/catalog/alerts/storage/feedback/queue)",
                          parents=[common])
    p_ov.add_argument("--section", default="all",
                      choices=["all", "dashboard", "stats", "catalog", "alerts", "storage", "feedback", "queue"])

    p_set = sub.add_parser("settings", help="設定の表示・変更・export/import・モデル一覧・疎通",
                           description="settings show は「いま効いている値と出所の層」を並べる。set/import/chunking の変更は --yes 必須。",
                           parents=[common])
    p_set_sub = p_set.add_subparsers(dest="settings_cmd", required=True)
    p_set_sub.add_parser("show", help="いま効いている値と出所の層", parents=[common])
    p_set_set = p_set_sub.add_parser("set", help="1 キーの変更 (--yes 必須)", parents=[common, yes_p])
    p_set_set.add_argument("--key", required=True)
    p_set_set.add_argument("--value", required=True)
    p_set_ck = p_set_sub.add_parser("chunking", help="chunk_size/overlap の表示・変更 (変更は --yes)", parents=[common, yes_p])
    p_set_ck.add_argument("--size", help="chunk_size (数値 か unset)")
    p_set_ck.add_argument("--overlap", help="chunk_overlap (数値 か unset)")
    p_set_exp = p_set_sub.add_parser("export", help="全設定の書き出し", parents=[common])
    p_set_exp.add_argument("--out", help="書き出し先ファイル (省略で標準出力)")
    p_set_imp = p_set_sub.add_parser("import", help="全設定の読み込み (--yes 必須)", parents=[common, yes_p])
    p_set_imp.add_argument("--file", required=True, help="settings export の JSON ファイル")
    p_set_sub.add_parser("models", help="LLM モデル一覧", parents=[common])
    p_set_sub.add_parser("test-connection", help="LLM 疎通確認", parents=[common])

    p_ir = sub.add_parser("ingest-roots", help="取り込み元 (ホスト側フォルダ) の一覧・追加・削除", parents=[common])
    p_ir_sub = p_ir.add_subparsers(dest="roots_cmd", required=True)
    p_ir_sub.add_parser("list", help="一覧", parents=[common])
    p_ir_add = p_ir_sub.add_parser("add", help="追加", parents=[common])
    p_ir_add.add_argument("--path", required=True, help="ホスト側の絶対パス")
    p_ir_add.add_argument("--name", help="名前 (省略可)")
    p_ir_add.add_argument("--label", help="表示ラベル (省略可)")
    p_ir_rm = p_ir_sub.add_parser("remove", help="削除 (--yes 必須)", parents=[common, yes_p])
    p_ir_rm.add_argument("--name", required=True)

    p_src = sub.add_parser("sources", help="データソースの一覧・詳細・追加・削除・ファイル一覧", parents=[common])
    p_src_sub = p_src.add_subparsers(dest="sources_cmd", required=True)
    p_src_list = p_src_sub.add_parser("list", help="一覧", parents=[common])
    p_src_list.add_argument("--workspace", help="絞り込み workspace_id")
    p_src_show = p_src_sub.add_parser("show", help="1 件の詳細 (走査の進み具合もここ)", parents=[common])
    p_src_show.add_argument("--id", required=True)
    p_src_add = p_src_sub.add_parser("add", help="追加", parents=[common])
    p_src_add.add_argument("--workspace", required=True)
    p_src_add.add_argument("--path", required=True)
    p_src_add.add_argument("--name")
    p_src_rm = p_src_sub.add_parser("remove", help="削除 (--yes 必須)", parents=[common, yes_p])
    p_src_rm.add_argument("--id", required=True)
    p_src_files = p_src_sub.add_parser("files", help="ソース内ファイル一覧", parents=[common])
    p_src_files.add_argument("--id", required=True)

    p_lf = sub.add_parser("link-files", help="走査済みファイルをコレクションへ結線 (省略で未結線を全部)", parents=[common])
    p_lf.add_argument("--collection", required=True)
    p_lf.add_argument("--file-ids", dest="file_ids", help="カンマ区切りの file id (省略で unlinked 全部)")

    p_usr = sub.add_parser("users", help="利用者の一覧・作成・更新・削除・パスワード再設定 (admin)", parents=[common])
    p_usr_sub = p_usr.add_subparsers(dest="users_cmd", required=True)
    p_usr_sub.add_parser("list", help="一覧", parents=[common])
    p_usr_cr = p_usr_sub.add_parser("create", help="作成", parents=[common])
    p_usr_cr.add_argument("--username", required=True)
    p_usr_cr.add_argument("--password", required=True)
    p_usr_cr.add_argument("--role", default="viewer", choices=["viewer", "admin"])
    p_usr_cr.add_argument("--name")
    p_usr_up = p_usr_sub.add_parser("update", help="更新", parents=[common])
    p_usr_up.add_argument("--id", required=True)
    p_usr_up.add_argument("--role", choices=["viewer", "admin"])
    p_usr_up.add_argument("--name")
    p_usr_up.add_argument("--active", choices=["0", "1"])
    p_usr_del = p_usr_sub.add_parser("delete", help="削除 (--yes 必須。--purge で完全削除)", parents=[common, yes_p])
    p_usr_del.add_argument("--id", required=True)
    p_usr_del.add_argument("--purge", action="store_true",
                           help="完全削除 (物理削除・元に戻せない。監査ログの行は残る)")
    p_usr_rp = p_usr_sub.add_parser("reset-password", help="パスワード再設定 (--yes 必須)", parents=[common, yes_p])
    p_usr_rp.add_argument("--id", required=True)
    p_usr_rp.add_argument("--password", required=True)

    p_bk = sub.add_parser("backup", help="控えの作成・一覧・復元・削除・確認 (人が打つ命令。自動実行は仕込まない)",
                          parents=[common])
    p_bk_sub = p_bk.add_subparsers(dest="backup_cmd", required=True)
    p_bk_cr = p_bk_sub.add_parser("create", help="作成", parents=[common])
    p_bk_cr.add_argument("--label", help="控えに付ける印")
    p_bk_sub.add_parser("list", help="一覧", parents=[common])
    p_bk_rs = p_bk_sub.add_parser("restore", help="復元 (--yes 必須。現行 DB を置き換える)", parents=[common, yes_p])
    p_bk_rs.add_argument("--name", required=True)
    p_bk_del = p_bk_sub.add_parser("delete", help="削除 (--yes 必須)", parents=[common, yes_p])
    p_bk_del.add_argument("--name", required=True)
    p_bk_vf = p_bk_sub.add_parser("verify", help="存在とメタの確認", parents=[common])
    p_bk_vf.add_argument("--name", required=True)

    p_cl = sub.add_parser("cleanup", help="片づけ (保管庫の一覧/戻し/完全削除・vacuum・孤児掃除)", parents=[common])
    p_cl_sub = p_cl.add_subparsers(dest="cleanup_cmd", required=True)
    p_cl_sub.add_parser("archived-list", help="保管庫の一覧", parents=[common])
    p_cl_rs = p_cl_sub.add_parser("restore", help="保管庫から戻す", parents=[common])
    p_cl_rs.add_argument("--kind", required=True, help="workspace / collection")
    p_cl_rs.add_argument("--id", required=True)
    p_cl_pg = p_cl_sub.add_parser("purge", help="完全削除 (--yes 必須)", parents=[common, yes_p])
    p_cl_pg.add_argument("--kind", required=True)
    p_cl_pg.add_argument("--id", required=True)
    p_cl_sub.add_parser("vacuum", help="DB 圧縮 (--yes 必須)", parents=[common, yes_p])
    p_cl_sub.add_parser("orphans", help="chromadb 孤児掃除 (--yes 必須)", parents=[common, yes_p])

    p_au = sub.add_parser("audit", help="監査ログ・書き出し・変更履歴・準拠レポート", parents=[common])
    p_au_sub = p_au.add_subparsers(dest="audit_cmd", required=True)
    p_au_logs = p_au_sub.add_parser("logs", help="監査ログ一覧", parents=[common])
    p_au_logs.add_argument("--workspace")
    p_au_logs.add_argument("--limit")
    p_au_exp = p_au_sub.add_parser("export", help="監査ログの書き出し", parents=[common])
    p_au_exp.add_argument("--out", help="書き出し先ファイル (省略で標準出力)")
    p_au_sub.add_parser("change-log", help="管理変更履歴", parents=[common])
    p_au_sub.add_parser("compliance", help="準拠チェックリストとレポート", parents=[common])

    p_ss = sub.add_parser("sessions", help="チャットセッションの一覧・詳細・削除", parents=[common])
    p_ss_sub = p_ss.add_subparsers(dest="sessions_cmd", required=True)
    p_ss_sub.add_parser("list", help="一覧", parents=[common])
    p_ss_show = p_ss_sub.add_parser("show", help="1 件 (メッセージ含む)", parents=[common])
    p_ss_show.add_argument("--id", required=True)
    p_ss_del = p_ss_sub.add_parser("delete", help="削除 (--yes 必須)", parents=[common, yes_p])
    p_ss_del.add_argument("--id", required=True)

    p_pol = sub.add_parser("policy", help="保護ポリシーの一覧・作成・更新・削除・マトリクス・PII 検知", parents=[common])
    p_pol_sub = p_pol.add_subparsers(dest="policy_cmd", required=True)
    p_pol_sub.add_parser("list", help="一覧", parents=[common])
    p_pol_cr = p_pol_sub.add_parser("create", help="作成 (--body に JSON)", parents=[common])
    p_pol_cr.add_argument("--body", required=True)
    p_pol_up = p_pol_sub.add_parser("update", help="更新 (--body に JSON)", parents=[common])
    p_pol_up.add_argument("--id", required=True)
    p_pol_up.add_argument("--body", required=True)
    p_pol_del = p_pol_sub.add_parser("delete", help="削除 (--yes 必須)", parents=[common, yes_p])
    p_pol_del.add_argument("--id", required=True)
    p_pol_sub.add_parser("matrix", help="ポリシーマトリクス", parents=[common])
    p_pol_sub.add_parser("pii", help="PII 検知の集計", parents=[common])

    p_mcp = sub.add_parser("mcp", help="MCP の設定・指紋・疎通 (読み取り)", parents=[common])
    p_mcp_sub = p_mcp.add_subparsers(dest="mcp_cmd", required=True)
    p_mcp_sub.add_parser("config", help="MCP 設定", parents=[common])
    p_mcp_sub.add_parser("fingerprint", help="道具一覧の指紋 (変化検知)", parents=[common])
    p_mcp_sub.add_parser("test", help="疎通確認", parents=[common])

    p_rep = sub.add_parser("report", help="報告書の一覧・表示・生成", parents=[common])
    p_rep_sub = p_rep.add_subparsers(dest="report_cmd", required=True)
    p_rep_sub.add_parser("list", help="一覧", parents=[common])
    p_rep_show = p_rep_sub.add_parser("show", help="1 件", parents=[common])
    p_rep_show.add_argument("--id", required=True)
    p_rep_sub.add_parser("generate", help="生成 (時間がかかる)", parents=[common])

    p_rst = sub.add_parser("restart", help="Pod の再起動 (--yes 必須。restart_required の設定を反映させる)",
                           description="受けた Pod を落として K8s に立ち直らせる。複数レプリカでは --times をレプリカ数以上にする。",
                           parents=[common, yes_p])
    p_rst.add_argument("--times", default=1, help="繰り返し回数 (既定 1)")
    p_rst.add_argument("--interval", default=5, help="繰り返しの間隔秒 (既定 5)")

    p_doc = sub.add_parser("doctor", help="この機材で足りないものを名指しする (podman/k3d/DOCKER_HOST/LLM/容量/口/鍵)",
                           description="サーバでなく手元の機材を検査する。足りないものは直し方つきで名指しされる。"
                           "--remote はホスト検査を飛ばし、サーバ側の口 (health/detailed/ready/queue/mcp) を検査する。",
                           parents=[common])
    p_doc.add_argument("--remote", action="store_true",
                       help="サーバ側の口を検査 (ホスト側の podman/k3d/容量などの検査は飛ばす)")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # --url / --token を最優先で適用 (指定時のみ)。
    global BASE_URL, TOKEN
    url = getattr(args, "url", None)
    token = getattr(args, "token", None)
    if url:
        BASE_URL = url.rstrip("/")
    if token:
        TOKEN = token

    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "health":
        return cmd_health(args)
    if args.cmd == "search":
        return cmd_search(args)
    if args.cmd == "workspaces":
        if args.ws_cmd == "list":
            return cmd_workspaces_list(args)
        if args.ws_cmd == "register":
            return cmd_workspaces_register(args)
        return cmd_workspaces_manage(args)
    if args.cmd == "collections":
        if args.col_cmd == "list":
            return cmd_collections_list(args)
        if args.col_cmd == "create":
            return cmd_collections_create(args)
        if args.col_cmd == "status":
            return cmd_collections_status(args)
        return cmd_collections_manage(args)
    if args.cmd == "scan":
        if getattr(args, "scan_cmd", None):
            return cmd_scan2(args)
        if not getattr(args, "source", None):
            print("scan start --source SRC_ID (または旧形式 scan --source SRC_ID)", file=sys.stderr)
            return 2
        return cmd_scan(args)
    if args.cmd == "ingest-status":
        return cmd_ingest_status(args)
    if args.cmd == "index-status":
        return cmd_index_status(args)
    if args.cmd == "publish":
        if getattr(args, "publish_cmd", None):
            return cmd_publish2(args)
        if not getattr(args, "collection", None):
            print("publish start --collection COL_ID (または旧形式 publish --collection COL_ID)", file=sys.stderr)
            return 2
        return cmd_publish(args)
    if args.cmd == "key":
        if args.key_cmd == "revoke":
            r = _require_yes(args, f"key revoke ({args.id} = 鍵の失効)")
            if r is not None:
                return r
        return cmd_key(args)
    if args.cmd == "chat":
        return cmd_chat(args)
    # ─── DD-CYN-0143 ステップ2 (N-2/N-3) の追加命令 ───
    if args.cmd == "login":
        return cmd_login(args)
    if args.cmd == "logout":
        return cmd_logout(args)
    if args.cmd == "jobs":
        return cmd_jobs(args)
    # ─── DD-CYN-0143 ステップ1 (P-2) の追加命令 ───
    if args.cmd == "overview":
        return cmd_overview(args)
    if args.cmd == "settings":
        return cmd_settings(args)
    if args.cmd == "ingest-roots":
        return cmd_ingest_roots(args)
    if args.cmd == "sources":
        return cmd_sources(args)
    if args.cmd == "link-files":
        return cmd_link_files(args)
    if args.cmd == "users":
        return cmd_users(args)
    if args.cmd == "backup":
        return cmd_backup(args)
    if args.cmd == "cleanup":
        return cmd_cleanup(args)
    if args.cmd == "audit":
        return cmd_audit(args)
    if args.cmd == "sessions":
        return cmd_sessions(args)
    if args.cmd == "policy":
        return cmd_policy(args)
    if args.cmd == "mcp":
        return cmd_mcp(args)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "restart":
        return cmd_restart(args)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    print(f"未知のコマンド: {args.cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

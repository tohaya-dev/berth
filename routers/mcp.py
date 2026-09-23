"""MCP config / test endpoints (/api/mcp/*)."""

from __future__ import annotations

import os
import sys

from fastapi import APIRouter, Request

from core.auth import _require_admin

router = APIRouter(tags=["mcp"])


@router.get("/api/mcp/config", response_model=None)
def mcp_config(request: Request):
    """P6-A: claude_desktop_config.json 用のスニペットを生成する。"""
    _require_admin(request)
    # mcp_server.py の相対パスは server.py と同じディレクトリ
    import server as _server

    repo_root = os.path.abspath(os.path.dirname(_server.__file__))
    py_path = os.environ.get("CYNOVELA_MCP_PYTHON") or sys.executable or "python"
    server_path = os.path.join(repo_root, "mcp_server.py")
    # fixall-B5 20260602: ポート 8765 のリテラル直書きを撤去。server.py が起動時に公開する
    # CYNOVELA_PORT (= --port) を最優先、無ければ現リクエストの port、最後に既定 8765 を使う。
    _port = os.environ.get("CYNOVELA_PORT") or (request.url.port if request.url.port else None) or 8765
    snippet = {
        "mcpServers": {
            "cynovela": {
                "command": py_path,
                "args": [server_path, "--cynovela-url", f"http://127.0.0.1:{_port}"],
            }
        }
    }
    # sweep-fix-d-20260711: 静的2要素配列を撤去し mcp_server.py の TOOLS 実ツール名を動的取得。
    #   件数表示(フロント mc.tools.length)が実装済みツール数(11)と一致するようにする。
    #   ※ import mcp_server は不可: 同モジュールは import 時に module-level argparse を実行し
    #     サーバの argv で sys.exit(2) して落ちる。副作用ゼロの ast 解析で TOOLS リテラルを読む。
    _tool_names: list = []
    try:
        import ast

        with open(server_path, "r", encoding="utf-8") as _mf:
            _tree = ast.parse(_mf.read())
        for _node in _tree.body:
            if isinstance(_node, ast.Assign) and any(
                isinstance(_t, ast.Name) and _t.id == "TOOLS" for _t in _node.targets
            ):
                if isinstance(_node.value, ast.List):
                    for _el in _node.value.elts:
                        if isinstance(_el, ast.Dict):
                            for _k, _v in zip(_el.keys, _el.values):
                                if (
                                    isinstance(_k, ast.Constant)
                                    and _k.value == "name"
                                    and isinstance(_v, ast.Constant)
                                ):
                                    _tool_names.append(_v.value)
                break
    except Exception:
        _tool_names = []
    return {
        "snippet": snippet,
        "tools": _tool_names,
        # DD-CYN-0132 Phase 6: stdio に加え HTTP 待受 (/api/mcp/rpc) を追加した。
        #   HTTP は Service DNS の後ろで全レプリカが同じ口を出せ、認可を MCP 層で持つ
        #   (用途限定の鍵 cyn_... か JWT)。stdio 口は従来どおり残す。
        "transports": ["stdio", "http"],
        "http_endpoint": "/api/mcp/rpc",
        "http_fingerprint_endpoint": "/api/mcp/fingerprint",
        "config_file_hint": "macOS: ~/Library/Application Support/Claude/claude_desktop_config.json",
    }


@router.get("/api/mcp/test-connection", response_model=None)
def mcp_test_connection(request: Request):
    """P6-A: ローカルMCPの基本疎通テスト。"""
    _require_admin(request)
    import server as _server

    repo_root = os.path.abspath(os.path.dirname(_server.__file__))
    server_path = os.path.join(repo_root, "mcp_server.py")
    checks = []
    checks.append(
        {
            "name": "mcp_server.py が存在する",
            "ok": os.path.isfile(server_path),
            "detail": server_path,
        }
    )
    try:
        import mcp  # noqa: F401

        checks.append({"name": "mcp パッケージがインポート可能", "ok": True, "detail": ""})
    except Exception as e:
        checks.append({"name": "mcp パッケージがインポート可能", "ok": False, "detail": type(e).__name__})
    try:
        import httpx  # noqa: F401

        checks.append({"name": "httpx パッケージがインポート可能", "ok": True, "detail": ""})
    except Exception as e:
        checks.append({"name": "httpx パッケージがインポート可能", "ok": False, "detail": type(e).__name__})
    checks.append(
        {
            "name": "Cynovela本体 (このサーバー) が応答している",
            "ok": True,
            "detail": "現在のリクエストが届いていることが応答の証左",
        }
    )
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks)}

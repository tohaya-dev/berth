"""Demo data endpoints (/api/demo/*)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from db import get_db

import state as _state

router = APIRouter(tags=["demo"])


@router.get("/api/demo/role-switch", response_model=None)
def get_role_switch_demo(request: Request):
    """BLOCK B-2: ロール切替デモの workspace_id と利用可能ロールを返す。
    --demo 起動時のみ available=True を返す。それ以外は常に False。

    Stage R8-fix: 認証必須。
    """
    from core.auth import _require_authenticated
    from core.constants import ROLE_DEMO_WS_NAME

    _require_authenticated(request)

    if not (_state.config is not None and _state.config.demo):
        return {"available": False}
    conn = get_db()
    try:
        ws = conn.execute(
            "SELECT id, name FROM workspaces WHERE name = ?",
            (ROLE_DEMO_WS_NAME,),
        ).fetchone()
    finally:
        conn.close()
    if not ws:
        return {"available": False}
    return {
        "available": True,
        "workspace_id": ws["id"],
        "workspace_name": ws["name"],
        "roles": [
            {"role": "admin", "label": "Admin (全件閲覧)"},
            {"role": "viewer", "label": "Viewer (Legalのみ閲覧)"},
        ],
    }

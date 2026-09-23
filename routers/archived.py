"""Archived items endpoints (/api/archived/*)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from db import get_db
from core.auth import _require_admin
from core.audit import _log_audit

router = APIRouter(tags=["archived"])


@router.get("/api/archived", response_model=None)
def list_archived(request: Request):
    """アーカイブ済みアイテムをまとめて返す。"""
    _require_admin(request)
    out: dict = {"sources": [], "workspaces": [], "collections": []}
    conn = get_db()
    try:
        for kind_plural in ("sources", "workspaces", "collections"):
            try:
                rows = conn.execute(
                    f"SELECT id, name, archived_at FROM {kind_plural} "
                    f"WHERE archived_at IS NOT NULL ORDER BY archived_at DESC LIMIT 200"
                ).fetchall()
                out[kind_plural] = [dict(r) for r in rows]
            except Exception:
                pass
    finally:
        conn.close()
    return out


@router.post("/api/archived/{kind}/{item_id}/archive", response_model=None)
async def archive_item(request: Request, kind: str, item_id: str):
    """指定アイテムを論理削除（archived_at にタイムスタンプを書き込む）。"""
    from core.constants import _ARCHIVABLE

    _require_admin(request)
    if kind not in _ARCHIVABLE:
        raise HTTPException(400, f"unknown kind {kind}")
    table = _ARCHIVABLE[kind]
    conn = get_db()
    try:
        row = conn.execute(f"SELECT id FROM {table} WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"{kind} not found")
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(f"UPDATE {table} SET archived_at = ? WHERE id = ?", (now, item_id))
        _log_audit(conn, f"{kind}_archived", item_id, "")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "kind": kind, "id": item_id, "archived_at": now}


@router.post("/api/archived/{kind}/{item_id}/restore", response_model=None)
async def restore_item(request: Request, kind: str, item_id: str):
    """アーカイブ済みアイテムを復元する。"""
    from core.constants import _ARCHIVABLE

    _require_admin(request)
    if kind not in _ARCHIVABLE:
        raise HTTPException(400, f"unknown kind {kind}")
    table = _ARCHIVABLE[kind]
    conn = get_db()
    try:
        row = conn.execute(f"SELECT id FROM {table} WHERE id = ?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"{kind} not found")
        conn.execute(f"UPDATE {table} SET archived_at = NULL WHERE id = ?", (item_id,))
        _log_audit(conn, f"{kind}_restored", item_id, "")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "kind": kind, "id": item_id}


@router.delete("/api/archived/{kind}/{item_id}", response_model=None)
async def purge_archived(request: Request, kind: str, item_id: str):
    """アーカイブ済みアイテムを完全削除する。既存DELETE経路に委譲して chunk/Chroma も掃除。"""
    from core.constants import _ARCHIVABLE
    from routers.sources import delete_source
    from routers.workspaces import delete_workspace
    from routers.collections import delete_collection

    _require_admin(request)
    if kind not in _ARCHIVABLE:
        raise HTTPException(400, f"unknown kind {kind}")
    # DD-CYN-0115 H-3: 委譲先の実体は引数の並びが三者三様
    #   delete_source(request, source_id) / delete_workspace(ws_id, request) /
    #   delete_collection(request, col_id)
    # で、workspace と collection は引数不足のまま呼ばれていた (TypeError で 500 になり
    # まとまり・作業場の完全削除が何も消えていなかった)。並びの違いで再発しないよう
    # すべて名前付きで渡す。request は本エンドポイントのものを引き回す (権限検査は
    # 委譲先でも _require_admin が再度走る)。
    if kind == "source":
        return delete_source(request=request, source_id=item_id)
    if kind == "workspace":
        return delete_workspace(ws_id=item_id, request=request)
    return delete_collection(request=request, col_id=item_id)

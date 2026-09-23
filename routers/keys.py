"""DD-CYN-0132 4-2: 用途を限定した鍵 (api_keys) の発行・失効・一覧。

自動化 (CLI / MCP) 用の資格。管理者の合言葉を配らずに、用途を絞った鍵で API を叩けるようにする。

方針:
- 発行できるのは admin のみ (鍵は「誰かの役割で API を叩ける」強い資格のため)。
- 鍵の役割は発行者の役割を上限にクランプする。admin は viewer 鍵も admin 鍵も出せる。
  viewer が発行することはできない (admin 限定なので到達しない)。
- 平文の鍵 (cyn_...) は発行時に 1 回だけ返す。DB には sha256 のみ保存する。
- scope で引ける作業場所 / コレクション / 道具を絞れる。空は「その軸で絞らない」。
- 失効は revoked_at を立てるだけ (行は監査のため残す)。
"""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, HTTPException, Request

from core.api_schema import parse_body_pydantic
from core.auth import _require_admin, _require_authenticated, hash_api_key
from core.audit import _log_audit
from db import get_db, new_id

router = APIRouter(tags=["keys"])

_VALID_KEY_ROLES = {"admin", "viewer"}
_ROLE_RANK = {"viewer": 1, "admin": 2}


def _norm_scope(raw) -> dict:
    """scope を {"workspaces":[...], "collections":[...], "tools":[...]} に正規化する。
    未知の軸は落とす。各値は文字列 id のリスト。"""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for axis in ("workspaces", "collections", "tools"):
        v = raw.get(axis)
        if isinstance(v, list):
            out[axis] = [str(x) for x in v if isinstance(x, (str, int))]
    return out


@router.post("/api/keys", response_model=None, summary="APIキー発行 (admin 限定・平文は1回だけ返る)")
async def issue_key(request: Request):
    """鍵を発行する。admin 限定。平文の鍵は本応答でのみ返す (再表示不可)。"""
    issuer = _require_admin(request)
    body = await parse_body_pydantic(request)

    name = (body.get("name") or "").strip()
    # 鍵の役割: 既定は viewer (最小権限)。発行者役割を上限にクランプする。
    req_role = (body.get("role") or "viewer").strip().lower()
    if req_role not in _VALID_KEY_ROLES:
        raise HTTPException(400, f"role は {sorted(_VALID_KEY_ROLES)} のいずれか")
    if _ROLE_RANK.get(req_role, 0) > _ROLE_RANK.get(issuer.get("role", "viewer"), 0):
        raise HTTPException(403, "発行者より高い役割の鍵は発行できません")

    # scope: 明示指定 scope、または scope_workspace / scope_collection の簡便指定を受ける。
    scope = _norm_scope(body.get("scope") or {})
    if body.get("scope_workspace"):
        scope.setdefault("workspaces", [])
        scope["workspaces"].append(str(body["scope_workspace"]))
    if body.get("scope_collection"):
        scope.setdefault("collections", [])
        scope["collections"].append(str(body["scope_collection"]))

    # 鍵が名乗る利用者: 既定は発行者自身。admin は user_id を明示して別利用者の鍵も出せる。
    key_user_id = (body.get("user_id") or issuer["id"]).strip()

    plaintext = "cyn_" + secrets.token_urlsafe(32)
    key_id = new_id()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO api_keys "
            "(id, name, key_prefix, key_hash, user_id, role, scope_json, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key_id,
                name,
                plaintext[:12],
                hash_api_key(plaintext),
                key_user_id,
                req_role,
                json.dumps(scope, ensure_ascii=False),
                issuer["id"],
            ),
        )
        conn.commit()
        try:
            _log_audit(
                conn,
                "api_key_issued",
                target=key_id,
                detail=json.dumps(
                    {"role": req_role, "scope": scope, "user_id": key_user_id}, ensure_ascii=False
                ),
                user_id=issuer["id"],
                category="security",
            )
        except Exception:
            pass
    finally:
        conn.close()
    return {
        "id": key_id,
        "name": name,
        "role": req_role,
        "scope": scope,
        "user_id": key_user_id,
        # 平文の鍵はここでのみ返す。以後は取得できない。
        "key": plaintext,
        "key_prefix": plaintext[:12],
    }


@router.get("/api/keys", response_model=None, summary="APIキー一覧 (admin は全件・他は自分の鍵)")
def list_keys(request: Request):
    """鍵の一覧。admin は全件、それ以外は自分に紐づく鍵のみ。平文/ハッシュは返さない。"""
    user = _require_authenticated(request)
    conn = get_db()
    try:
        if user.get("role") == "admin":
            rows = conn.execute(
                "SELECT id, name, key_prefix, user_id, role, scope_json, created_at, "
                "created_by, last_used_at, revoked_at FROM api_keys ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, key_prefix, user_id, role, scope_json, created_at, "
                "created_by, last_used_at, revoked_at FROM api_keys "
                "WHERE user_id = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["scope"] = json.loads(d.pop("scope_json") or "{}")
        except Exception:
            d["scope"] = {}
        d["revoked"] = bool(d.get("revoked_at"))
        out.append(d)
    return {"count": len(out), "keys": out}


@router.delete("/api/keys/{key_id}", response_model=None, summary="APIキー失効 (行は監査のため残る)")
def revoke_key(request: Request, key_id: str):
    """鍵を失効する。admin は任意、それ以外は自分の鍵のみ。行は監査のため残す。"""
    user = _require_authenticated(request)
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
        if not row:
            raise HTTPException(404, "鍵が見つかりません")
        row = dict(row)
        if user.get("role") != "admin" and row.get("user_id") != user["id"]:
            raise HTTPException(403, "この鍵を失効する権限がありません")
        if row.get("revoked_at"):
            return {"ok": True, "id": key_id, "already_revoked": True}
        conn.execute(
            "UPDATE api_keys SET revoked_at = datetime('now') WHERE id = ?", (key_id,)
        )
        conn.commit()
        try:
            _log_audit(
                conn,
                "api_key_revoked",
                target=key_id,
                user_id=user["id"],
                category="security",
            )
        except Exception:
            pass
    finally:
        conn.close()
    return {"ok": True, "id": key_id}

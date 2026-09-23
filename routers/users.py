"""User profile endpoint (/api/users/{user_id})."""

from __future__ import annotations

from datetime import datetime

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, Request

from db import get_db
from core.errors import api_error
from core.audit import log_admin_change

router = APIRouter(tags=["users"])


@router.patch("/api/users/{user_id}", response_model=None)
async def update_user_profile(user_id: str, request: Request):
    # FIX-030: admin OR self in-line → _require_admin_or_self helper 統一
    from core.auth import _require_admin_or_self

    user = _require_admin_or_self(request, user_id)
    _is_admin = user.get("role") == "admin"
    body = await parse_body_pydantic(request)
    allowed = {"display_name", "role"}
    updates = {k: v for k, v in (body or {}).items() if k in allowed}
    # role 変更は admin のみ (_is_admin フラグ使用、文字列マッチ排除)
    if "role" in updates and not _is_admin:
        updates.pop("role")
    if not updates:
        return {"status": "no_change"}
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = get_db()
    try:
        before = conn.execute(
            "SELECT display_name, role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not before:
            conn.close()
            raise api_error("NOT_FOUND", "user not found", status=404)
        conn.execute(
            f"UPDATE users SET {set_clause}, updated_at = ? WHERE id = ?",
            list(updates.values()) + [datetime.now().isoformat(timespec="seconds"), user_id],
        )
        conn.commit()
    finally:
        conn.close()
    log_admin_change(user["id"], "user", user_id, "update", dict(before), updates)
    return {"status": "updated", "id": user_id, "updates": updates}

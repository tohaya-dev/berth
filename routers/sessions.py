"""Session endpoints (/api/sessions/*)."""

from __future__ import annotations

from datetime import datetime

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request

from db import get_db, new_id

from core.auth import _require_admin
from core.audit import _log_audit

router = APIRouter(tags=["sessions"])


@router.get("/api/sessions/{session_id}", response_model=None)
def get_session(request: Request, session_id: str):
    """セッションのメッセージ一覧を返す。"""
    _require_admin(request)
    conn = get_db()
    try:
        s = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not s:
            conn.close()
            raise HTTPException(404, "Session not found")
        msgs = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    from vault_enc import dec_raw as _dec_raw
    def _dm(m):
        d = dict(m)
        if d.get("content") is not None:
            d["content"] = _dec_raw(d["content"])
        if d.get("retrieval_json") is not None:
            d["retrieval_json"] = _dec_raw(d["retrieval_json"])
        return d
    return {"session": dict(s), "messages": [_dm(m) for m in msgs]}


@router.get("/api/sessions", response_model=None)
def list_sessions(request: Request, workspace_id: str | None = None, limit: int = 50):
    """セッション一覧。workspace_id 指定で絞り込み。"""
    from core.auth import _require_authenticated

    # E2(a) allinone IDOR是正: 非adminは自分の user_id のセッションのみ返す。admin は監督用途で全件可。
    _user = _require_authenticated(request)
    _uid = _user.get("id") or _user.get("user_id")
    _is_admin = (_user.get("role") == "admin")
    conn = get_db()
    try:
        _clauses, _params = [], []
        if workspace_id:
            _clauses.append("s.workspace_id = ?")
            _params.append(workspace_id)
        if not _is_admin:
            _clauses.append("s.user_id = ?")
            _params.append(_uid)
        _where = ("WHERE " + " AND ".join(_clauses)) if _clauses else ""
        rows = conn.execute(
            f"""SELECT s.*, COUNT(m.id) as message_count
                   FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
                   {_where}
                   GROUP BY s.id
                   ORDER BY s.updated_at DESC LIMIT ?""",
            (*_params, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/sessions", response_model=None)
async def create_session(request: Request):
    """新規セッションを作成する。session_id を返す。

    Stage R5-fix P1 #10: WS メンバーシップ検査追加 (admin は全 WS、その他は workspace_users で許可済 WS のみ)。
    """
    from core.auth import _require_authenticated

    user = _require_authenticated(request)
    body = await parse_body_pydantic(request)
    workspace_id = (body.get("workspace_id") or "").strip()
    title = (body.get("title") or "新しいチャット").strip()
    if not workspace_id:
        raise HTTPException(400, "workspace_id is required")
    user_id = user["id"]
    # Stage R5-fix P1 #10: WS メンバーシップ検査 (admin は全 WS 通過、その他は workspace_users 必須)
    _user_role = user.get("role") or ""
    _is_admin_user = _user_role == "admin"
    if not _is_admin_user:
        _conn = get_db()
        try:
            _row = _conn.execute(
                "SELECT 1 FROM workspace_users WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user_id),
            ).fetchone()
        finally:
            _conn.close()
        if not _row:
            raise HTTPException(403, "WS メンバーシップがありません")
    sid = new_id()
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()
    try:
        ws = conn.execute("SELECT id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if not ws:
            conn.close()
            raise HTTPException(404, "Workspace not found")
        conn.execute(
            """INSERT INTO sessions (id, user_id, workspace_id, system_prompt_id, title, created_at, updated_at)
               VALUES (?, ?, ?, NULL, ?, ?, ?)""",
            (sid, user_id, workspace_id, title, now, now),
        )
        _log_audit(conn, "session_created", sid, f"ws={workspace_id}")
        conn.commit()
    finally:
        conn.close()
    return {
        "id": sid,
        "user_id": user_id,
        "workspace_id": workspace_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
    }


@router.delete("/api/sessions/{session_id}", response_model=None)
def delete_session(request: Request, session_id: str):
    """セッション + 関連メッセージ + RAG参照 を削除する。"""
    _require_admin(request)
    conn = get_db()
    try:
        s = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not s:
            conn.close()
            raise HTTPException(404, "Session not found")
        # message_rag_refs → messages → sessions の順で削除
        conn.execute(
            "DELETE FROM message_rag_refs WHERE message_id IN " "(SELECT id FROM messages WHERE session_id = ?)",
            (session_id,),
        )
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        _log_audit(conn, "session_deleted", session_id, "")
        conn.commit()
    finally:
        conn.close()
    return {"deleted": session_id}


@router.get("/api/sessions/{session_id}/messages", response_model=None)
def get_session_messages(request: Request, session_id: str):
    """セッション内のメッセージ一覧 (created_at 昇順)。

    DD-CYN-0020 U-3: 一覧の口 (GET /api/sessions) は閲覧者にも「自分の分だけ」を返すのに、
    中身を返すこの口だけが無条件に管理者を要求していた。画面の会話記録一覧の「開く」は
    この口を呼ぶため、閲覧者は自分の記録すら開けず 403 になっていた。
    判定を所有権へ変える: 自分のものなら閲覧者も可・他人のものは管理者のみ
    (core.auth.require_session_owner が admin は素通し・非 admin の他人指定は 403)。
    """
    from core.auth import _require_authenticated, require_session_owner

    user = _require_authenticated(request)
    conn = get_db()
    try:
        require_session_owner(user, session_id, conn)
        msgs = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    from vault_enc import dec_raw as _dec_raw
    def _dm(m):
        d = dict(m)
        if d.get("content") is not None:
            d["content"] = _dec_raw(d["content"])
        if d.get("retrieval_json") is not None:
            d["retrieval_json"] = _dec_raw(d["retrieval_json"])
        return d
    return [_dm(m) for m in msgs]

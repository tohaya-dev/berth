"""メッセージ関連エンドポイント。

- /api/messages/{id} (GET): メッセージ詳細 + RAG参照 + フィードバック
- /api/messages/{id}/feedback (POST): 👍 / 👎 フィードバック保存
"""

from __future__ import annotations

from datetime import datetime

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request

from db import get_db
from core.auth import _require_admin
from core.audit import _log_audit


router = APIRouter(tags=["messages"])


@router.post("/api/messages/{message_id}/feedback", response_model=None)
async def save_feedback(message_id: str, request: Request):
    """RAG Chat の回答に 👍 / 👎 のフィードバックを保存する。"""
    _require_admin(request)
    body = await parse_body_pydantic(request)
    rating = body.get("rating")
    comment = body.get("comment", "") or ""
    if rating not in (1, -1):
        raise HTTPException(400, "rating は 1 (👍) または -1 (👎) を指定してください")
    conn = get_db()
    try:
        msg = conn.execute("SELECT id FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not msg:
            conn.close()
            raise HTTPException(404, "Message not found")
        # feedback.id は INTEGER PRIMARY KEY AUTOINCREMENT。
        # SQLite に発番させて lastrowid を返す（hex TEXT を入れると datatype mismatch で 500）。
        cur = conn.execute(
            """INSERT INTO feedback (message_id, rating, comment, created_at)
               VALUES (?, ?, ?, ?)""",
            (message_id, int(rating), comment, datetime.now().isoformat(timespec="seconds")),
        )
        fid = cur.lastrowid
        _log_audit(conn, "feedback_saved", message_id, f"rating={rating}")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": fid}


@router.get("/api/messages/{message_id}", response_model=None)
def get_message(request: Request, message_id: str):
    """メッセージとそのRAG参照を取得する。"""
    _require_admin(request)
    conn = get_db()
    try:
        msg = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not msg:
            conn.close()
            raise HTTPException(404, "Message not found")
        refs = conn.execute(
            "SELECT * FROM message_rag_refs WHERE message_id = ? ORDER BY rank",
            (message_id,),
        ).fetchall()
        fb = conn.execute(
            "SELECT * FROM feedback WHERE message_id = ? ORDER BY created_at DESC",
            (message_id,),
        ).fetchall()
    finally:
        conn.close()
    from vault_enc import dec_raw as _dec_raw
    _md = dict(msg)
    if _md.get("content") is not None:
        _md["content"] = _dec_raw(_md["content"])
    if _md.get("retrieval_json") is not None:
        _md["retrieval_json"] = _dec_raw(_md["retrieval_json"])
    return {
        "message": _md,
        "rag_refs": [dict(r) for r in refs],
        "feedback": [dict(f) for f in fb],
    }

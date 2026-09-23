"""Feedback endpoints (/api/feedback/*)."""

from __future__ import annotations

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request

from db import get_db
from core.auth import _require_admin

router = APIRouter(tags=["feedback"])


@router.post("/api/feedback", response_model=None)
async def post_feedback(request: Request):
    """PHASE F-1: 👍/👎 フィードバックを記録する。"""
    _require_admin(request)
    body = await parse_body_pydantic(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "オブジェクト形式で指定してください")
    rating = int(body.get("rating", 0))
    if rating not in (1, -1):
        raise HTTPException(400, "rating は +1 または -1 を指定してください")
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query は必須です")

    import json as _json

    sources_used = body.get("sources_used")
    sources_str = _json.dumps(sources_used, ensure_ascii=False) if sources_used else None

    c = get_db()
    try:
        qid = body.get("query_id") or ""
        if qid:
            existing = c.execute(
                "SELECT id FROM feedback WHERE query_id = ? ORDER BY id DESC LIMIT 1",
                (qid,),
            ).fetchone()
        else:
            existing = None
        if existing:
            c.execute(
                "UPDATE feedback SET rating=?, answer_preview=?, sources_used=?, "
                "mode=?, collection_id=?, workspace_id=?, response_time_ms=?, "
                "crag_triggered=?, multi_query_count=?, timestamp=datetime('now') "
                "WHERE id=?",
                (
                    rating,
                    body.get("answer_preview") or "",
                    sources_str,
                    body.get("mode") or "",
                    body.get("collection_id") or "",
                    body.get("workspace_id") or "",
                    int(body.get("response_time_ms") or 0),
                    int(bool(body.get("crag_triggered"))),
                    int(body.get("multi_query_count") or 1),
                    existing["id"],
                ),
            )
        else:
            c.execute(
                "INSERT INTO feedback "
                "(query_id, query, answer_preview, sources_used, rating, mode, "
                "collection_id, workspace_id, response_time_ms, "
                "crag_triggered, multi_query_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    qid,
                    query,
                    body.get("answer_preview") or "",
                    sources_str,
                    rating,
                    body.get("mode") or "",
                    body.get("collection_id") or "",
                    body.get("workspace_id") or "",
                    int(body.get("response_time_ms") or 0),
                    int(bool(body.get("crag_triggered"))),
                    int(body.get("multi_query_count") or 1),
                ),
            )
        c.commit()
    finally:
        c.close()
    return {"ok": True, "rating": rating}


@router.get("/api/feedback/stats", response_model=None)
def get_feedback_stats(request: Request):
    """PHASE F-1/F-2: フィードバック集計を返す。"""
    _require_admin(request)
    c = get_db()
    try:
        rows = c.execute("SELECT mode, rating, COUNT(*) AS n FROM feedback GROUP BY mode, rating").fetchall()
        daily = c.execute(
            "SELECT DATE(created_at) AS day, "
            "SUM(CASE WHEN rating=1 THEN 1 ELSE 0 END) AS up, "
            "SUM(CASE WHEN rating=-1 THEN 1 ELSE 0 END) AS down "
            "FROM feedback "
            "WHERE created_at >= datetime('now', '-30 days') "
            "GROUP BY DATE(created_at) ORDER BY day"
        ).fetchall()
    finally:
        c.close()
    total = {"up": 0, "down": 0}
    by_mode: dict = {}
    for r in rows:
        rating = int(r["rating"])
        n = int(r["n"])
        if rating == 1:
            total["up"] += n
        else:
            total["down"] += n
        m = r["mode"] or "unknown"
        if m not in by_mode:
            by_mode[m] = {"up": 0, "down": 0}
        by_mode[m]["up" if rating == 1 else "down"] += n
    return {
        "total": total,
        "by_mode": by_mode,
        "daily_30d": [{"day": r["day"], "up": int(r["up"] or 0), "down": int(r["down"] or 0)} for r in daily],
    }


@router.get("/api/feedback/negatives", response_model=None)
def get_feedback_negatives(request: Request, limit: int = 20, offset: int = 0):
    """PHASE F-2: 👎 (rating=-1) のフィードバック一覧をページネーションで返す。"""
    _require_admin(request)
    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    c = get_db()
    try:
        rows = c.execute(
            "SELECT id, created_at, query, answer_preview, sources_used, "
            "mode, collection_id, workspace_id, response_time_ms "
            "FROM feedback WHERE rating = -1 "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        total_row = c.execute("SELECT COUNT(*) AS n FROM feedback WHERE rating = -1").fetchone()
    finally:
        c.close()
    return {
        "items": [dict(r) for r in rows],
        "total": int(total_row["n"] if total_row else 0),
        "limit": limit,
        "offset": offset,
    }

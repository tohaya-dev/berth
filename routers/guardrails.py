"""Guardrails / PII 検出系エンドポイント。

- /api/guardrails/pii-detections (GET): audit_logs から PII 検出を集計
- /api/guardrails/blocked-topics (GET/POST): 禁止トピック一覧 + 追加
- /api/guardrails/blocked-topics/{id} (DELETE): 禁止トピック削除
- /api/pii-detections (GET): chunks テーブルからドキュメント単位で集計 (P4-14)
"""

from __future__ import annotations

import re

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request

from db import get_db, new_id
from core.errors import api_error
from core.audit import log_admin_change
# ga-close-v3 PartD D-3: 伏字件数の数え方は guardrail.py の 1 か所に集約する。
from guardrail import pii_count_sql


router = APIRouter(tags=["guardrails"])


@router.get("/api/guardrails/pii-detections", response_model=None)
def list_pii_detections_from_audit(
    request: Request,
    collection_id: str | None = None,
    limit: int = 100,
):
    """audit_logs から PII 検出ログを集計して返す.

    audit_logs.detail (TEXT/JSON) に保存されている形式が
    実装依存のため、JSON でないものは無視する。
    """
    # FIX-026: PII 検出履歴は admin 限定化 (/api/pii-detections と対称化)
    from core.auth import _require_admin

    _require_admin(request)
    limit = max(1, min(int(limit or 100), 500))
    # 既存の audit_logs.detail には JSON でないテキスト (フリー文字列) も
    # 混在する。json_extract が malformed JSON で SQLite エラーを投げないよう
    # WHERE で json_valid() フィルタを掛けてから抽出する。
    sql = """
        SELECT
            json_extract(detail, '$.document_id')   AS document_id,
            json_extract(detail, '$.filename')      AS filename,
            json_extract(detail, '$.collection_id') AS collection_id,
            json_extract(detail, '$.pii_type')      AS pii_type,
            COUNT(*) AS detection_count,
            MAX(timestamp) AS last_detected
        FROM audit_logs
        WHERE action IN ('PII_DETECTED', 'pii_detected')
          AND detail IS NOT NULL
          AND json_valid(detail) = 1
    """
    params: list = []
    if collection_id:
        sql += " AND json_extract(detail, '$.collection_id') = ?"
        params.append(collection_id)
    # DD-CYN-0143 版5 (V-2 実叩きで発覚): 選択列のうち非集計の filename / collection_id が
    # GROUP BY に無く、PostgreSQL では GroupingError (500) になっていた (SQLite は黙って通す)。
    # 出力列名での GROUP BY は両方言とも有効。
    sql += " GROUP BY document_id, filename, collection_id, pii_type ORDER BY last_detected DESC LIMIT ?"
    params.append(limit)
    conn = get_db()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return {"detections": [dict(r) for r in rows]}


@router.get("/api/guardrails/blocked-topics", response_model=None)
def list_blocked_topics(request: Request):
    """登録済み禁止トピック一覧.

    Stage R5-fix P1 #12: 認証必須化 (pattern が偵察情報になるため)。
    """
    from core.auth import _require_authenticated

    _require_authenticated(request)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, pattern, is_regex, action, created_by, "
            "created_at, is_active FROM blocked_topics ORDER BY created_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return {"topics": [dict(r) for r in rows]}


@router.post("/api/guardrails/blocked-topics", response_model=None)
async def add_blocked_topic(request: Request):
    """禁止トピックを追加 (admin のみ)."""
    from core.auth import _require_admin

    user = _require_admin(request)
    body = await parse_body_pydantic(request)
    name = (body.get("name") or "").strip()
    pattern = (body.get("pattern") or "").strip()
    is_regex = bool(body.get("is_regex"))
    act = (body.get("action") or "block").strip()
    if not name or not pattern:
        raise api_error("BAD_REQUEST", "name and pattern are required", status=400)
    if act not in ("block", "warn"):
        raise api_error("BAD_REQUEST", "action must be 'block' or 'warn'", status=400)
    if is_regex:
        try:
            re.compile(pattern)
        except re.error as e:
            raise api_error("INVALID_REGEX", f"Invalid regex: {e}", status=400)
    tid = new_id()
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO blocked_topics
               (id, name, pattern, is_regex, action, created_by, is_active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (tid, name, pattern, 1 if is_regex else 0, act, user["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    log_admin_change(
        user["id"],
        "blocked_topic",
        tid,
        "create",
        None,
        {"name": name, "pattern": pattern, "is_regex": is_regex, "action": act},
    )
    return {"id": tid, "name": name, "status": "created"}


@router.delete("/api/guardrails/blocked-topics/{topic_id}", response_model=None)
def delete_blocked_topic(topic_id: str, request: Request):
    from core.auth import _require_admin

    user = _require_admin(request)
    conn = get_db()
    try:
        before = conn.execute(
            "SELECT name, pattern FROM blocked_topics WHERE id = ?",
            (topic_id,),
        ).fetchone()
        if not before:
            raise api_error("NOT_FOUND", "blocked_topic not found", status=404)
        conn.execute("DELETE FROM blocked_topics WHERE id = ?", (topic_id,))
        conn.commit()
    finally:
        conn.close()
    log_admin_change(user["id"], "blocked_topic", topic_id, "delete", dict(before), None)
    return {"status": "deleted"}


@router.get("/api/pii-detections", response_model=None)
def list_pii_detections_from_chunks(request: Request, limit: int | None = None, offset: int = 0):
    """P4-14: PII検出済みチャンクをドキュメント単位で集計して返す。
    BETA-pagination: limit/offset でページネーション。"""
    from core.auth import _require_admin

    _require_admin(request)
    if limit is not None and limit not in (10, 20, 50, 100):
        raise HTTPException(status_code=400, detail="limitは10/20/50/100のいずれかです")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offsetは0以上です")
    conn = get_db()
    try:
        # ga-close-v3 PartD D-3: 数え方は guardrail.pii_count_sql (= pii_counts_from_summaries
        #   と同じ定義) の 1 か所から取る。旧 c.pii_detected = 1 は層を絞らないため
        #   raw+masked の二重計上で、要約・一覧・公開履歴と食い違っていた。
        _pii_pred = pii_count_sql("c")
        total = conn.execute(
            f"""SELECT COUNT(*) AS c FROM (
                 SELECT c.source_doc, c.collection_id FROM chunks c
                 WHERE {_pii_pred}
                 GROUP BY c.source_doc, c.collection_id
               ) AS sub"""
        ).fetchone()["c"]
        pagination_sql = " LIMIT 200" if limit is None else " LIMIT ? OFFSET ?"
        pagination_params: list = [] if limit is None else [limit, offset]
        rows = conn.execute(
            f"""SELECT c.source_doc, c.collection_id,
                      COUNT(*) AS pii_chunks,
                      SUM(CASE WHEN c.excluded     = 1 THEN 1 ELSE 0 END) AS excluded
               FROM chunks c
               WHERE {_pii_pred}
               GROUP BY c.source_doc, c.collection_id
               ORDER BY pii_chunks DESC
               {pagination_sql}""",
            pagination_params,
        ).fetchall()
        # Collection 名解決
        col_names: dict[str, str] = {}
        for col_id in {r["collection_id"] for r in rows if r["collection_id"]}:
            cn = conn.execute("SELECT name FROM collections WHERE id = ?", (col_id,)).fetchone()
            if cn:
                col_names[col_id] = cn["name"]
    finally:
        conn.close()
    items = []
    for r in rows:
        items.append(
            {
                "source_doc": r["source_doc"] or "(unnamed)",
                "collection_id": r["collection_id"],
                "collection_name": col_names.get(r["collection_id"], r["collection_id"] or ""),
                "pii_chunks": int(r["pii_chunks"] or 0),
                "excluded": int(r["excluded"] or 0),
            }
        )
    resp = {"items": items, "total_documents": total, "total": total}
    if limit is not None:
        resp["limit"] = limit
        resp["offset"] = offset
    return resp

"""Stats endpoints (/api/stats/*)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from db import get_db
from core.auth import _require_admin

router = APIRouter(tags=["stats"])


@router.get("/api/stats/performance", response_model=None)
def get_performance_stats(request: Request, days: int = 7):
    """P3 §4: 応答時間・ディスク使用量・モデル変更イベント."""
    from server import _disk_usage_bytes

    _require_admin(request)
    import os as _os
    import shutil as _shutil

    days = max(1, min(int(days or 7), 90))
    conn = get_db()
    try:
        rt_rows = conn.execute(
            f"""
            SELECT
                date(timestamp) AS day,
                AVG(CAST(json_extract(detail, '$.llm_ms') AS REAL))    AS avg_llm_ms,
                AVG(CAST(json_extract(detail, '$.search_ms') AS REAL)) AS avg_search_ms
            FROM audit_logs
            WHERE action IN ('chat_query', 'RAG_QUERY')
              AND timestamp >= datetime('now', '-{days} days')
            GROUP BY day
            ORDER BY day
            """
        ).fetchall()
        model_rows = conn.execute(
            f"""
            SELECT timestamp,
                   COALESCE(json_extract(detail, '$.model_name'), detail) AS model_name
            FROM audit_logs
            WHERE action IN ('MODEL_CHANGED', 'model_changed', 'llm_model_changed')
              AND timestamp >= datetime('now', '-{days} days')
            ORDER BY timestamp
            """
        ).fetchall()
    finally:
        conn.close()

    # release/v1.0.0-alpha: CYNOVELA_DB / CYNOVELA_CHROMA env 経由でデータディレクトリを解決
    sqlite_bytes = _disk_usage_bytes(_os.environ.get("CYNOVELA_DB", ""))
    chroma_bytes = _disk_usage_bytes(_os.environ.get("CYNOVELA_CHROMA", ""))
    try:
        _, _, free = _shutil.disk_usage("/")
    except Exception:
        free = 0

    return {
        "days": days,
        "response_times": [
            {
                "day": r["day"],
                "avg_llm_ms": float(r["avg_llm_ms"] or 0.0),
                "avg_search_ms": float(r["avg_search_ms"] or 0.0),
            }
            for r in rt_rows
        ],
        "disk": {
            "chroma_bytes": int(chroma_bytes),
            "sqlite_bytes": int(sqlite_bytes),
            "total_bytes": int(chroma_bytes + sqlite_bytes),
            "free_bytes": int(free),
        },
        "model_events": [{"timestamp": r["timestamp"], "model_name": r["model_name"] or "unknown"} for r in model_rows],
    }


@router.get("/api/stats/model", response_model=None)
def get_model_stats(request: Request, days: int = 7):
    """モデル別クエリ数・平均応答時間 (audit_logs.detail JSON 経由)."""
    _require_admin(request)
    days = max(1, min(int(days or 7), 90))
    conn = get_db()
    try:
        rows = conn.execute(
            f"""
            SELECT
                COALESCE(json_extract(detail, '$.model_name'), 'unknown') AS model_name,
                COUNT(*) AS query_count,
                AVG(CAST(json_extract(detail, '$.llm_ms') AS REAL)) AS avg_llm_ms
            FROM audit_logs
            WHERE action IN ('chat_query', 'RAG_QUERY')
              AND timestamp >= datetime('now', '-{days} days')
            GROUP BY model_name
            ORDER BY query_count DESC
            """
        ).fetchall()
    finally:
        conn.close()
    return {
        "days": days,
        "models": [
            {
                "model_name": r["model_name"],
                "query_count": int(r["query_count"]),
                "avg_llm_ms": float(r["avg_llm_ms"] or 0.0),
            }
            for r in rows
        ],
    }


@router.get("/api/stats/rag-quality", response_model=None)
def get_rag_quality_stats(request: Request, days: int = 7):
    """RAG 品質スコア推移・ゼロヒット率・Guardrail 内訳."""
    _require_admin(request)
    days = max(1, min(int(days or 7), 90))
    conn = get_db()
    try:
        quality_rows = conn.execute(
            f"""
            SELECT
                date(timestamp) AS day,
                AVG(CAST(json_extract(detail, '$.faithfulness') AS REAL)) AS avg_faithfulness,
                AVG(CAST(json_extract(detail, '$.top_score')   AS REAL)) AS avg_top_score,
                SUM(CASE WHEN json_extract(detail, '$.feedback') = 'positive' THEN 1 ELSE 0 END)
                    * 1.0 / NULLIF(COUNT(*), 0) AS thumbs_up_rate,
                COUNT(*) AS query_count
            FROM audit_logs
            WHERE action IN ('chat_query', 'RAG_QUERY')
              AND timestamp >= datetime('now', '-{days} days')
            GROUP BY day
            ORDER BY day
            """
        ).fetchall()
        zero_hit_rows = conn.execute(
            f"""
            SELECT
                date(timestamp) AS day,
                SUM(CASE WHEN CAST(json_extract(detail, '$.top_score') AS REAL) < 0.3
                          THEN 1 ELSE 0 END)
                    * 1.0 / NULLIF(COUNT(*), 0) AS zero_hit_rate
            FROM audit_logs
            WHERE action IN ('chat_query', 'RAG_QUERY')
              AND timestamp >= datetime('now', '-{days} days')
            GROUP BY day
            ORDER BY day
            """
        ).fetchall()
        guardrail_rows = conn.execute(
            f"""
            SELECT
                COALESCE(
                    json_extract(detail, '$.guardrail_type'),
                    action
                ) AS guardrail_type,
                COUNT(*) AS count
            FROM audit_logs
            WHERE action IN (
                'GUARDRAIL_TRIGGERED', 'PROMPT_INJECTION_BLOCKED',
                'LOW_CONFIDENCE_FALLBACK'
            )
              AND timestamp >= datetime('now', '-{days} days')
            GROUP BY guardrail_type
            """
        ).fetchall()
    finally:
        conn.close()
    return {
        "days": days,
        "quality_trend": [
            {
                "day": r["day"],
                "avg_faithfulness": float(r["avg_faithfulness"]) if r["avg_faithfulness"] is not None else None,
                "avg_top_score": float(r["avg_top_score"]) if r["avg_top_score"] is not None else None,
                "thumbs_up_rate": float(r["thumbs_up_rate"]) if r["thumbs_up_rate"] is not None else None,
                "query_count": int(r["query_count"] or 0),
            }
            for r in quality_rows
        ],
        "zero_hit_trend": [
            {
                "day": r["day"],
                "zero_hit_rate": float(r["zero_hit_rate"]) if r["zero_hit_rate"] is not None else None,
            }
            for r in zero_hit_rows
        ],
        "guardrail_breakdown": [
            {"guardrail_type": r["guardrail_type"], "count": int(r["count"] or 0)} for r in guardrail_rows
        ],
    }

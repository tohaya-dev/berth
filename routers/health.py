"""Health check endpoints (/api/health, /api/health/*)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from db import get_db

import state as _state
from core.api_schema import BaseResponseSchema as _PilotResp
from core.errors import api_error

logger = logging.getLogger("cynovela.health")

router = APIRouter(tags=["health"])


def _vector_consistency() -> dict:
    """Report semantic readiness for Postgres/pgvector deployments."""
    provider = "chromadb"
    unified = False
    try:
        from core.data_layer import get_data_layer

        unified = bool(get_data_layer().is_unified)
    except Exception:
        unified = False
    try:
        from core.config import CYNOVELA_CONFIG

        provider = ((CYNOVELA_CONFIG.get("vector_store") or {}).get("provider") or provider).lower()
    except Exception:
        pass

    if not unified:
        return {"status": "ok", "provider": provider, "unified": False}

    conn = get_db()
    try:
        chunks = int(conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"])
        masked = int(conn.execute("SELECT count(*) AS n FROM chunks WHERE tier = 'masked'").fetchone()["n"])
        try:
            vectors = int(conn.execute("SELECT count(*) AS n FROM chunks_vec").fetchone()["n"])
            table = "present"
        except Exception:
            vectors = 0
            table = "missing"
    finally:
        conn.close()

    status = "ok"
    reason = None
    if chunks > 0 and vectors == 0:
        status = "degraded"
        reason = "documents_present_vectors_empty"
    elif masked > 0 and vectors < masked:
        status = "degraded"
        reason = "vectors_below_masked_chunks"
    return {
        "status": status,
        "provider": provider,
        "unified": True,
        "chunks": chunks,
        "masked_chunks": masked,
        "vectors": vectors,
        "table": table,
        "reason": reason,
    }


@router.get("/api/health", response_model=_PilotResp)  # FIX-052 パイロット
def health():
    # P1-2: CircuitBreaker 状態を含める
    cb = _state.llm_circuit_breaker
    # fix062 A7: demo モード判定フラグをレスポンスに含める (frontend / 監視で利用)
    _demo = bool(_state.config.demo) if (_state.config is not None) else False
    from core.version import APP_VERSION

    # DD-CYN-0143 X-1: 稼働イメージのタグを併記する。読む先は環境変数1つ
    # (CYNOVELA_IMAGE_TAG。deploy 側が IMAGE の値から注入する)。未注入なら None。
    import os as _os

    vector = _vector_consistency()
    return {
        "status": "ok" if vector.get("status") == "ok" else "degraded",
        "version": APP_VERSION,
        "image": _os.environ.get("CYNOVELA_IMAGE_TAG") or None,
        "demo": _demo,
        "circuit_breaker": cb.status() if cb else None,
        "vector": vector,
    }


# ─── P2 §3: per-component health endpoints (used by frontend dashboard) ───


@router.get("/api/health/db", response_model=_PilotResp)  # FIX-052 パイロット
def health_db():
    try:
        conn = get_db()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return {"status": "ok"}
    except Exception as e:
        # FIX-027: str(e) 漏洩 (SQLite ファイルパス・内部状態) を抑制
        logger.exception(f"/api/health/db 失敗: {e}")
        raise api_error("DB_UNAVAILABLE", "db_unavailable", status=503)


@router.get("/api/health/vector", response_model=_PilotResp)  # FIX-052 パイロット
def health_vector():
    """Vector store connectivity and semantic consistency."""
    try:
        consistency = _vector_consistency()
        if consistency.get("unified"):
            return consistency
        from rag import get_chroma

        client = get_chroma()
        try:
            client.heartbeat()
        except AttributeError:
            client.list_collections()
        return {"status": "ok", **consistency}
    except Exception as e:
        # FIX-028: str(e) 漏洩 (ChromaDB 内部状態) を抑制
        logger.exception(f"/api/health/vector 失敗: {e}")
        raise api_error("VECTOR_STORE_UNAVAILABLE", "vector_store_unavailable", status=503)


@router.get("/api/health/guardrails", response_model=_PilotResp)  # FIX-052 パイロット
def health_guardrails():
    """Guardrail Policy が active で 1 件以上あるかをチェック。"""
    try:
        conn = get_db()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM guardrail_policies WHERE state = 'active'").fetchone()
            count = int(row["c"]) if row else 0
        finally:
            conn.close()
        return {"status": "ok", "active_policies": count}
    except Exception as e:
        # FIX-029: str(e) 漏洩 (SQL リテラル) を抑制
        logger.exception(f"/api/health/guardrails 失敗: {e}")
        raise api_error("GUARDRAILS_UNAVAILABLE", "guardrails_unavailable", status=503)


@router.get("/api/ready", response_model=_PilotResp)  # P1-6: K8S Readiness Probe
async def readiness_probe():
    """K8S Readiness Probe: DB と Vector Store が応答可能かを確認する。"""
    try:
        conn = get_db()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        # Vector store 疎通（health_vector と同じ取得方法に合わせる）
        from rag import get_chroma

        client = get_chroma()
        try:
            client.heartbeat()
        except AttributeError:
            client.list_collections()
        consistency = _vector_consistency()
        if consistency.get("status") != "ok":
            raise RuntimeError("vector_degraded")
        return {"status": "ready", "vector": consistency}
    except Exception as e:
        # FIX-027/028 と同様、str(e) 漏洩を抑制しつつ 503 を返す
        logger.exception(f"/api/ready 失敗: {e}")
        raise api_error("NOT_READY", "not_ready", status=503)


@router.get("/api/health/detailed", response_model=None)
async def health_detailed(request: Request):
    """全Providerの状態 + システム（DB/Chroma/Stale）を一括返却。

    Stage R5-fix P2 #16: 運用詳細 (database.size_mb 等) は admin 限定。
    一般ユーザーは /api/health で十分。

    後方互換: 既存のフラットキー (llm/embedding/vector_store/reranker) を維持しつつ、
    新規 components キーで database/vector_store_disk/stale_publishing を追加する。
    """
    from core.auth import _require_admin

    _require_admin(request)
    # server モジュール側のグローバル / 定数を遅延 import
    from server import (
        _vector_store,
        DB_PATH_FOR_BACKUP,
        CHROMA_PATH_FOR_BACKUP,
    )
    from rag import get_embedding_provider_current, get_reranker_provider_current

    adapter = _state.adapter
    out: dict = {}
    components: dict = {}

    # Provider接続テスト（既存）
    # Stage-2G-4 G5: str(e) を返さず、汎用 "error" のみ。詳細は logger 経由。
    try:
        out["llm"] = (
            await adapter.test_connection() if adapter else {"status": "error", "error": "adapter not initialized"}
        )
    except Exception:
        out["llm"] = {"status": "error", "error": "provider_unavailable"}
    try:
        out["embedding"] = await get_embedding_provider_current().test_connection()
    except Exception:
        out["embedding"] = {"status": "error", "error": "provider_unavailable"}
    try:
        out["vector_store"] = await _vector_store.test_connection()
    except Exception:
        out["vector_store"] = {"status": "error", "error": "provider_unavailable"}
    try:
        out["reranker"] = await get_reranker_provider_current().test_connection()
    except Exception:
        out["reranker"] = {"status": "error", "error": "provider_unavailable"}

    # BLOCK 4: システムコンポーネント
    try:
        conn = get_db()
        try:
            conn.execute("SELECT 1").fetchone()
            size = DB_PATH_FOR_BACKUP.stat().st_size if DB_PATH_FOR_BACKUP.exists() else 0
            wal = conn.execute("PRAGMA journal_mode").fetchone()
            fk = conn.execute("PRAGMA foreign_keys").fetchone()
            stale_rows = conn.execute("SELECT id FROM collections WHERE status='publishing'").fetchall()
        finally:
            conn.close()
        components["database"] = {
            "status": "ok",
            "size_mb": round(size / 1024 / 1024, 2),
            "journal_mode": wal[0] if wal else "?",
            "foreign_keys": bool(fk[0]) if fk else False,
        }
        components["stale_publishing"] = {
            "status": "ok" if not stale_rows else "warning",
            "count": len(stale_rows),
        }
    except Exception:
        components["database"] = {"status": "error", "error": "db_unavailable"}
    try:
        chroma_size = (
            sum(p.stat().st_size for p in CHROMA_PATH_FOR_BACKUP.rglob("*") if p.is_file())
            if CHROMA_PATH_FOR_BACKUP.exists()
            else 0
        )
        components["vector_store_disk"] = {
            "status": "ok" if CHROMA_PATH_FOR_BACKUP.exists() else "missing",
            "path": str(CHROMA_PATH_FOR_BACKUP),
            "size_mb": round(chroma_size / 1024 / 1024, 2),
        }
    except Exception:
        components["vector_store_disk"] = {"status": "error", "error": "disk_unavailable"}

    overall = "ok"
    for c in components.values():
        if c.get("status") == "error":
            overall = "error"
            break
        if c.get("status") in ("warning", "missing") and overall == "ok":
            overall = "degraded"
    out["status"] = overall
    out["components"] = components
    return out

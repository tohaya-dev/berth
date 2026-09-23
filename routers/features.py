"""Features flags endpoints (/api/features)."""

from __future__ import annotations

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request

from db import get_db
from core.auth import _require_admin
from core.audit import _log_audit

router = APIRouter(tags=["features"])


@router.get("/api/features", response_model=None)
def get_features_api(request: Request):
    """P4-15: 全featuresフラグの現在値を返す。"""
    _require_admin(request)
    from core.config import get_features as _gf

    return _gf()


@router.patch("/api/features", response_model=None)
async def update_features_api(request: Request):
    """P4-15: featuresフラグを更新する。DB settings に永続化する。"""
    _require_admin(request)
    from core.config import set_runtime_feature_override

    body = await parse_body_pydantic(request)
    if not isinstance(body, dict):
        raise HTTPException(400, "オブジェクト形式で {feature: bool} を指定してください")
    allowed = {
        "metadata_engine",
        "data_guardrails",
        "data_sync",
        "audit_log",
        "pipeline_visualization",
        "session_history",
        "feedback",
    }
    conn = get_db()
    try:
        for k, v in body.items():
            if k not in allowed:
                raise HTTPException(400, f"未知のfeatureキー: {k}")
            enabled = bool(v)
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"feature.{k}", "1" if enabled else "0"),
            )
            set_runtime_feature_override(k, enabled)
        _log_audit(conn, "features_updated", "", ",".join(body.keys()))
        conn.commit()
    finally:
        conn.close()
    from core.config import get_features as _gf

    return _gf()

"""Alert poll endpoint (/api/alerts)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from core.auth import _require_admin

router = APIRouter(tags=["alerts"])


@router.get("/api/alerts", response_model=None)
async def get_alerts(request: Request):
    """全アラートチェック結果を返す."""
    _require_admin(request)
    from alert_engine import run_alert_checks

    alerts = await run_alert_checks()
    return {
        "alerts": [
            {
                "level": a.level.value,
                "code": a.code,
                "message_en": a.message_en,
                "message_ja": a.message_ja,
                "detail": a.detail,
            }
            for a in alerts
        ],
    }

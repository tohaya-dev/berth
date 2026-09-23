"""Audit log endpoints (/api/audit-logs/*)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from db import get_db
from core.auth import _require_admin
from core.audit import _AUDIT_CATEGORY_MAP

router = APIRouter(tags=["audit-logs"])

# C-9: DB 格納は UTC 生値のまま維持し、出力側で日本時間へ変換する
_TZ_LOCAL = ZoneInfo("Asia/Tokyo")


def _utc_to_local_iso(ts) -> str | None:
    """C-9: DB の timestamp (UTC 生値 'YYYY-MM-DD HH:MM:SS') を
    Asia/Tokyo の ISO8601 (+09:00 オフセット付き) 文字列へ変換する。
    変換できない値は None を返す (既存 timestamp フィールドは常に残るため表示は壊れない)。
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("T", " "))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_TZ_LOCAL).isoformat()
    except Exception:
        return None


@router.get("/api/audit-logs", response_model=None)
def list_audit_logs(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    action: str | None = None,
    q: str | None = None,
    category: str | None = None,
    workspace_id: str | None = None,
):
    """BETA-pagination: q (キーワード), category, workspace_id, offset を追加。"""
    from server import rows_to_list

    _require_admin(request)
    if offset < 0:
        raise HTTPException(status_code=400, detail="offsetは0以上です")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limitは1〜200の範囲です")
    conn = get_db()
    try:
        where_clauses: list[str] = []
        params: list = []
        if action:
            where_clauses.append("action = ?")
            params.append(action)
        if category:
            cat_actions = [a for a, c in _AUDIT_CATEGORY_MAP.items() if c == category]
            if cat_actions:
                placeholders = ",".join("?" * len(cat_actions))
                where_clauses.append(f"(action IN ({placeholders}) OR category = ?)")
                params.extend(cat_actions)
                params.append(category)
            else:
                where_clauses.append("category = ?")
                params.append(category)
        if workspace_id:
            # target は ws-xxx の生 ID と URL (.../workspaces/ws-xxx/...) の両形式があるため LIKE で包含一致
            where_clauses.append("(target = ? OR target LIKE ?)")
            params.append(workspace_id)
            params.append(f"%{workspace_id}%")
        if q:
            where_clauses.append("(action LIKE ? OR detail LIKE ? OR target LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        total = conn.execute(f"SELECT COUNT(*) FROM audit_logs {where_sql}", params).fetchone()[0]

        logs = rows_to_list(
            conn.execute(
                f"SELECT * FROM audit_logs {where_sql} " f"ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        )
    finally:
        conn.close()
    # C-9: 既存 timestamp (UTC 生値) は互換のため残し、日本時間の timestamp_local を追加する
    for _l in logs:
        _l["timestamp_local"] = _utc_to_local_iso(_l.get("timestamp"))
    # target は DB に格納された値をそのまま返す（URL/path を basename で切り詰めない）
    return {"items": logs, "total": total, "limit": limit, "offset": offset}


@router.get("/api/audit-logs/export", response_model=None)
def export_audit_logs(request: Request):
    """Export entire audit_logs as CSV. Admin role required."""
    from core.auth import _require_admin

    _exp_admin = _require_admin(request)
    import csv
    import io

    # sokessan-fix-a7-20260711: 監査ログ CSV 全件エクスポート(データ持ち出し)操作自体を監査に残す。
    # 従来この経路は無記録で、監査ログの持ち出し痕跡が残らなかった。
    try:
        from core.audit import _log_audit as _la_exp

        _exp_ca = get_db()
        try:
            _la_exp(
                _exp_ca,
                "audit_logs_exported",
                detail="audit CSV export (all)",
                ip_address=(request.client.host if request.client else None),
                user_id=(_exp_admin.get("id") if isinstance(_exp_admin, dict) else None),
            )
        finally:
            _exp_ca.close()
    except Exception:
        pass

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, timestamp, action, target, detail FROM audit_logs " "ORDER BY timestamp DESC"
        ).fetchall()
    finally:
        conn.close()

    out = io.StringIO()
    writer = csv.DictWriter(
        out,
        fieldnames=[
            "id",
            "timestamp",
            "timestamp_local",  # C-9: 日本時間 (+09:00) の列を追加 (既存列は維持)
            "action",
            "target",
            "detail",
        ],
    )
    writer.writeheader()
    for r in rows:
        d = dict(r)
        d["timestamp_local"] = _utc_to_local_iso(d.get("timestamp"))
        writer.writerow(d)
    out.seek(0)

    filename = f"cynovela_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

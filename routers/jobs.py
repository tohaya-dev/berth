"""Publish job status endpoint (/api/jobs/*)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from db import get_db
from core.auth import _require_admin

router = APIRouter(tags=["jobs"])


@router.get("/api/jobs", response_model=None)
def list_jobs(
    request: Request,
    status: str | None = None,
    collection_id: str | None = None,
    limit: int = 50,
    kind: str = "all",
):
    """DD-CYN-0143 A-2: publish_jobs の一覧を新しい順に返す。
    id を知らなくても進み具合へ到達できる口 (CLI publish status / MCP get_job_status の起点)。

    N-3: kind でジョブ横断一覧にする。
      kind=publish … 従来どおり publish_jobs のみ
      kind=scan    … sources 表から走査を疑似ジョブ形式で合成
      kind=all     … 両方 (既定。publish 行の従来フィールドは変えず type だけ追加)
    scan の疑似ジョブ形式:
      {id: "scan:"+source_id, type: "scan", status, source_id, name, file_count, last_scanned}
    """
    _require_admin(request)
    if kind not in ("all", "publish", "scan"):
        raise HTTPException(400, "kindは all / publish / scan のいずれかです")
    limit = max(1, min(int(limit), 500))
    jobs: list[dict] = []
    conn = get_db()
    try:
        if kind in ("all", "publish"):
            where: list[str] = []
            params: list = []
            if status:
                where.append("status = ?")
                params.append(status)
            if collection_id:
                where.append("collection_id = ?")
                params.append(collection_id)
            sql = "SELECT * FROM publish_jobs"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
            params.append(limit)
            for r in conn.execute(sql, params).fetchall():
                _j = dict(r)
                # 追加のみ (既存クライアント互換: 従来フィールドの形は変えない)
                _j.setdefault("type", "publish")
                jobs.append(_j)
        # scan は publish_jobs のようなジョブレコードを持たないため、sources 表から
        # 「走査中の全件 + 直近 last_scanned のもの limit 件」を疑似ジョブとして合成する。
        # collection_id は publish 専用の絞り込みなので、指定時は scan を混ぜない。
        if kind in ("all", "scan") and not collection_id:
            _rows = conn.execute(
                "SELECT id, name, status, file_count, last_scanned FROM sources "
                "WHERE status = 'scanning'"
            ).fetchall()
            _recent = conn.execute(
                "SELECT id, name, status, file_count, last_scanned FROM sources "
                "WHERE status <> 'scanning' AND last_scanned IS NOT NULL "
                "ORDER BY last_scanned DESC LIMIT ?",
                (limit,),
            ).fetchall()
            for r in list(_rows) + list(_recent):
                if status and (r["status"] or "") != status:
                    continue
                jobs.append(
                    {
                        "id": "scan:" + r["id"],
                        "type": "scan",
                        "status": r["status"],
                        "source_id": r["id"],
                        "name": r["name"],
                        "file_count": r["file_count"],
                        "last_scanned": r["last_scanned"],
                    }
                )
    finally:
        conn.close()
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/api/jobs/{job_id}", response_model=None)
def get_job_status(request: Request, job_id: str):
    """publish_jobs から job の現在状態を返す。見つからなければ 404。"""
    _require_admin(request)
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "Job not found")
    return dict(row)

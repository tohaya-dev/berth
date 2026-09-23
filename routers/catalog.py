"""Catalog endpoints (/api/catalog and /api/data-catalog*)."""

from __future__ import annotations

import csv as _csv
import io as _io
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from db import get_db
from core.auth import _require_admin, _require_authenticated
from core.errors import api_error

router = APIRouter(tags=["catalog"])


# ─── /api/catalog (legacy metadata catalog) ───


@router.get("/api/catalog", response_model=None)
def get_data_catalog(
    request: Request,
    search: str = "",
    doc_type: str = "",
    sensitivity: str = "",
    workspace_id: str = "",
    limit: int = 200,
):
    """全データアセット横断ビュー。"""
    from core.auth import _require_authenticated

    user = _require_authenticated(request)
    role = user.get("role") or "viewer"
    limit = max(1, min(int(limit or 200), 500))
    sql = """
        SELECT DISTINCT
            f.id          AS id,
            f.name        AS filename,
            f.scanned_at  AS uploaded_at,
            f.doc_type    AS doc_type,
            f.sensitivity_level AS sensitivity_level,
            f.sensitivity_score AS sensitivity_score,
            f.owner       AS owner,
            f.department  AS department,
            f.project     AS project,
            c.id          AS collection_id,
            c.name        AS collection_name,
            c.access_level AS access_level,
            w.id          AS workspace_id,
            w.name        AS workspace_name
        FROM files f
        LEFT JOIN collection_files cf ON cf.file_id = f.id
        LEFT JOIN collections c       ON c.id = cf.collection_id
        LEFT JOIN workspaces w        ON w.id = c.workspace_id
        WHERE 1=1
    """
    params: list = []
    if search:
        sql += " AND (f.name LIKE ? OR f.doc_type LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if doc_type:
        sql += " AND f.doc_type = ?"
        params.append(doc_type)
    if sensitivity:
        sql += " AND f.sensitivity_level = ?"
        params.append(sensitivity)
    if workspace_id:
        sql += " AND w.id = ?"
        params.append(workspace_id)
    # authz-fix-v1: 非admin は自分の所属WSのドキュメントのみ横断表示 (admin は全件=広域維持)。
    if role != "admin":
        sql += " AND w.id IN (SELECT workspace_id FROM workspace_users WHERE user_id = ?)"
        params.append(user.get("id"))
    if role == "viewer":
        sql += " AND (c.access_level IS NULL OR c.access_level != 'confidential')"
    sql += " ORDER BY f.scanned_at DESC LIMIT ?"
    params.append(limit)
    conn = get_db()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return {"documents": [dict(r) for r in rows]}


# ─── /api/data-catalog (P5-B) ───


def _data_catalog_impl(limit: int | None = None, offset: int = 0, category: str | None = None,
                       member_user_id: str | None = None):
    conn = get_db()
    try:
        where_parts = []
        params: list = []
        if category:
            where_parts.append("f.classification = ?")
            params.append(category)
        # authz-fix-v1: member_user_id 指定時 (非admin経路) は、その user が所属するWSに
        # 紐づく source の file のみに絞る (admin/export は member_user_id=None で全件=広域維持)。
        if member_user_id:
            where_parts.append(
                "f.source_id IN (SELECT source_id FROM workspace_sources WHERE workspace_id IN "
                "(SELECT workspace_id FROM workspace_users WHERE user_id = ?))"
            )
            params.append(member_user_id)
        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        total_all = conn.execute(
            f"SELECT COUNT(*) AS c FROM (" f"  SELECT 1 FROM files f {where_sql} GROUP BY f.name, f.path" f") AS sub",
            params,
        ).fetchone()["c"]

        pagination_sql = ""
        pagination_params: list = []
        if limit is not None:
            pagination_sql = " LIMIT ? OFFSET ?"
            pagination_params = [limit, offset]
        else:
            pagination_sql = " LIMIT 1000"
        rows = conn.execute(
            f"""SELECT MIN(f.id) AS id, f.name, f.path,
                      MIN(f.size) AS size, MIN(f.categories) AS categories,
                      MIN(f.doc_type) AS doc_type, MIN(f.sensitivity) AS sensitivity,
                      MIN(f.sensitivity_score) AS sensitivity_score,
                      MIN(f.auto_tags) AS auto_tags,
                      MIN(f.owner) AS owner, MIN(f.department) AS department,
                      MIN(f.classification) AS classification,
                      MIN(s.name) AS source_name, MIN(f.source_id) AS source_id
               FROM files f
               LEFT JOIN sources s ON s.id = f.source_id
               {where_sql}
               GROUP BY f.name, f.path
               ORDER BY
                 CASE MIN(f.sensitivity)
                   WHEN 'restricted' THEN 0
                   WHEN 'confidential' THEN 1
                   WHEN 'internal' THEN 2
                   ELSE 3 END,
                 f.name
               {pagination_sql}""",
            params + pagination_params,
        ).fetchall()
        last_acc_by_doc: dict[str, str] = {}
        try:
            for r in conn.execute(
                "SELECT source_doc, MAX(last_accessed_at) AS la FROM chunks "
                "WHERE last_accessed_at IS NOT NULL GROUP BY source_doc"
            ).fetchall():
                if r["source_doc"]:
                    last_acc_by_doc[r["source_doc"]] = r["la"]
        except Exception:
            pass

        # F4: 感度 / doc_type / department の内訳は「現在ページ」ではなく全件を対象に集計する。
        #     リスト本体と同じ category フィルタ + (name, path) dedup を使い total と整合させる。
        #     従来は下の `for r in rows` ループ（ページ分のみ）で数えており、
        #     1ページ=20件だけが反映されてカードが実分布と乖離していた。
        sens_count: dict[str, int] = {}
        type_count: dict[str, int] = {}
        dept_count: dict[str, int] = {}
        for br in conn.execute(
            f"""SELECT COALESCE(NULLIF(MIN(f.sensitivity), ''), 'public') AS sens,
                       COALESCE(NULLIF(MIN(f.doc_type), ''), 'general') AS dt,
                       MIN(f.department) AS dept
                FROM files f
                {where_sql}
                GROUP BY f.name, f.path""",
            params,
        ).fetchall():
            sens_count[br["sens"]] = sens_count.get(br["sens"], 0) + 1
            type_count[br["dt"]] = type_count.get(br["dt"], 0) + 1
            if br["dept"]:
                dept_count[br["dept"]] = dept_count.get(br["dept"], 0) + 1
    finally:
        conn.close()
    items = []
    for r in rows:
        try:
            cats = json.loads(r["categories"]) if r["categories"] else []
        except Exception:
            cats = []
        try:
            tags = json.loads(r["auto_tags"]) if r["auto_tags"] else []
        except Exception:
            tags = []
        sens = r["sensitivity"] or "public"
        dt = r["doc_type"] or "general"
        dept = r["department"] or ""
        last_acc = last_acc_by_doc.get(r["name"]) or ""
        is_stale = False
        days_since: int | None = None
        if last_acc:
            try:
                la_dt = datetime.fromisoformat(last_acc)
                days_since = (datetime.now() - la_dt).days
                is_stale = days_since >= 90
            except Exception:
                pass
        items.append(
            {
                "id": r["id"],
                "name": r["name"],
                # pathleak-fix-v1 (P5 露出1): 応答から絶対パス (f.path) を除去する。
                # 従来は items[].path にファイルシステム上の絶対パスをそのまま載せており、
                # 閲覧者を含む全認証利用者へ内部ディレクトリ構成が露出していた。
                # 一覧の用途 (名前・件数・種別・感度・部門等) には不要なため項目ごと落とす。
                # SELECT / GROUP BY 側の f.path は (name, path) の重複排除キーとして
                # 引き続き必要なので残す (total と一覧の整合を保つため)。
                "size": r["size"],
                "source": r["source_name"],
                "source_id": r["source_id"],
                "doc_type": dt,
                "sensitivity": sens,
                "sensitivity_score": r["sensitivity_score"] or 0.0,
                "department": dept,
                "owner": r["owner"] or "",
                "categories": cats,
                "auto_tags": tags,
                "last_accessed_at": last_acc,
                "days_since_access": days_since,
                "is_stale": is_stale,
                "classification": r["classification"] if "classification" in r.keys() else None,
            }
        )
    resp = {
        "items": items,
        "total": total_all,
        "sensitivity_breakdown": sens_count,
        "doc_type_breakdown": type_count,
        "department_breakdown": dept_count,
    }
    if limit is not None:
        resp["limit"] = limit
        resp["offset"] = offset
    return resp


@router.get("/api/data-catalog", response_model=None)
def data_catalog(
    request: Request,
    limit: int | None = None,
    offset: int = 0,
    category: str | None = None,
):
    """P5-B: 全ドキュメント横断のデータカタログ。"""
    user = _require_authenticated(request)
    if limit is not None and limit not in (10, 20, 50, 100):
        raise HTTPException(status_code=400, detail="limitは10/20/50/100のいずれかです")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offsetは0以上です")
    # authz-fix-v1: 非admin は所属WSのドキュメントのみ (admin は member_user_id=None で全件=広域維持)。
    _member = None if (user or {}).get("role") == "admin" else (user or {}).get("id")
    return _data_catalog_impl(limit=limit, offset=offset, category=category, member_user_id=_member)


@router.get("/api/data-catalog/export", response_model=None)
def data_catalog_export(request: Request, format: str = "csv", category: str | None = None):
    _require_admin(request)
    data = _data_catalog_impl(limit=None, offset=0, category=category)
    items = data.get("items", [])
    if format.lower() == "json":
        return Response(
            content=json.dumps(items, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="data-catalog-{category or "all"}.json"'},
        )
    buf = _io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(["name", "category", "sensitivity", "source", "department", "owner", "last_accessed_at"])
    for it in items:
        writer.writerow(
            [
                it.get("name", ""),
                it.get("classification") or (it.get("auto_tags", [{}])[0] if it.get("auto_tags") else ""),
                it.get("sensitivity", ""),
                it.get("source", ""),
                it.get("department", ""),
                it.get("owner", ""),
                it.get("last_accessed_at", ""),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="data-catalog-{category or "all"}.csv"'},
    )

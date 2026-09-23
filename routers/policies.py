"""Guardrail policies + policy-matrix + compliance report endpoints."""

from __future__ import annotations

import csv as _csv
import io as _io
import json
from datetime import datetime

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from db import get_db, new_id
from core.auth import _require_admin
from core.audit import _log_audit
# ga-close-v3 PartD D-3: 伏字件数の数え方は guardrail.py の 1 か所に集約する。
from guardrail import pii_count_sql

router = APIRouter(tags=["policies"])


@router.get("/api/policies", response_model=None)
def list_policies(
    request: Request,
    limit: int | None = None,
    offset: int = 0,
    q: str | None = None,
):
    """BETA-pagination: limit/offset/q でページネーション・検索を有効化。"""
    from server import rows_to_list

    _require_admin(request)
    if limit is not None and limit not in (10, 20, 50, 100):
        raise HTTPException(status_code=400, detail="limitは10/20/50/100のいずれかです")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offsetは0以上です")
    conn = get_db()
    try:
        where_parts: list[str] = []
        params: list = []
        if q:
            where_parts.append("name LIKE ?")
            params.append(f"%{q}%")
        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        total = None
        if limit is not None:
            total = conn.execute(f"SELECT COUNT(*) FROM guardrail_policies {where_sql}", params).fetchone()[0]

        pagination_sql = ""
        pagination_params: list = []
        if limit is not None:
            pagination_sql = " LIMIT ? OFFSET ?"
            pagination_params = [limit, offset]
        policies = rows_to_list(
            conn.execute(
                f"SELECT * FROM guardrail_policies {where_sql} " f"ORDER BY created_at DESC {pagination_sql}",
                params + pagination_params,
            ).fetchall()
        )
        for p in policies:
            if isinstance(p.get("rules"), str):
                try:
                    p["rules"] = json.loads(p["rules"])
                except Exception:
                    p["rules"] = []
            try:
                ws_cnt = conn.execute(
                    "SELECT COUNT(DISTINCT workspace_id) AS c FROM workspace_policies WHERE policy_id = ?",
                    (p["id"],),
                ).fetchone()
                p["workspace_count"] = int(ws_cnt["c"] or 0) if ws_cnt else 0
            except Exception:
                p["workspace_count"] = 0
            try:
                trig = conn.execute(
                    "SELECT COUNT(*) AS c, MAX(timestamp) AS last_t FROM audit_logs "
                    "WHERE action LIKE 'guardrail%' AND detail LIKE ? "
                    "AND timestamp >= datetime('now', '-7 days')",
                    (f'%{p["id"]}%',),
                ).fetchone()
                p["trigger_count_7d"] = int(trig["c"] or 0) if trig else 0
                p["last_triggered"] = (trig["last_t"] if trig else None) or None
            except Exception:
                p["trigger_count_7d"] = 0
                p["last_triggered"] = None
    finally:
        conn.close()
    if limit is None:
        return policies
    return {"items": policies, "total": total, "limit": limit, "offset": offset}


@router.get("/api/guardrails/policies", response_model=None)
def list_policies_guardrails_alias(
    request: Request,
    limit: int | None = None,
    offset: int = 0,
    q: str | None = None,
):
    """fix061 A6: /api/policies の alias (v11 E2E 経路維持)。"""
    return list_policies(request, limit=limit, offset=offset, q=q)


@router.post("/api/policies", response_model=None)
async def create_policy(request: Request):
    _require_admin(request)
    body = await parse_body_pydantic(request)
    name = body.get("name")
    rules = body.get("rules", [])
    if not name:
        raise HTTPException(400, "name is required")
    pid = new_id()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO guardrail_policies (id, name, rules) VALUES (?, ?, ?)",
            (pid, name, json.dumps(rules)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": pid, "name": name}


@router.put("/api/policies/{policy_id}", response_model=None)
async def update_policy(policy_id: str, request: Request):
    _require_admin(request)
    body = await parse_body_pydantic(request)
    conn = get_db()
    try:
        policy = conn.execute("SELECT * FROM guardrail_policies WHERE id = ?", (policy_id,)).fetchone()
        if not policy:
            conn.close()
            raise HTTPException(404, "Policy not found")

        if "name" in body:
            conn.execute("UPDATE guardrail_policies SET name = ? WHERE id = ?", (body["name"], policy_id))
        if "rules" in body:
            conn.execute("UPDATE guardrail_policies SET rules = ? WHERE id = ?", (json.dumps(body["rules"]), policy_id))
        if "state" in body:
            conn.execute("UPDATE guardrail_policies SET state = ? WHERE id = ?", (body["state"], policy_id))

        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": policy_id}


@router.delete("/api/policies/{policy_id}", response_model=None)
def delete_policy(request: Request, policy_id: str):
    _require_admin(request)
    conn = get_db()
    try:
        conn.execute("DELETE FROM guardrail_policies WHERE id = ?", (policy_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ─── /api/policy-matrix ───


def _default_policy_matrix() -> dict:
    """デフォルトのポリシーマトリクス。

    DD-CYN-0021 §2-1: 撤去済み職能名の行を削除し、
    有効ロール (core/constants.py VALID_ROLES = admin / viewer) だけを行に持つ。
    非 admin 行の既定値は従前の非 admin 分岐と同一 (CREDIT / MYNUMBER は
    exclude_from_rag、他は mask)。
    """
    roles = ["admin", "viewer"]
    pii_types = ["EMAIL", "PHONE_JP", "PHONE_LAND", "CREDIT", "MYNUMBER", "IPV4"]
    out: dict = {}
    for r in roles:
        out[r] = {}
        for t in pii_types:
            if r == "admin":
                out[r][t] = "log_only"
            elif t in ("CREDIT", "MYNUMBER"):
                out[r][t] = "exclude_from_rag"
            else:
                out[r][t] = "mask"
    return out


@router.get("/api/policy-matrix", response_model=None)
def get_policy_matrix(request: Request):
    """P5-C: ロール × PII種別 → action のマトリクスを返す。"""
    _require_admin(request)
    conn = get_db()
    try:
        row = conn.execute("SELECT rules FROM guardrail_policies WHERE name = 'P5-C-matrix'").fetchone()
    finally:
        conn.close()
    if not row:
        return {"matrix": _default_policy_matrix()}
    try:
        rules = json.loads(row["rules"]) if row["rules"] else {}
    except Exception:
        rules = {}
    if not rules:
        rules = _default_policy_matrix()
    return {"matrix": rules}


@router.put("/api/policy-matrix", response_model=None)
async def update_policy_matrix(request: Request):
    """P5-C: マトリクス全体を保存する。"""
    _require_admin(request)
    body = await parse_body_pydantic(request)
    matrix = body.get("matrix")
    if not isinstance(matrix, dict):
        raise HTTPException(400, "matrix オブジェクトが必要です")
    valid_actions = {"mask", "exclude_from_rag", "log_only", "allow"}
    for role, types in matrix.items():
        if not isinstance(types, dict):
            raise HTTPException(400, f"roles[{role}] はオブジェクト")
        for t, a in types.items():
            if a not in valid_actions:
                raise HTTPException(400, f"action {a} は許可されていません ({sorted(valid_actions)})")
    conn = get_db()
    try:
        existing = conn.execute("SELECT id FROM guardrail_policies WHERE name = 'P5-C-matrix'").fetchone()
        rules_json = json.dumps(matrix, ensure_ascii=False)
        if existing:
            conn.execute(
                "UPDATE guardrail_policies SET rules = ?, state = 'active' WHERE id = ?",
                (rules_json, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO guardrail_policies (id, name, rules, state) VALUES (?, ?, ?, 'active')",
                (new_id(), "P5-C-matrix", rules_json),
            )
        _log_audit(conn, "policy_matrix_updated", "", "")
        conn.commit()
    finally:
        conn.close()
    return {"matrix": matrix}


# ─── /api/compliance-report.csv ───


@router.get("/api/compliance-report.csv", response_model=None)
def compliance_report_csv(request: Request):
    """P5-C: コンプライアンスレポートCSVエクスポート。"""
    _require_admin(request)
    conn = get_db()
    try:
        # ga-close-v3 PartD D-3: 数え方は guardrail.pii_count_sql の 1 か所から取る
        #   (旧 c.pii_detected = 1 は層を絞らず raw+masked の二重計上だった)。
        _pii_pred = pii_count_sql("c")
        rows = conn.execute(
            f"""SELECT c.source_doc, c.collection_id,
                      COUNT(*) AS pii_chunks,
                      SUM(CASE WHEN c.excluded     = 1 THEN 1 ELSE 0 END) AS excluded_chunks,
                      MAX(col.name) AS collection_name
               FROM chunks c LEFT JOIN collections col ON col.id = c.collection_id
               WHERE {_pii_pred}
               GROUP BY c.source_doc, c.collection_id
               ORDER BY pii_chunks DESC"""
        ).fetchall()
    finally:
        conn.close()
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["report_generated_at", datetime.now().isoformat(timespec="seconds")])
    w.writerow([])
    w.writerow(["source_doc", "collection", "pii_chunks", "excluded_chunks"])
    for r in rows:
        w.writerow(
            [
                r["source_doc"] or "(unnamed)",
                r["collection_name"] or "",
                r["pii_chunks"] or 0,
                r["excluded_chunks"] or 0,
            ]
        )
    buf.seek(0)
    fname = f"compliance-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )

"""Workspace endpoints (/api/workspaces/*).

NOTE: chat/stream, full-export, import の3エンドポイントは Phase 3段階以降に移動予定 (server.py に残存)。
"""

from __future__ import annotations

import io
import json
import sqlite3
import threading
import zipfile
from datetime import datetime

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from db import get_db, new_id
import state as _state
from core.auth import _require_admin, _require_authenticated
from core.audit import _log_audit, log_admin_change
from core.errors import api_error

# vault-enc: raw chunks の preview 表示前に復号する。masked / 旧平文は素通し。
from vault_enc import dec_raw
# ga-close-v3 PartD D-3: 伏字件数の数え方は guardrail.py の 1 か所に集約する。
from guardrail import pii_counts_from_db, pii_counts_from_summaries

router = APIRouter(tags=["workspaces"])


def _integrity_error_types() -> tuple:
    """整合性制約違反として扱う例外の型 (SQLite / Postgres の両方)。

    Postgres 背骨 (core/relational_pg.py) は psycopg 3 の例外を翻訳せずそのまま上げる。
    一意制約違反は psycopg.errors.UniqueViolation、外部キー違反は psycopg.errors.ForeignKeyViolation
    (どちらも psycopg.IntegrityError の子) で、sqlite3.IntegrityError とは継承関係が無い。
    ∴ sqlite3 だけを捕まえていると 500 になる。
    standalone 形態で psycopg が入っていなくても動くよう、import は守って行う。
    """
    _types: tuple = (sqlite3.IntegrityError,)
    try:
        import psycopg  # noqa: PLC0415

        _types = _types + (psycopg.IntegrityError,)
    except Exception:
        pass
    return _types


# routers/sources.py / collections.py と同じ定義 (共有置き場が無いため router ごとに持つ)。
_INTEGRITY_ERRORS = _integrity_error_types()


def _is_unique_violation(e: BaseException) -> bool:
    """一意制約違反か。psycopg: SQLSTATE 23505 ("duplicate key value violates unique constraint")、
    sqlite3: "UNIQUE constraint failed"。外部キー違反 (23503 / "FOREIGN KEY constraint failed") は False。"""
    if getattr(e, "sqlstate", None) == "23505":
        return True
    _msg = str(e)
    return "UNIQUE constraint failed" in _msg or "duplicate key" in _msg


def _rollback_quietly(conn) -> None:
    """Postgres では失敗した取引が aborted のまま残るので、明示的に巻き戻してから返す
    (finally の close も rollback するが、ここで確実に片付けておく)。"""
    try:
        conn.rollback()
    except Exception:
        pass


def _first_duplicate(ids) -> str | None:
    """同じ id が 2 回以上含まれていれば最初の重複を返す (無ければ None)。"""
    _seen: set = set()
    for _id in ids:
        if _id in _seen:
            return _id
        _seen.add(_id)
    return None


def _first_missing_id(conn, table: str, ids) -> str | None:
    """ids のうち table.id に無い最初のものを返す (全部あれば None)。

    workspace_* 結線表の外部キーに当たる前の存在確認。table は呼び出し側の定数だけを渡す
    (sources / users / guardrail_policies)。DB に書く前に弾けるので、下の INSERT で外部キー違反に
    なるのは同時削除との競合のみ。
    """
    for _id in ids:
        if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (_id,)).fetchone() is None:
            return _id
    return None


def _insert_link(conn, sql: str, ws_id: str, ref_id: str, label: str) -> None:
    """workspace_* 結線表へ 1 行入れ、整合性違反を正しい文言の HTTPException に翻訳する。

    従来は `except Exception` で外部キー違反 (存在しない id) も「重複した…」と返していた
    (q3a D1/D2)、あるいは捕まえておらず 500 だった (D3/D6)。collections.py の PUT file_ids と同じ区別:
      一意制約違反 (23505 / "UNIQUE constraint failed")     → 409 "重複した{label}が指定されています"
      それ以外 (外部キー違反 23503 / "FOREIGN KEY constraint failed") → 400 "存在しない{label}が含まれています"
    重複・存在は呼び出し側が事前に確認しているので、ここへ来るのは同時更新との競合のみ。
    """
    try:
        conn.execute(sql, (ws_id, ref_id))
    except _INTEGRITY_ERRORS as e:
        _rollback_quietly(conn)
        if _is_unique_violation(e):
            raise HTTPException(409, f"重複した{label}が指定されています: {ref_id}") from e
        raise HTTPException(400, f"存在しない{label}が含まれています: {ref_id}") from e


@router.get("/api/workspaces", response_model=None)
def list_workspaces(
    request: Request,
    has_published: bool = False,
    include_archived: bool = False,
    limit: int | None = None,
    offset: int = 0,
    q: str | None = None,
):
    """List workspaces."""
    from server import rows_to_list
    from core.chat_helpers import parse_policy_ids
    from core.auth import _require_authenticated

    user = _require_authenticated(request)
    user_id = user["id"]
    is_admin = user.get("role") == "admin"
    if limit is not None and limit not in (10, 20, 50, 100):
        raise HTTPException(status_code=400, detail="limitは10/20/50/100のいずれかです")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offsetは0以上です")
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (接続リークで書き込みロック残留を防ぐ)
    try:
        where_parts: list[str] = []
        params: list = []
        if not include_archived:
            where_parts.append("archived_at IS NULL")
        if q:
            where_parts.append("name LIKE ?")
            params.append(f"%{q}%")
        if not is_admin:
            where_parts.append("id IN (SELECT workspace_id FROM workspace_users WHERE user_id = ?)")
            params.append(user_id)
        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        total = None
        if limit is not None:
            total = conn.execute(
                f"SELECT COUNT(*) FROM workspaces {where_sql}",
                params,
            ).fetchone()[0]

        pagination_sql = ""
        pagination_params: list = []
        if limit is not None:
            pagination_sql = " LIMIT ? OFFSET ?"
            pagination_params = [limit, offset]
        workspaces = rows_to_list(
            conn.execute(
                f"SELECT * FROM workspaces {where_sql} " f"ORDER BY created_at DESC {pagination_sql}",
                params + pagination_params,
            ).fetchall()
        )
        is_mock = bool(_state.config is not None and _state.config.mock)
        from datetime import timedelta as _td

        seven_days_ago = (datetime.now() - _td(days=7)).isoformat(timespec="seconds")
        for ws in workspaces:
            pids = [
                r["policy_id"]
                for r in conn.execute(
                    "SELECT policy_id FROM workspace_policies WHERE workspace_id = ?",
                    (ws["id"],),
                ).fetchall()
            ]
            if not pids:
                pids = parse_policy_ids(ws.get("guardrail_policy_id"))
            ws["guardrail_policy_ids"] = pids
            ws["source_ids"] = [
                r["source_id"]
                for r in conn.execute(
                    "SELECT source_id FROM workspace_sources WHERE workspace_id = ?", (ws["id"],)
                ).fetchall()
            ]
            ws["user_ids"] = [
                r["user_id"]
                for r in conn.execute("SELECT user_id FROM workspace_users WHERE workspace_id = ?", (ws["id"],)).fetchall()
            ]
            policy_names: list[str] = []
            for pid in pids:
                pr = conn.execute("SELECT name FROM guardrail_policies WHERE id = ?", (pid,)).fetchone()
                if pr and pr["name"]:
                    policy_names.append(pr["name"])
            ws["policy_names"] = policy_names
            try:
                file_count = 0
                if ws["source_ids"]:
                    ph = ",".join("?" for _ in ws["source_ids"])
                    row = conn.execute(
                        f"SELECT COUNT(*) AS c FROM files WHERE source_id IN ({ph})",
                        ws["source_ids"],
                    ).fetchone()
                    file_count = int(row["c"]) if row else 0
                ws["file_count"] = file_count
            except Exception:
                ws["file_count"] = 0
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM collections "
                    "WHERE workspace_id = ? AND status = 'ready' AND archived_at IS NULL",
                    (ws["id"],),
                ).fetchone()
                ws["published_collections"] = int(row["c"]) if row else 0
            except Exception:
                ws["published_collections"] = 0
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM chunks WHERE workspace_id = ? AND excluded = 0",
                    (ws["id"],),
                ).fetchone()
                ws["vectorized_chunks"] = int(row["c"]) if row else 0
            except Exception:
                ws["vectorized_chunks"] = 0
            try:
                # piicount-tierfix (2026-07-09 instr-…-mynumber-and-piicount-…-v1 Stage2):
                # tier 無差別 COUNT は raw+masked の二重計上(約2倍表示)になるため、
                # dashboard/summary (routers/dashboard.py)・チャンク一覧 (本ファイル T1) と
                # 同じ tier='raw' 限定に揃える (zanken-fix1-20260706 の水平展開)。
                # ga-close-v3 PartD D-3: 数え方は guardrail.pii_counts_from_db に集約。
                #   pii_detected 列は raw 側が簡易正規表現の当たりでも 1 になる (伏字 0 件
                #   でも計上される) ため、実際に当てた伏字 (pii_summary) で数える。
                ws["pii_count"] = int(pii_counts_from_db(conn, workspace_id=ws["id"])["pii_chunks"])
            except Exception:
                ws["pii_count"] = 0
            try:
                last_scan_date = None
                last_scan_status = None
                if ws["source_ids"]:
                    ph = ",".join("?" for _ in ws["source_ids"])
                    rows = conn.execute(
                        f"SELECT last_scanned, status FROM sources WHERE id IN ({ph}) "
                        f"ORDER BY last_scanned DESC LIMIT 1",
                        ws["source_ids"],
                    ).fetchall()
                    if rows:
                        last_scan_date = rows[0]["last_scanned"]
                        src_status = rows[0]["status"] or ""
                        if src_status == "completed":
                            last_scan_status = "ok"
                        elif src_status == "failed":
                            last_scan_status = "error"
                        else:
                            last_scan_status = None
                ws["last_scan_date"] = last_scan_date
                ws["last_scan_status"] = last_scan_status
            except Exception:
                ws["last_scan_date"] = None
                ws["last_scan_status"] = None
            auto_sync = False
            try:
                if ws.get("sync_config"):
                    cfg = json.loads(ws["sync_config"]) if isinstance(ws["sync_config"], str) else ws["sync_config"]
                    auto_sync = bool((cfg or {}).get("auto_poll"))
            except Exception:
                auto_sync = False
            ws["auto_sync"] = auto_sync
            try:
                row = conn.execute(
                    """SELECT COUNT(*) AS c FROM messages m
                       JOIN sessions s ON s.id = m.session_id
                       WHERE s.workspace_id = ? AND m.role = 'user'
                         AND m.created_at >= ?""",
                    (ws["id"], seven_days_ago),
                ).fetchone()
                ws["query_count_7d"] = int(row["c"]) if row else 0
            except Exception:
                ws["query_count_7d"] = 0
            ws["user_count"] = len(ws["user_ids"])
            if is_mock:
                if ws["last_scan_status"] is None and ws["last_scan_date"]:
                    ws["last_scan_status"] = "ok"

    finally:
        conn.close()
    if has_published:
        workspaces = [w for w in workspaces if (w.get("published_collections") or 0) > 0]
    if limit is None:
        return workspaces
    return {"items": workspaces, "total": total, "limit": limit, "offset": offset}


@router.get("/api/workspaces/selectable", response_model=None)
def list_workspaces_selectable(request: Request):
    """RAGChat WS選択用軽量エンドポイント。

    Stage R5-fix P1 #14: 未認証で全件返却していたのを停止し 401。
    """
    from core.auth import _require_authenticated

    user = _require_authenticated(request)
    user_id = user["id"]
    is_admin = user.get("role") == "admin"
    conn = get_db()
    try:
        # authz-fix-v1 (P5 露出2): 非admin へ未所属WSの「名前」まで返していたのを停止する。
        # 従来は全WSを返し、絞り込みは画面側の user_accessible フラグ任せだったため、
        # API を直接叩けば所属していない作業場所の名前が読めた (画面側フィルタのみ)。
        # 非admin はサーバ側で所属WSに限定する (admin は全件=広域維持)。
        # 絞り込み規約は /api/workspaces (list_workspaces) と揃える。
        access_sql = ""
        params: list = [user_id, user_id]
        if not is_admin:
            access_sql = (
                " AND EXISTS (SELECT 1 FROM workspace_users wu2 "
                "WHERE wu2.workspace_id = w.id AND wu2.user_id = ?)"
            )
            params.append(user_id)
        rows = conn.execute(
            f"""
            SELECT
                w.id,
                w.name,
                (SELECT COUNT(*) FROM collections c
                 WHERE c.workspace_id = w.id
                   AND c.status = 'ready'
                   AND c.archived_at IS NULL) AS published_collections,
                CASE WHEN ? IS NULL THEN 1
                     WHEN EXISTS (
                         SELECT 1 FROM workspace_users wu
                         WHERE wu.workspace_id = w.id AND wu.user_id = ?
                     ) THEN 1 ELSE 0 END AS user_accessible
            FROM workspaces w
            WHERE w.archived_at IS NULL{access_sql}
            ORDER BY w.created_at DESC
        """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/workspaces/{ws_id}/scan", response_model=None)
def scan_workspace(request: Request, ws_id: str):
    """WS-card-v2: WSに紐づく全Sourceをまとめてスキャンする。"""
    from server import _start_scan, logger

    _require_admin(request)
    conn = get_db()
    try:
        ws = conn.execute("SELECT id FROM workspaces WHERE id = ? AND archived_at IS NULL", (ws_id,)).fetchone()
        if not ws:
            raise HTTPException(404, "Workspace not found")
        rows = conn.execute("SELECT source_id FROM workspace_sources WHERE workspace_id = ?", (ws_id,)).fetchall()
        source_ids = [r["source_id"] for r in rows]
    finally:
        conn.close()
    if not source_ids:
        return {"ok": True, "scanned": 0, "message": "Sourceが紐づいていません"}
    for sid in source_ids:
        try:
            # DD-CYN-0116 Q-1: 走査も列を通す (publish と対称)。
            _start_scan(sid)
        except Exception as e:
            logger.warning(f"ws-scan: {sid}: {e}")
    return {"ok": True, "scanned": len(source_ids), "message": f"{len(source_ids)} Source のスキャンを開始しました"}


@router.post("/api/workspaces", response_model=None)
async def create_workspace(request: Request):
    from server import _do_scan

    _require_admin(request)
    body = await parse_body_pydantic(request)
    name = body.get("name")
    source_ids = body.get("source_ids", [])
    user_ids = body.get("user_ids", ["user-admin"])
    policy_ids = body.get("policy_ids") or ([body["policy_id"]] if body.get("policy_id") else [])
    # fix-all-v2: 取り込みモード (pdf_mode) を WS 作成時に受領する。patch_workspace と同じ許容値。
    pdf_mode = (body.get("pdf_mode") or "fast")
    if pdf_mode not in ("fast", "quality", "vision"):
        raise HTTPException(400, "pdf_mode は fast / quality / vision のいずれかを指定してください")

    if not name or not isinstance(name, str):
        raise HTTPException(400, "name is required")
    name = name.strip()
    if len(name) == 0:
        raise HTTPException(400, "name must not be blank")
    if len(name) > 255:
        raise HTTPException(400, "name must be 255 chars or less")
    _forbidden = ["<script", "javascript:", "onerror=", "onload=", "'; drop", "; drop ", "union select", "/*", "*/"]
    _lower = name.lower()
    for _f in _forbidden:
        if _f in _lower:
            raise HTTPException(400, f"name contains forbidden pattern: {_f}")

    ws_id = new_id()
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        # fix064 H: 同名 workspace 禁止 (DB UNIQUE 制約に加えて API 側で 409 を返す)
        _existing = conn.execute("SELECT id FROM workspaces WHERE name = ?", (name,)).fetchone()
        if _existing is not None:
            raise HTTPException(409, f"workspace name already exists: {name}")
        # q3a D3: 結線先の存在確認。INSERT OR IGNORE (Postgres では ON CONFLICT DO NOTHING) は主キー重複は
        # 吸収するが外部キー違反は吸収しないので、存在しない id を渡されると 500 になっていた。
        # DB に書く前に 400 で返す (同じ id の重複指定は従来どおり OR IGNORE で黙って畳む)。
        for _table, _ids, _label in (
            ("guardrail_policies", [p for p in policy_ids if p], "policy_id"),
            ("sources", source_ids, "source_id"),
            ("users", user_ids, "user_id"),
        ):
            _missing = _first_missing_id(conn, _table, _ids)
            if _missing is not None:
                raise HTTPException(400, f"存在しない{_label}が含まれています: {_missing}")
        try:
            conn.execute("INSERT INTO workspaces (id, name) VALUES (?, ?)", (ws_id, name))
        except _INTEGRITY_ERRORS as _e:
            # fix064 H: 並行 INSERT で UNIQUE 違反が起きた場合は 409 (5xx 返却を回避)。
            # q3a D5: 文字列 "UNIQUE constraint failed" は SQLite 専用で、Postgres (psycopg) の
            # "duplicate key value violates unique constraint" には当たらず 500 になっていた。
            # 型で捕まえ、SQLSTATE 23505 / 両方の文言で判定する。
            _rollback_quietly(conn)
            if _is_unique_violation(_e):
                raise HTTPException(409, f"workspace name already exists: {name}") from _e
            raise
        for pid in policy_ids:
            if pid:
                _insert_link(
                    conn,
                    "INSERT OR IGNORE INTO workspace_policies (workspace_id, policy_id) VALUES (?, ?)",
                    ws_id,
                    pid,
                    "policy_id",
                )
        for sid in source_ids:
            _insert_link(
                conn,
                "INSERT OR IGNORE INTO workspace_sources (workspace_id, source_id) VALUES (?, ?)",
                ws_id,
                sid,
                "source_id",
            )
        for uid in user_ids:
            _insert_link(
                conn,
                "INSERT OR IGNORE INTO workspace_users (workspace_id, user_id) VALUES (?, ?)",
                ws_id,
                uid,
                "user_id",
            )
        # fix-all-v2: 作成時の pdf_mode を acl_config に保存 (patch_workspace と同じ格納先)
        conn.execute(
            "UPDATE workspaces SET acl_config = ? WHERE id = ?",
            (json.dumps({"pdf_mode": pdf_mode}, ensure_ascii=False), ws_id),
        )
        _log_audit(conn, "workspace_created", ws_id, name)
        conn.commit()
    finally:
        conn.close()

    for sid in source_ids:
        conn = get_db()
        # connleak-fix-20260709: 例外時も必ず close する
        try:
            source = conn.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone()
        finally:
            conn.close()
        if source and source["status"] != "completed":
            _do_scan(sid)

    return {"id": ws_id, "name": name, "guardrail_policy_ids": policy_ids}


@router.get("/api/workspaces/{ws_id}", response_model=None)
def get_workspace_by_id(request: Request, ws_id: str):
    """PHASE 0-C: Workspace 単体取得"""
    _require_admin(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (接続リークで書き込みロック残留を防ぐ)
    try:
        ws = conn.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        if not ws:
            raise HTTPException(404, "Workspace not found")
        out = dict(ws)
        out["source_ids"] = [
            r["source_id"]
            for r in conn.execute("SELECT source_id FROM workspace_sources WHERE workspace_id = ?", (ws_id,)).fetchall()
        ]
        out["policy_ids"] = [
            r["policy_id"]
            for r in conn.execute("SELECT policy_id FROM workspace_policies WHERE workspace_id = ?", (ws_id,)).fetchall()
        ]
    finally:
        conn.close()
    return out


@router.put("/api/workspaces/{ws_id}", response_model=None)
async def update_workspace_legacy(ws_id: str, request: Request):
    """DD-CYN-0116 F-4: 中身は PATCH 側へ一本化した。

    従来この受け口だけが source_ids / user_ids を扱い、しかも監査を残していなかった。
    古い呼び出しのために道は残すが、扱いは PATCH と同一にする (監査も残る)。
    """
    return await patch_workspace(ws_id, request)


@router.patch("/api/workspaces/{ws_id}", response_model=None)
async def patch_workspace(ws_id: str, request: Request):
    """P4-6 / P4-11: WS の name / description / sync_config を更新する。"""
    _require_admin(request)
    body = await parse_body_pydantic(request)
    name = body.get("name")
    description = body.get("description")
    sync_config = body.get("sync_config")
    pdf_mode = body.get("pdf_mode")
    # DD-CYN-0116 F-4: 同じ道 (/api/workspaces/{ws_id}) に PUT と PATCH の2つの受け口があり、
    # 扱う項目が割れていた。PUT だけが source_ids / user_ids を持ち、画面は PATCH しか
    # 叩かない (frontend/js/workspace.js)。∴ 既にある まとまり の利用者と取り込み元は
    # 画面から一度も変えられなかった。さらに PUT は監査を残さないため、監査を避けて
    # 利用者を差し替えられる状態だった。両方を PATCH 側へ統合する。
    source_ids = body.get("source_ids")
    user_ids = body.get("user_ids")
    if (
        name is None
        and description is None
        and sync_config is None
        and pdf_mode is None
        and source_ids is None
        and user_ids is None
    ):
        raise HTTPException(
            400, "name / description / sync_config / pdf_mode / source_ids / user_ids のいずれかが必要です"
        )
    if source_ids is not None and not isinstance(source_ids, list):
        raise HTTPException(400, "source_ids は配列で指定してください")
    if user_ids is not None and not isinstance(user_ids, list):
        raise HTTPException(400, "user_ids は配列で指定してください")
    if pdf_mode is not None and pdf_mode not in ("fast", "quality", "vision"):
        raise HTTPException(400, "pdf_mode は fast / quality / vision のいずれかを指定してください")
    if name is not None and not str(name).strip():
        raise HTTPException(400, "name は空にできません")

    sync_str = None
    if sync_config is not None:
        if not isinstance(sync_config, dict):
            raise HTTPException(400, "sync_config はオブジェクト形式で指定してください")
        if "poll_interval_seconds" in sync_config and sync_config["poll_interval_seconds"] is not None:
            try:
                interval = int(sync_config["poll_interval_seconds"])
            except (ValueError, TypeError):
                raise HTTPException(400, "poll_interval_seconds は整数で指定してください")
            if not (30 <= interval <= 2592000):
                raise HTTPException(
                    400,
                    "poll_interval_seconds は 30秒〜2592000秒（30日）の範囲で指定してください",
                )
            sync_config["poll_interval_seconds"] = interval

    conn = get_db()
    try:
        ws = conn.execute("SELECT id, sync_config FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        if not ws:
            raise HTTPException(404, "Workspace not found")
        now_iso = datetime.now().isoformat(timespec="seconds")
        if name is not None:
            _new_name = str(name).strip()
            # q3a D4: 同名 workspace への改名は workspaces.name の UNIQUE 制約に当たって 500 になっていた
            # (画面の改名 frontend/js/workspace.js が踏む経路)。作成時 (fix064 H) と同じ文言で 409 を返す。
            _clash = conn.execute(
                "SELECT id FROM workspaces WHERE name = ? AND id != ?", (_new_name, ws_id)
            ).fetchone()
            if _clash is not None:
                raise HTTPException(409, f"workspace name already exists: {_new_name}")
            try:
                conn.execute(
                    "UPDATE workspaces SET name = ?, updated_at = ? WHERE id = ?", (_new_name, now_iso, ws_id)
                )
            except _INTEGRITY_ERRORS as e:
                # 上で確認しているので、ここへ来る一意制約違反は同時作成・改名との競合のみ。
                _rollback_quietly(conn)
                if _is_unique_violation(e):
                    raise HTTPException(409, f"workspace name already exists: {_new_name}") from e
                raise
        if description is not None:
            conn.execute(
                "UPDATE workspaces SET description = ?, updated_at = ? WHERE id = ?",
                (description or "", now_iso, ws_id),
            )
        if sync_config is not None:
            existing = {}
            if ws["sync_config"]:
                try:
                    existing = json.loads(ws["sync_config"])
                except Exception:
                    existing = {}
            merged = {**existing, **sync_config}
            sync_str = json.dumps(merged, ensure_ascii=False)
            conn.execute(
                "UPDATE workspaces SET sync_config = ?, updated_at = ? WHERE id = ?", (sync_str, now_iso, ws_id)
            )
        if pdf_mode is not None:
            existing_acl = {}
            acl_row = conn.execute("SELECT acl_config FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
            if acl_row and acl_row["acl_config"]:
                try:
                    existing_acl = json.loads(acl_row["acl_config"])
                except Exception:
                    existing_acl = {}
            existing_acl["pdf_mode"] = pdf_mode
            conn.execute(
                "UPDATE workspaces SET acl_config = ?, updated_at = ? WHERE id = ?",
                (json.dumps(existing_acl, ensure_ascii=False), now_iso, ws_id),
            )
        # DD-CYN-0116 F-4: PUT から移した2項目。
        # q3a D1/D2: 従来は `except Exception` で捕まえ、外部キー違反 (存在しない source_id / user_id) も
        # 「重複した…」と返していた。同じ id の重複指定は DB に触る前に検出して従来どおり 409、
        # 存在しない id は 400 "存在しない…" で返す。INSERT で残る整合性違反 (同時更新との競合) は
        # _insert_link が 23505 / "UNIQUE constraint failed" と外部キー違反を区別して翻訳する
        # (sqlite3 / psycopg の両方の型を捕まえる)。
        if source_ids is not None:
            _dup = _first_duplicate(source_ids)
            if _dup is not None:
                raise HTTPException(409, f"重複したsource_idが指定されています: {_dup}")
            _missing = _first_missing_id(conn, "sources", source_ids)
            if _missing is not None:
                raise HTTPException(400, f"存在しないsource_idが含まれています: {_missing}")
            conn.execute("DELETE FROM workspace_sources WHERE workspace_id = ?", (ws_id,))
            for sid in source_ids:
                _insert_link(
                    conn,
                    "INSERT INTO workspace_sources (workspace_id, source_id) VALUES (?, ?)",
                    ws_id,
                    sid,
                    "source_id",
                )
        if user_ids is not None:
            _dup = _first_duplicate(user_ids)
            if _dup is not None:
                raise HTTPException(409, f"重複したuser_idが指定されています: {_dup}")
            _missing = _first_missing_id(conn, "users", user_ids)
            if _missing is not None:
                raise HTTPException(400, f"存在しないuser_idが含まれています: {_missing}")
            conn.execute("DELETE FROM workspace_users WHERE workspace_id = ?", (ws_id,))
            for uid in user_ids:
                _insert_link(
                    conn,
                    "INSERT INTO workspace_users (workspace_id, user_id) VALUES (?, ?)",
                    ws_id,
                    uid,
                    "user_id",
                )
        _log_audit(
            conn,
            "workspace_updated",
            ws_id,
            ",".join(
                k
                for k in ("name", "description", "sync_config", "pdf_mode", "source_ids", "user_ids")
                if k in body
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
    finally:
        conn.close()
    return dict(row)


@router.get("/api/workspaces/{ws_id}/sync-config", response_model=None)
def get_workspace_sync_config(request: Request, ws_id: str):
    """P4-11: WSのポーリング設定を返す。"""
    _require_admin(request)
    from core.config import get_sync_config as _gsc

    conn = get_db()
    try:
        ws = conn.execute("SELECT sync_config FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        if not ws:
            raise HTTPException(404, "Workspace not found")
        global_cfg = _gsc()
        ws_cfg = {}
        if ws["sync_config"]:
            try:
                ws_cfg = json.loads(ws["sync_config"])
            except Exception:
                ws_cfg = {}
        last_scan = conn.execute(
            "SELECT timestamp FROM audit_logs WHERE action = 'auto_scan_complete' "
            "AND target = ? ORDER BY timestamp DESC LIMIT 1",
            (ws_id,),
        ).fetchone()
        last_scan_at = last_scan["timestamp"] if last_scan else None
    finally:
        conn.close()
    effective = {**global_cfg, **ws_cfg}
    next_at = None
    if last_scan_at and effective.get("auto_poll"):
        try:
            from datetime import timedelta

            dt = datetime.fromisoformat(last_scan_at)
            next_at = (dt + timedelta(seconds=int(effective.get("poll_interval_seconds", 3600)))).isoformat(
                timespec="seconds"
            )
        except Exception:
            pass
    return {
        "workspace_id": ws_id,
        "global_defaults": global_cfg,
        "workspace_overrides": ws_cfg,
        "effective": effective,
        "last_scan_at": last_scan_at,
        "next_scan_at": next_at,
    }


@router.patch("/api/workspaces/{ws_id}/sync-config", response_model=None)
async def update_workspace_sync_config(ws_id: str, request: Request):
    """P4-11: WSのポーリング設定だけを更新する。"""
    _require_admin(request)
    body = await parse_body_pydantic(request)
    if "poll_interval_seconds" in body and body["poll_interval_seconds"] is not None:
        try:
            interval = int(body["poll_interval_seconds"])
        except (ValueError, TypeError):
            raise HTTPException(400, "poll_interval_seconds は整数で指定してください")
        if not (30 <= interval <= 2592000):
            raise HTTPException(400, "poll_interval_seconds は 30秒〜2592000秒（30日）の範囲で指定してください")
        body["poll_interval_seconds"] = interval

    conn = get_db()
    try:
        ws = conn.execute("SELECT id, sync_config FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        if not ws:
            raise HTTPException(404, "Workspace not found")
        existing = {}
        if ws["sync_config"]:
            try:
                existing = json.loads(ws["sync_config"])
            except Exception:
                existing = {}
        merged = {**existing, **body}
        now_iso = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "UPDATE workspaces SET sync_config = ?, updated_at = ? WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False), now_iso, ws_id),
        )
        _log_audit(conn, "ws_sync_config_updated", ws_id, ",".join(k for k in body.keys()))
        conn.commit()
    finally:
        conn.close()
    return {"workspace_id": ws_id, "sync_config": merged}


@router.delete("/api/workspaces/{ws_id}", response_model=None)
def delete_workspace(ws_id: str, request: Request):
    _require_admin(request)
    from server import _purge_collections_for_workspace, _purge_chunks_for_source

    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        # cascade-source-cleanup (key-vector-fix-20260721): この WS の collection 群が使って
        # いた source 候補を控え、削除後にどこの collection からも使われていないものだけ
        # 連鎖削除する (他 WS の collection が使う source は残す)。
        _cand_sources = [
            r["source_id"]
            for r in conn.execute(
                "SELECT DISTINCT f.source_id FROM collections c "
                "JOIN collection_files cf ON cf.collection_id = c.id "
                "JOIN files f ON f.id = cf.file_id WHERE c.workspace_id = ?",
                (ws_id,),
            ).fetchall()
        ]
        _purge_collections_for_workspace(conn, ws_id)
        conn.execute("DELETE FROM workspaces WHERE id = ?", (ws_id,))
        _log_audit(conn, "workspace_deleted", ws_id)
        for _sid in _cand_sources:
            _still = conn.execute(
                "SELECT COUNT(*) AS n FROM collection_files cf "
                "JOIN files f ON f.id = cf.file_id WHERE f.source_id = ?",
                (_sid,),
            ).fetchone()["n"]
            if _still == 0:
                _purge_chunks_for_source(conn, _sid)
                conn.execute("DELETE FROM sources WHERE id = ?", (_sid,))
                _log_audit(conn, "source_cascade_deleted", _sid)
        conn.commit()
    finally:
        conn.close()
    # fix-v3 (A2-F2): 削除コミット後に BM25 索引を再構築する。WS の chunks は全削除済みのため
    # rebuild_bm25_from_db は 0 件 → build_bm25_index が索引を pop し in-memory 索引を一掃する。
    # 従来は stale 索引が残り削除済みチャンクが RAG 回答に残留していた (delete_collection と同型)。
    try:
        from rag import rebuild_bm25_from_db
        rebuild_bm25_from_db(ws_id)
    except Exception:
        pass
    return {"ok": True}


@router.patch("/api/workspaces/{ws_id}/archive", response_model=None)
def archive_workspace(ws_id: str, request: Request):
    from core.auth import _require_admin

    user = _require_admin(request)
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        if not row:
            raise api_error("NOT_FOUND", "workspace not found", status=404)
        conn.execute(
            "UPDATE workspaces SET archived_at = ?, archived_by = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), user["id"], ws_id),
        )
        _log_audit(conn, "workspace_archived", ws_id, f"by={user['id']}")
        conn.commit()
    finally:
        conn.close()
    log_admin_change(user["id"], "workspace", ws_id, "archive")
    return {"id": ws_id, "status": "archived"}


@router.patch("/api/workspaces/{ws_id}/unarchive", response_model=None)
def unarchive_workspace(ws_id: str, request: Request):
    from core.auth import _require_admin

    user = _require_admin(request)
    conn = get_db()
    try:
        conn.execute(
            "UPDATE workspaces SET archived_at = NULL, archived_by = NULL WHERE id = ?",
            (ws_id,),
        )
        _log_audit(conn, "workspace_unarchived", ws_id, f"by={user['id']}")
        conn.commit()
    finally:
        conn.close()
    log_admin_change(user["id"], "workspace", ws_id, "unarchive")
    return {"id": ws_id, "status": "unarchived"}


@router.get("/api/workspaces/{workspace_id}/chunks", response_model=None)
def get_workspace_chunks(
    request: Request,
    workspace_id: str,
    filter: str = "all",
    limit: int = 50,
    offset: int = 0,
):
    """Workspace内のチャンク一覧をメタデータ付きで返す。"""
    user = _require_authenticated(request)
    # DD-CYN-0136 2-6: 鍵 scope の強制 (chunk 本文の直接閲覧口)
    # agentK K-2 (q5 B2): 従来は作業場所の軸しか効かず、scope.collections を持つ鍵でも
    # WS 内の全コレクションのチャンクが返っていた。返り値 (実効コレクション) が非空なら
    # 集計・一覧の全クエリに ch.collection_id IN (...) を足して範囲内に閉じる。
    from core.auth import enforce_api_key_scope as _eaks_ch
    _scope_cols = _eaks_ch(request, workspace_id)
    _col_clause = ""
    _col_params: tuple = ()
    if _scope_cols:
        _col_clause = " AND ch.collection_id IN (" + ",".join("?" for _ in _scope_cols) + ")"
        _col_params = tuple(_scope_cols)
    if offset < 0:
        raise HTTPException(status_code=400, detail="offsetは0以上です")
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (接続リークで書き込みロック残留を防ぐ)
    try:
        # fix-chunks-acl-leak-v1: 越境raw-PIIリーク修正
        #   (A) 非adminは workspace_users 未割当WSへのアクセスを403で拒否
        #       (list_workspaces / sessions.py のメンバーシップ検査と同じパターン)
        #   (B) tier はロール駆動 (admin=raw / その他=masked) とし raw 本文の漏洩を防止
        #       (rag.tier_for_role は chat.py が検索・出力マスクで使う単一の決定関数)
        from rag import tier_for_role
        if user.get("role") != "admin":
            _member = conn.execute(
                "SELECT 1 FROM workspace_users WHERE workspace_id = ? AND user_id = ?",
                (workspace_id, user["id"]),
            ).fetchone()
            if not _member:
                raise HTTPException(status_code=403, detail="このワークスペースへのアクセス権がありません")
        _tier = tier_for_role(user.get("role") or "viewer")
        # sokessan-fix-a8-20260711: チャンク本文の直接閲覧 API を監査に残す。
        # chat 経由の chat_retrieved はあるが、本 API での直接閲覧は従来無記録だった。
        try:
            _log_audit(
                conn,
                "workspace_chunks_viewed",
                workspace_id,
                detail=f"filter={filter}",
                ip_address=(request.client.host if request.client else None),
                user_id=user.get("id"),
                tier=_tier,
            )
        except Exception:
            pass
        # rawmode-partE-20260709: raw_only=1 コレクション (masked 層を生成しない取り込みモード) の
        # 安全性メモ。masked 行が DB に存在しないため、非admin (_tier='masked') は本EPで該当
        # コレクションのチャンクが 0 件になるだけで、raw 本文は構造的に返らない。
        # 下の集計・一覧の両クエリとも WHERE ch.tier = ? で tier を厳格に束縛しており、
        # raw へのフォールバック経路は無い (tier_for_role は admin 以外を常に 'masked' とする
        # 厳格判定)。raw_only 列を参照する追加ガードは、旧スキーマ (列未追加のDB) で
        # クラッシュする退行リスクの方が大きいため置かない。
        filter_clause = ""
        if filter == "pii":
            # ga-close-v3 PartD D-3: 絞り込みも数え方に合わせる。旧 ch.pii_detected = 1 は
            #   masked 層 (viewer が見る層) では伏字後の再判定なのでほぼ常に 0 で、
            #   「伏字が効いているのに 1 件も出ない」一覧になっていた。
            filter_clause = " AND ch.pii_summary IS NOT NULL AND ch.pii_summary <> '' AND ch.pii_summary <> '{}'"
        elif filter == "excluded":
            filter_clause = " AND ch.excluded = 1"

        # T1 (P0-B F1 案1): chunks 集計・一覧は tier='raw' のみに限定。
        # raw + masked を同時に数えると同一チャンクが二重表示・件数不一致を起こすため。
        # （masked 行は __raw に対応した派生で、管理者画面でも生の raw のみ見ればよい。）
        # ga-close-v3 PartD D-3: 伏字件数 (pii) は guardrail.pii_counts_from_summaries で
        #   数える。pii_summary は raw 行と masked 行に同じ値が入るので、閲覧者が masked
        #   層を見ていても要約・公開履歴と同じ数になる (旧 pii_detected 列は masked 層で
        #   伏字後の再判定になり、実測で 2128 対 18 と食い違っていた)。
        summary_row = conn.execute(
            f"""
            SELECT
              COUNT(*) AS total,
              COALESCE(SUM(CASE WHEN excluded=1 THEN 1 ELSE 0 END), 0) AS excluded_cnt
            FROM chunks ch
            WHERE ch.workspace_id = ? AND ch.tier = ?{filter_clause}{_col_clause}
        """,
            (workspace_id, _tier, *_col_params),
        ).fetchone()
        _pii_counts = pii_counts_from_summaries(
            r[0]
            for r in conn.execute(
                f"SELECT ch.pii_summary FROM chunks ch "
                f"WHERE ch.workspace_id = ? AND ch.tier = ?{filter_clause}{_col_clause}",
                (workspace_id, _tier, *_col_params),
            ).fetchall()
        )

        # vault-enc: SQL の SUBSTR(content,1,100) を外し、Python 側で dec_raw 後に切詰める。
        # raw 行の content は 'enc:...' 形式で保存されているため、SQL レベルの先頭 100 文字では
        # 復号できないため。masked / 旧平文の content は dec_raw を素通しする (冪等)。
        rows = conn.execute(
            f"""
            SELECT ch.chunk_id, ch.source_doc, ch.page_hint, ch.char_count,
                   ch.pii_detected, ch.excluded,
                   ch.content AS content,
                   ch.collection_id,
                   ch.pii_summary,
                   c.allowed_roles_json AS allowed_roles_json
            FROM chunks ch
            LEFT JOIN collections c ON c.id = ch.collection_id
            WHERE ch.workspace_id = ? AND ch.tier = ?{filter_clause}{_col_clause}
            ORDER BY ch.source_doc, ch.chunk_id
            LIMIT ? OFFSET ?
        """,
            (workspace_id, _tier, *_col_params, limit, offset),
        ).fetchall()
    finally:
        conn.close()

    chunks = []
    for r in rows:
        ar = None
        if r["allowed_roles_json"]:
            try:
                ar = json.loads(r["allowed_roles_json"])
            except Exception:
                ar = None
        _ps = None
        if r["pii_summary"]:
            try:
                _ps = json.loads(r["pii_summary"])
            except Exception:
                _ps = None
        # vault-enc: 復号してから先頭 100 文字を preview として返す (旧 API 互換)。
        _decoded = dec_raw(r["content"] or "")
        _preview = _decoded[:100]
        chunks.append(
            {
                "chunk_id": r["chunk_id"],
                "source_doc": r["source_doc"] or "",
                "page_hint": r["page_hint"],
                "char_count": r["char_count"] or 0,
                "pii_detected": bool(r["pii_detected"]),
                "excluded": bool(r["excluded"]),
                "preview": _preview,
                "allowed_roles": ar,
                "collection_id": r["collection_id"],
                "pii_summary": _ps,
            }
        )
    summary = {
        "total": int(summary_row["total"] or 0),
        "pii": int(_pii_counts["pii_chunks"]),
        "pii_spans": int(_pii_counts["pii_spans"]),
        "pii_labels": _pii_counts["labels"],
        "excluded": int(summary_row["excluded_cnt"] or 0),
        "acl_restricted": sum(
            1 for c in chunks if isinstance(c.get("allowed_roles"), list) and "viewer" not in c["allowed_roles"]
        ),
    }
    return {
        "workspace_id": workspace_id,
        "filter": filter,
        "total_in_filter": summary["total"],
        "total": summary["total"],
        "limit": limit,
        "offset": offset,
        "summary": summary,
        "chunks": chunks,
        "items": chunks,
    }


@router.get("/api/workspaces/{workspace_id}/publish-history", response_model=None)
def get_publish_history(request: Request, workspace_id: str, limit: int = 10):
    """Workspace のPublish履歴を新しい順で返す。"""
    # fix-publish-history-acl-v1: 兄弟エンドポイント (GET /api/workspaces/{id} ・
    #   /lineage ・/export) と同じ workspace-access ガードに統一。
    #   _require_authenticated だけでは viewer が非所有 WS の集計メタ
    #   (doc/chunk/pii/excluded 件数・タイムスタンプ等) を 200 で取得できる
    #   IDOR/BOLA だったため、_require_admin に変更し admin_required を監査・403 を返す。
    _require_admin(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (接続リークで書き込みロック残留を防ぐ)
    try:
        rows = conn.execute(
            """
            SELECT timestamp, doc_count, chunk_count, pii_count,
                   excluded_count, avg_chunk_chars, elapsed_seconds
            FROM publish_history
            WHERE workspace_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return {
        "workspace_id": workspace_id,
        "history": [
            {
                "timestamp": r["timestamp"],
                "doc_count": r["doc_count"],
                "chunk_count": r["chunk_count"],
                "avg_chunk_chars": round(r["avg_chunk_chars"] or 0, 1),
                "pii_count": r["pii_count"],
                "excluded_count": r["excluded_count"],
                "elapsed_seconds": round(r["elapsed_seconds"] or 0, 1),
            }
            for r in rows
        ],
    }


@router.put("/api/workspaces/{ws_id}/policy", response_model=None)
async def assign_policy(ws_id: str, request: Request):
    _require_admin(request)
    body = await parse_body_pydantic(request)
    policy_ids = body.get("policy_ids")
    if policy_ids is None:
        single = body.get("policy_id")
        policy_ids = [single] if single else []
    policy_ids = [p for p in policy_ids if p]
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        ws = conn.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
        if not ws:
            raise HTTPException(404, "Workspace not found")
        # q3a D6: 存在しない policy_id は INSERT OR IGNORE でも外部キー違反 → 500 になっていた
        # (OR IGNORE は主キー重複しか吸収しない)。DELETE の前に確認して 400 で返す。
        _missing = _first_missing_id(conn, "guardrail_policies", policy_ids)
        if _missing is not None:
            raise HTTPException(400, f"存在しないpolicy_idが含まれています: {_missing}")
        conn.execute("DELETE FROM workspace_policies WHERE workspace_id = ?", (ws_id,))
        for pid in policy_ids:
            _insert_link(
                conn,
                "INSERT OR IGNORE INTO workspace_policies (workspace_id, policy_id) VALUES (?, ?)",
                ws_id,
                pid,
                "policy_id",
            )
        conn.execute("UPDATE workspaces SET guardrail_policy_id = NULL WHERE id = ?", (ws_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "workspace_id": ws_id, "policy_ids": policy_ids}


@router.get("/api/workspaces/{workspace_id}/lineage", response_model=None)
def get_workspace_lineage(request: Request, workspace_id: str, limit: int = 200):
    """Workspace 配下の document_lineage 一覧を返す (updated_at DESC)。"""
    _require_admin(request)
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM document_lineage WHERE workspace_id = ?
               ORDER BY updated_at DESC LIMIT ?""",
            (workspace_id, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/api/workspaces/{workspace_id}/lineage/diff", response_model=None)
async def post_lineage_diff(workspace_id: str, request: Request):
    """body.file_hashes ({path: sha256}) を受け取り new/changed/unchanged を返す。"""
    from server import get_changed_files

    _require_admin(request)
    body = await parse_body_pydantic(request)
    file_hashes = body.get("file_hashes") or {}
    if not isinstance(file_hashes, dict):
        raise HTTPException(400, "file_hashes は {path: hash} の辞書を指定してください")
    conn = get_db()
    try:
        return get_changed_files(conn, workspace_id, file_hashes)
    finally:
        conn.close()


@router.get("/api/workspaces/{workspace_id}/export", response_model=None)
def export_workspace(request: Request, workspace_id: str):
    """Workspace 配下の Collection / Guardrail / Source 設定を ZIP で返す。"""
    _require_admin(request)
    conn = get_db()
    try:
        ws = conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if not ws:
            conn.close()
            raise HTTPException(404, "Workspace not found")
        ws_dict = dict(ws)
        collections = [
            dict(r)
            for r in conn.execute("SELECT * FROM collections WHERE workspace_id = ?", (workspace_id,)).fetchall()
        ]
        for col in collections:
            cf = conn.execute(
                "SELECT file_id FROM collection_files WHERE collection_id = ?",
                (col["id"],),
            ).fetchall()
            col["file_ids"] = [r["file_id"] for r in cf]
        ws_sources = [
            r["source_id"]
            for r in conn.execute(
                "SELECT source_id FROM workspace_sources WHERE workspace_id = ?", (workspace_id,)
            ).fetchall()
        ]
        ws_users = [
            r["user_id"]
            for r in conn.execute(
                "SELECT user_id FROM workspace_users WHERE workspace_id = ?", (workspace_id,)
            ).fetchall()
        ]
        ws_policies = [
            r["policy_id"]
            for r in conn.execute(
                "SELECT policy_id FROM workspace_policies WHERE workspace_id = ?", (workspace_id,)
            ).fetchall()
        ]
        sources_snapshot = []
        if ws_sources:
            ph = ",".join("?" for _ in ws_sources)
            for r in conn.execute(f"SELECT * FROM sources WHERE id IN ({ph})", ws_sources).fetchall():
                sources_snapshot.append(dict(r))
        files_snapshot = []
        if ws_sources:
            ph = ",".join("?" for _ in ws_sources)
            for r in conn.execute(
                f"SELECT * FROM files WHERE source_id IN ({ph})",
                ws_sources,
            ).fetchall():
                files_snapshot.append(dict(r))
        policies_snapshot = []
        if ws_policies:
            ph = ",".join("?" for _ in ws_policies)
            for r in conn.execute(
                f"SELECT * FROM guardrail_policies WHERE id IN ({ph})",
                ws_policies,
            ).fetchall():
                policies_snapshot.append(dict(r))
    finally:
        conn.close()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("workspace.json", json.dumps(ws_dict, ensure_ascii=False, indent=2))
        zf.writestr("collections.json", json.dumps(collections, ensure_ascii=False, indent=2))
        zf.writestr(
            "links.json",
            json.dumps(
                {"source_ids": ws_sources, "user_ids": ws_users, "policy_ids": ws_policies},
                ensure_ascii=False,
                indent=2,
            ),
        )
        zf.writestr("sources.json", json.dumps(sources_snapshot, ensure_ascii=False, indent=2))
        zf.writestr("files.json", json.dumps(files_snapshot, ensure_ascii=False, indent=2))
        zf.writestr("guardrail_policies.json", json.dumps(policies_snapshot, ensure_ascii=False, indent=2))
        zf.writestr(
            "_meta.json",
            json.dumps(
                {
                    "export_version": "v1",
                    "workspace_id": workspace_id,
                    "exported_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=workspace_{workspace_id}.zip"},
    )

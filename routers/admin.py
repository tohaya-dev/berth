"""Admin endpoints (/api/admin/*)."""

from __future__ import annotations

import csv
import io
import json
import json as _json_mod
import os
import shutil as _shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from db import get_db, hash_password, new_id
from core.auth import _require_admin
from core.audit import _log_audit
from core.errors import api_error

router = APIRouter(tags=["admin"])


def _integrity_error_types() -> tuple:
    """一意制約違反として扱う例外の型 (SQLite / Postgres の両方)。

    Postgres 背骨 (core/relational_pg.py) は psycopg 3 の例外を翻訳せずそのまま上げる。
    一意制約違反は psycopg.errors.UniqueViolation (psycopg.IntegrityError の子) で、
    sqlite3.IntegrityError とは継承関係が無い。∴ sqlite3 だけを捕まえていると 500 になる。
    standalone 形態で psycopg が入っていなくても動くよう、import は守って行う。
    """
    _types: tuple = (sqlite3.IntegrityError,)
    try:
        import psycopg  # noqa: PLC0415

        _types = _types + (psycopg.IntegrityError,)
    except Exception:
        pass
    return _types


# routers/sources.py と同じ定義 (共有置き場が無いため router ごとに持つ)。
# 外部キー違反 (psycopg.errors.ForeignKeyViolation) も psycopg.IntegrityError の子。
_INTEGRITY_ERRORS = _integrity_error_types()


# ─── /api/admin/change-log ──────────────────────────────────


@router.get("/api/admin/change-log", response_model=None)
def list_admin_change_log(request: Request, limit: int = 50):
    """管理変更ログ一覧 (admin のみ)."""
    _require_admin(request)
    limit = max(1, min(int(limit or 50), 500))
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, timestamp, changed_by, entity_type, entity_id, action, "
            "before_value, after_value "
            "FROM admin_change_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for jk in ("before_value", "after_value"):
            v = d.get(jk)
            if v:
                try:
                    d[jk] = json.loads(v)
                except Exception:
                    pass
        out.append(d)
    return {"changes": out}


# ─── /api/admin/users (CRUD + reset-password) ───────────────


@router.get("/api/admin/users", response_model=None)
def admin_list_users(request: Request, include_inactive: bool = True):
    """C-4 (2): include_inactive=false で有効 (is_active=1) の利用者のみ返す。既定 true=現行互換。"""
    _require_admin(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (接続リークで書き込みロック残留を防ぐ)
    try:
        _where = "" if include_inactive else "WHERE COALESCE(is_active, 1) = 1 "
        rows = conn.execute(
            "SELECT id, username, display_name, name, role, is_active, created_at, updated_at "
            f"FROM users {_where}ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@router.post("/api/admin/users", response_model=None)
async def admin_create_user(request: Request):
    from core.roles import VALID_ROLES

    _require_admin(request)
    body = await parse_body_pydantic(request)
    username = (body.get("username") or "").strip()
    display_name = (body.get("display_name") or "").strip() or username
    role = (body.get("role") or "viewer").strip()
    password = body.get("password") or ""
    if not username or not password:
        raise HTTPException(400, "username と password は必須です")
    if role not in VALID_ROLES:
        raise HTTPException(400, f"role は {sorted(VALID_ROLES)} のいずれか")
    uid = new_id()
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_db()
    # connleak-fix-20260709: INSERT 例外 (UNIQUE 違反等) で conn を開いたまま抜けると
    # 暗黙 BEGIN の書き込みトランザクションが残留し "database is locked" になるため、
    # try/finally で close を保証し、並行 INSERT の UNIQUE 違反は 409 で穏当に弾く。
    try:
        # C-4 (3): 重複判定は有効 (is_active=1) の行のみ 409。論理削除済みの同名が居る場合も
        # 自動再利用はせず、purge か別名の選択を 409 detail で案内する (username に UNIQUE 制約は
        # 無いため、ここで弾かないと同名 2 行が生まれてしまう)。
        existing = conn.execute(
            "SELECT id, COALESCE(is_active, 1) AS is_active FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if existing:
            if existing["is_active"]:
                raise HTTPException(409, "そのusernameは既に使われています")
            raise HTTPException(
                409,
                f"無効化済みの同名利用者が存在する(id={existing['id']})。purge するか別名を使う",
            )
        conn.execute(
            """INSERT INTO users (id, name, username, display_name, role, password_hash,
                                  is_active, created_at, updated_at, avatar, must_change_password)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 1)""",
            (uid, display_name, username, display_name, role, hash_password(password), now, now, "👤"),
        )
        _log_audit(conn, "user_created", uid, f"{username} ({role})")
        conn.commit()
    except _INTEGRITY_ERRORS as e:
        # Postgres では失敗した取引が aborted のまま残るので、明示的に巻き戻してから返す
        # (finally の close も rollback するが、ここで確実に片付けておく)。
        try:
            conn.rollback()
        except Exception:
            pass
        raise HTTPException(409, "そのusernameは既に使われています") from e
    finally:
        conn.close()
    return {"id": uid, "username": username, "display_name": display_name, "role": role}


@router.patch("/api/admin/users/{user_id}", response_model=None)
async def admin_update_user(user_id: str, request: Request):
    from core.roles import VALID_ROLES

    _require_admin(request)
    body = await parse_body_pydantic(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(404, "User not found")
        updates = []
        params: list = []
        if "display_name" in body:
            updates.append("display_name = ?")
            params.append(body["display_name"])
            updates.append("name = ?")
            params.append(body["display_name"])
        if "role" in body:
            role = body["role"]
            if role not in VALID_ROLES:
                raise HTTPException(400, f"role は {sorted(VALID_ROLES)} のいずれか")
            updates.append("role = ?")
            params.append(role)
        if "is_active" in body:
            updates.append("is_active = ?")
            params.append(1 if body["is_active"] else 0)
        if not updates:
            return {"ok": True, "id": user_id}
        # berth-ga-final-cleanup (q5 B10c): 有効な管理者を 0 人にしない (purge 側の最終管理者ガードと同じ規則)。
        # 最後の管理者を降格・無効化すると、製品側に管理権限を取り戻す手段が無い。
        _demote = "role" in body and body["role"] != "admin"
        _deact = "is_active" in body and not body["is_active"]
        _active = user["is_active"] if user["is_active"] is not None else 1
        if user["role"] == "admin" and _active and (_demote or _deact):
            _others = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND id != ? AND COALESCE(is_active, 1) = 1",
                (user_id,),
            ).fetchone()
            if not _others or _others["n"] == 0:
                raise HTTPException(400, "最後の管理者は降格・無効化できません")
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat(timespec="seconds"))
        params.append(user_id)
        conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        _log_audit(conn, "user_updated", user_id, ",".join(k for k in body.keys()))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": user_id}


@router.delete("/api/admin/users/{user_id}", response_model=None)
def admin_delete_user(user_id: str, request: Request, purge: bool = False):
    """既定は論理削除（is_active=0）。物理削除はaudit保持のため避ける。

    fix-security-batch-v2 (2026-05-28) Sub-2G-1 (CRIT-3): 削除と同時に state.sessions から
    該当ユーザーの全エントリを除去する。defense-in-depth として core/auth.py:get_user_from_token
    側にも is_active チェックを追加済みだが、即座にトークン無効化するためここでも消去する。

    C-4 (1): ?purge=true で物理削除。users 行と従属行 (db.py で user_id 列を持つ表 =
    workspace_users / api_keys / sessions / refresh_tokens) を DELETE する。
    audit_logs の当該利用者の行は監査保持のため消さない。削除前に user_purged の監査行へ
    username スナップショットを残す。自分自身・最後の admin の purge は 400 で拒否。
    """
    actor = _require_admin(request)
    if actor["id"] == user_id:
        raise HTTPException(400, "自分自身は削除できません")
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(404, "User not found")
        if purge:
            # 最後の有効な admin を物理削除させない (自分自身ガードと同系の 400)
            if user["role"] == "admin":
                other_admins = conn.execute(
                    "SELECT COUNT(*) AS n FROM users "
                    "WHERE role = 'admin' AND id != ? AND COALESCE(is_active, 1) = 1",
                    (user_id,),
                ).fetchone()
                if not other_admins or other_admins["n"] == 0:
                    raise HTTPException(400, "最後の管理者は完全削除できません")
            # 監査要件: 削除前に username スナップショットを details に残す
            _log_audit(
                conn,
                "user_purged",
                user_id,
                f"username={user['username'] or user['name']} role={user['role']}",
            )
            # 従属行 → users 行の順で物理削除 (audit_logs は監査保持のため対象外)
            for _tbl in ("workspace_users", "api_keys", "sessions", "refresh_tokens"):
                conn.execute(f"DELETE FROM {_tbl} WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        else:
            conn.execute(
                "UPDATE users SET is_active = 0, updated_at = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), user_id),
            )
            _log_audit(conn, "user_deactivated", user_id, user["username"] or user["name"])
        conn.commit()
    finally:
        conn.close()
    # CRIT-3 対策: state.sessions から該当ユーザーの全トークンを消去
    try:
        import state as _state

        _tokens_to_drop = [tok for tok, sess in list(_state.sessions.items()) if sess.get("user_id") == user_id]
        for _tok in _tokens_to_drop:
            _state.sessions.pop(_tok, None)
    except Exception:
        # sessions 消去失敗は削除自体を巻き戻さない（get_user_from_token の is_active チェックで救済）
        pass
    return {"ok": True, "purged": bool(purge)}


@router.post("/api/admin/users/{user_id}/reset-password", response_model=None)
async def admin_reset_password(user_id: str, request: Request):
    _require_admin(request)
    body = await parse_body_pydantic(request)
    new_password = body.get("password") or ""
    if len(new_password) < 8:
        raise HTTPException(400, "パスワードは8文字以上で指定してください")
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(404, "User not found")
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?",
            (hash_password(new_password), datetime.now().isoformat(timespec="seconds"), user_id),
        )
        # agentK K-1 (q5 B1): 再設定と同時に対象利用者の refresh token を全件失効させ、
        # password_changed_at を更新して再設定前の JWT も無効にする (routers/auth.py と同じ経路)。
        # 自分自身を再設定した管理者は自分の JWT も失効する (GUI は 401 でログイン画面に戻る)。
        from routers.auth import _revoke_user_refresh_tokens as _rurt

        _revoked = _rurt(conn, user_id)
        _log_audit(conn, "user_password_reset", user_id, f"refresh_tokens_revoked={_revoked}")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "refresh_tokens_revoked": _revoked}


# ─── /api/admin/restart ─────────────────────────────────────


@router.post("/api/admin/restart", response_model=None)
async def admin_restart(request: Request):
    """DD-CYN-0143 A-1: 受けた Pod を落として K8s の restartPolicy で立ち直らせる。
    restart_required_to_apply の設定を遠隔から反映させる道。
    応答を返してから SIGTERM を自プロセスへ送る (uvicorn の graceful shutdown 経由)。
    複数レプリカでは応答の pod 名を見ながら全レプリカ分を繰り返し叩く。
    管理の段位・明示の同意 (confirm: true) が無ければ実行しない。"""
    _require_admin(request)
    body = await parse_body_pydantic(request)
    if not (isinstance(body, dict) and body.get("confirm") is True):
        raise HTTPException(400, "confirm: true が必要です (再起動は明示の同意を要する操作です)")
    import signal
    import socket
    import threading

    pod = socket.gethostname()
    conn = get_db()
    try:
        _log_audit(conn, "admin_restart_requested", pod, "")
        conn.commit()
    finally:
        conn.close()

    def _terminate() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Timer(0.5, _terminate).start()
    return {"ok": True, "pod": pod, "restarting": True}


# ─── /api/admin/backup* ─────────────────────────────────────


@router.post("/api/admin/backup", response_model=None)
async def admin_create_backup(request: Request):
    from server import _create_backup

    _require_admin(request)
    body = {}
    try:
        body = await parse_body_pydantic(request)
    except Exception:
        pass
    label = (body.get("label") or "").strip() if isinstance(body, dict) else ""
    meta = _create_backup(label=label)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        _log_audit(conn, "backup_created", meta["name"], label)
        conn.commit()
    finally:
        conn.close()
    return meta


@router.get("/api/admin/backups", response_model=None)
def admin_list_backups(request: Request, limit: int | None = None, offset: int = 0):
    """BETA-pagination: limit/offset でページネーション。"""
    from server import _list_backups

    _require_admin(request)
    if limit is not None and limit not in (10, 20, 50, 100):
        raise HTTPException(status_code=400, detail="limitは10/20/50/100のいずれかです")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offsetは0以上です")
    all_items = _list_backups()
    if limit is None:
        return all_items
    total = len(all_items) if isinstance(all_items, list) else len(all_items.get("items", []))
    items_src = all_items if isinstance(all_items, list) else all_items.get("items", [])
    paged = items_src[offset : offset + limit]
    return {"items": paged, "total": total, "limit": limit, "offset": offset}


@router.post("/api/admin/backups/{name}/restore", response_model=None)
def admin_restore_backup(name: str, request: Request):
    from server import _restore_backup

    _require_admin(request)
    result = _restore_backup(name)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        _log_audit(conn, "backup_restored", name, "")
        conn.commit()
    finally:
        conn.close()
    return result


@router.delete("/api/admin/backups/{name}", response_model=None)
def admin_delete_backup(name: str, request: Request):
    from server import _delete_backup

    _require_admin(request)
    _delete_backup(name)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        _log_audit(conn, "backup_deleted", name, "")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ─── /api/admin/processing-logs ─────────────────────────────


@router.get("/api/admin/processing-logs", response_model=None)
def get_processing_logs(request: Request, log_type: str = "", limit: int = 200):
    """PHASE B-4: 直近 N 件の処理ログを返す (管理者向け)。"""
    _require_admin(request)
    limit = max(1, min(int(limit or 200), 1000))
    c = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (接続リークで書き込みロック残留を防ぐ)
    try:
        if log_type:
            rows = c.execute(
                "SELECT id, timestamp, log_type, job_id, level, message, metadata_json "
                "FROM processing_logs WHERE log_type = ? "
                "ORDER BY id DESC LIMIT ?",
                (log_type, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, timestamp, log_type, job_id, level, message, metadata_json "
                "FROM processing_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


# ─── /api/admin/storage-info / cleanup / vacuum / export ────


@router.get("/api/admin/storage-info", response_model=None)
def admin_storage_info(request: Request):
    """PHASE X-5-1 / PHASE 0-B: ストレージ使用量を返す。"""
    _require_admin(request)
    error_msgs: list = []

    def _dir_size_mb(p) -> float:
        try:
            from pathlib import Path as _P

            p = _P(p) if not isinstance(p, _P) else p
            if not p.exists():
                return 0.0
            if p.is_file():
                return round(p.stat().st_size / 1024 / 1024, 2)
            total = 0
            for f in p.rglob("*"):
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except Exception:
                    pass
            return round(total / 1024 / 1024, 2)
        except Exception as _e:
            error_msgs.append(f"size:{_e}")
            return 0.0

    # release/v1.0.0-alpha: CYNOVELA_DB / CYNOVELA_CHROMA env 経由でデータディレクトリを解決
    _db_path     = Path(os.environ.get("CYNOVELA_DB",     ""))
    _chroma_path = Path(os.environ.get("CYNOVELA_CHROMA", ""))
    _data_root   = Path(os.environ.get("CYNOVELA_DATA_DIR", str(_db_path.parent.parent) if str(_db_path) else ""))
    home = _data_root if str(_data_root) else None

    sqlite_mb = _dir_size_mb(_db_path) if _db_path.exists() else 0.0
    chroma_mb = _dir_size_mb(_chroma_path) if _chroma_path.exists() else 0.0
    bm25_mb = _dir_size_mb(home / "bm25") if home else 0.0
    try:
        # 別環境移植性: 開発機固有の /Volumes 直書きを撤去。CYNOVELA_BACKUP_DIR or データルート配下 backups を解決し、
        # 不在なら実値 0.0 を返す (捏造しない)。
        _backup_dir = Path(os.environ.get("CYNOVELA_BACKUP_DIR", str(home / "backups") if home else ""))
        backups_mb = _dir_size_mb(_backup_dir) if str(_backup_dir) and _backup_dir.exists() else 0.0
    except Exception as _e:
        error_msgs.append(f"backups:{_e}")
        backups_mb = 0.0

    collections: list = []
    try:
        from rag import get_chroma

        chroma = get_chroma()
        for col in chroma.list_collections():
            try:
                cnt = col.count()
                est_mb = round(cnt * 12 / 1024, 2)
                collections.append({"name": col.name, "chunks": cnt, "mb": est_mb})
            except Exception:
                continue
    except Exception as _e:
        error_msgs.append(f"chroma:{_e}")

    total_mb = round(sqlite_mb + chroma_mb + bm25_mb + backups_mb, 2)
    out = {
        "sqlite_mb": sqlite_mb,
        "chromadb_mb": chroma_mb,
        "bm25_mb": bm25_mb,
        "backups_mb": backups_mb,
        "total_mb": total_mb,
        "db_size_bytes": int(sqlite_mb * 1024 * 1024),
        "chroma_size_bytes": int(chroma_mb * 1024 * 1024),
        "total_size_bytes": int(total_mb * 1024 * 1024),
        "collections": collections,
    }
    if error_msgs:
        out["warnings"] = error_msgs
    return out


def _cleanup_chromadb_orphans_inner(get_chroma_fn, on_after_snapshot=None):
    """Stage-2G-2 HIGH-2 修正: TOCTOU 二重チェック付きクリーンアップ。

    1. SQLite chunks の id snapshot を取る
    2. ChromaDB の各コレクションを走査し orphan 候補を計算
    3. **再度 SQLite に問い合わせて二重チェック**、その間に追加された valid id を除外
    4. 残った真の orphan のみ delete

    Args:
        get_chroma_fn: rag.get_chroma 等のファクトリ（テスト時は mock 注入用）
        on_after_snapshot: テスト用 hook。初回 snapshot 直後に呼ばれる
            （並行 publish 模倣で chunks に行を追加するために使う）
    """
    deleted = 0
    c = get_db()
    try:
        valid_ids = {r["chunk_id"] for r in c.execute("SELECT chunk_id FROM chunks").fetchall()}
    finally:
        c.close()

    # テスト用フック: 並行 publish の chunks 投入を模倣
    if on_after_snapshot is not None:
        try:
            on_after_snapshot()
        except Exception:
            pass

    chroma = get_chroma_fn()
    for col in chroma.list_collections():
        try:
            all_items = col.get()
            ids = all_items.get("ids") or []
            orphan_candidates = [i for i in ids if i and i not in valid_ids]
            if not orphan_candidates:
                continue
            # 二重チェック: 候補 id を再 SELECT。並行 publish で
            # 今は valid になっているものを除外する。
            c2 = get_db()
            try:
                placeholders = ",".join(["?"] * len(orphan_candidates))
                now_valid_rows = c2.execute(
                    f"SELECT chunk_id FROM chunks WHERE chunk_id IN ({placeholders})",
                    orphan_candidates,
                ).fetchall()
                now_valid = {r["chunk_id"] for r in now_valid_rows}
            finally:
                c2.close()
            true_orphans = [i for i in orphan_candidates if i not in now_valid]
            if true_orphans:
                col.delete(ids=true_orphans)
                deleted += len(true_orphans)
        except Exception:
            continue
    return {"ok": True, "deleted_count": deleted}


@router.post("/api/admin/cleanup/chromadb-orphans", response_model=None)
def admin_cleanup_chromadb_orphans(request: Request):
    """PHASE X-5-2: ChromaDB の孤立エントリを削除（Stage-2G-2 HIGH-2 で二重チェック化）。"""
    _require_admin(request)
    from rag import get_chroma

    return _cleanup_chromadb_orphans_inner(get_chroma_fn=get_chroma)


@router.post("/api/admin/maintenance/vacuum", response_model=None)
def admin_vacuum(request: Request):
    """PHASE X-5-3: SQLite VACUUM を実行してファイル最適化する。"""
    _require_admin(request)
    # DD-CYN-0143 版5 (V-2 実叩きで発覚): Postgres 構成では VACUUM をトランザクション内で
    # 実行できず 500 になっていた。PG では autocommit 接続で実行し、大きさは pg_database_size で測る。
    from core.data_layer import get_data_layer

    if get_data_layer().is_unified:
        import psycopg

        from core.relational_pg import _resolve_pg_params

        with psycopg.connect(**_resolve_pg_params(), autocommit=True) as pc:
            before = pc.execute("SELECT pg_database_size(current_database())").fetchone()[0]
            pc.execute("VACUUM")
            after = pc.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        return {
            "ok": True,
            "backend": "postgres",
            "before_mb": round(before / 1024 / 1024, 2),
            "after_mb": round(after / 1024 / 1024, 2),
            "saved_mb": round((before - after) / 1024 / 1024, 2),
        }
    # release/v1.0.0-alpha: CYNOVELA_DB env 経由で DB ファイルを解決
    db_path = Path(os.environ.get("CYNOVELA_DB", ""))
    if not db_path.is_file():
        raise HTTPException(404, "DB が見つかりません")
    before = db_path.stat().st_size
    c = get_db()
    try:
        c.execute("VACUUM")
        c.commit()
    finally:
        c.close()
    after = db_path.stat().st_size
    return {
        "ok": True,
        "before_mb": round(before / 1024 / 1024, 2),
        "after_mb": round(after / 1024 / 1024, 2),
        "saved_mb": round((before - after) / 1024 / 1024, 2),
    }


@router.post("/api/admin/export", response_model=None)
def admin_export_full(request: Request):
    """PHASE X-1: 完全バックアップ tar.gz を生成して返す。"""
    import server as _server

    _require_admin(request)
    # release/v1.0.0-alpha: CYNOVELA_DB / CYNOVELA_CHROMA env 経由で解決
    yaml_path = Path(_server.__file__).resolve().parent / "cynovela.yaml"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = Path(tempfile.gettempdir()) / f"cynovela-export-{ts}.tar.gz"
    try:
        with tarfile.open(out_path, "w:gz") as tar:
            db_path = Path(os.environ.get("CYNOVELA_DB", ""))
            if db_path.is_file():
                tar.add(db_path, arcname="data/cynovela.db")
            chroma_path = Path(os.environ.get("CYNOVELA_CHROMA", ""))
            if chroma_path.is_dir():
                tar.add(chroma_path, arcname="data/chroma")
            # fix-security-batch-v2 (2026-05-28) Sub-1C: 関数スコープ外の `home` を参照していた
            # NameError を解消。CYNOVELA_DATA_DIR から data_root を再計算する。
            try:
                _data_root = Path(
                    os.environ.get("CYNOVELA_DATA_DIR", str(db_path.parent.parent) if str(db_path) else "")
                )
                bm25_path = _data_root / "bm25" if str(_data_root) else None
                if bm25_path is not None and bm25_path.is_dir():
                    tar.add(bm25_path, arcname="data/bm25")
            except NameError:
                # 後段で home が定義されていた場合への保険（現状は到達しない）
                pass
            except Exception:
                # bm25 ディレクトリが取得できなくても export 全体は継続
                pass
            if yaml_path.is_file():
                tar.add(yaml_path, arcname="cynovela.yaml")
    except Exception as e:
        raise HTTPException(500, f"export failed: {e}")
    return FileResponse(
        path=str(out_path),
        media_type="application/gzip",
        filename=out_path.name,
    )


@router.get("/api/admin/export/csv", response_model=None)
def admin_export_csv(request: Request, type: str = "", workspace_id: str = ""):
    """PHASE X-1 追記: 個別データを CSV/JSON でダウンロード。

    DD-CYN-0116 X-3: `type=chat_history` は `workspace_id` でまとまりを絞れる。
    指定した場合はそのまとまりの会話だけを出す (境界を効かせる)。
    """
    _require_admin(request)
    if not type:
        raise HTTPException(400, "?type=feedback|chat_history|audit_log|sources|settings は必須")
    c = get_db()
    try:
        if type == "feedback":
            rows = c.execute(
                "SELECT created_at, query, answer_preview, rating, mode, "
                "collection_id, response_time_ms FROM feedback ORDER BY id DESC"
            ).fetchall()
            cols = ["created_at", "query", "answer_preview", "rating", "mode", "collection_id", "response_time_ms"]
            fname = "feedback"
        elif type == "chat_history":
            # ga-close-v3 PartB (B-2): messages に user_id 列は無い (実在するのは
            # id/session_id/role/content/content_hash/model_name/redaction_status/
            # pii_flags_json/token_count/latency_ms/created_at/retrieval_json)。
            # 「誰の会話か」は sessions.user_id が持つので、そちらを外部結合で引く。
            # 従来は存在しない列を指定していたため本経路は必ず 500 だった。
            # DD-CYN-0116 X-3: この経路は本製品で最大の平文持ち出し口である。
            #  (1) まとまりの境界を効かせる: 従来は WHERE が無く、1回の持ち出しで
            #      全まとまりの全会話が出ていた (chat.py build_conversation_context が
            #      効かせている Stage-2G-2 HIGH-4 の境界検査を素通りしていた)。
            #  (2) enc-leak-guard: 復号できなかった本文 ('enc:' のまま) を CSV へ
            #      書かない。暗号文を本文であるかのように出さない。
            #  (3) 監査を残す: full_export_workspace は監査行を書くのに、こちらは
            #      無記録だった。
            if workspace_id:
                rows = c.execute(
                    "SELECT m.id AS id, m.session_id AS session_id, s.user_id AS user_id, "
                    "m.role AS role, m.content AS content, m.created_at AS created_at "
                    "FROM messages m JOIN sessions s ON s.id = m.session_id "
                    "WHERE s.workspace_id = ? "
                    "ORDER BY m.id DESC LIMIT 10000",
                    (workspace_id,),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT m.id AS id, m.session_id AS session_id, s.user_id AS user_id, "
                    "m.role AS role, m.content AS content, m.created_at AS created_at "
                    "FROM messages m LEFT JOIN sessions s ON s.id = m.session_id "
                    "ORDER BY m.id DESC LIMIT 10000"
                ).fetchall()
            from vault_enc import dec_raw as _dec_raw

            _decoded = [{**dict(_r), "content": _dec_raw(_r["content"])} for _r in rows]
            _undec = sum(1 for _r in _decoded if str(_r.get("content") or "").startswith("enc:"))
            rows = [_r for _r in _decoded if not str(_r.get("content") or "").startswith("enc:")]
            if _undec:
                _log_audit(
                    c,
                    "export_chat_history_undecryptable",
                    workspace_id or "(all)",
                    f"{_undec} 件を除外 (鍵不一致の可能性)",
                )
            _log_audit(
                c,
                "export_chat_history",
                workspace_id or "(all)",
                f"rows={len(rows)} scope={'workspace' if workspace_id else 'all'}",
            )
            cols = ["id", "session_id", "user_id", "role", "content", "created_at"]
            fname = "chat_history"
        elif type == "audit_log":
            # ga-close-v3 PartB (B-2): audit_logs の時刻列は created_at ではなく timestamp。
            # 従来は存在しない列を指定していたため本経路は必ず 500 だった。
            rows = c.execute(
                "SELECT id, user_id, action, target, detail, timestamp "
                "FROM audit_logs ORDER BY id DESC LIMIT 10000"
            ).fetchall()
            cols = ["id", "user_id", "action", "target", "detail", "timestamp"]
            fname = "audit_log"
        elif type == "sources":
            rows = c.execute(
                "SELECT id, name, path, status, file_count, created_at " "FROM sources ORDER BY created_at DESC"
            ).fetchall()
            cols = ["id", "name", "path", "status", "file_count", "created_at"]
            fname = "sources"
        elif type == "settings":
            rows = c.execute("SELECT key, value FROM settings").fetchall()
            settings_dict = {r["key"]: r["value"] for r in rows}
            ts = datetime.now().strftime("%Y%m%d")
            return Response(
                content=_json_mod.dumps(settings_dict, ensure_ascii=False, indent=2),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="settings_{ts}.json"'},
            )
        else:
            raise HTTPException(400, f"未知の type: {type}")
    finally:
        c.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r[c] if c in r.keys() else "" for c in cols])
    ts = datetime.now().strftime("%Y%m%d")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}_{ts}.csv"'},
    )


# ─── DD-CYN-0116 G-13: 順番待ちの様子を読めるようにする ───────────────
@router.get("/api/queue/status", response_model=None)
def queue_status(request: Request):
    """順番待ちの長さ・処理中の件数・再配送の回数・失敗の件数を返す。

    従来これらは worker のログにしか出ておらず、詰まっているのか空なのかを
    画面や決まった場所から読む手段が無かった。
    """
    _require_admin(request)
    out: dict = {"enabled": False, "pending": None, "processing": None}
    try:
        import core.job_queue as _q

        out["enabled"] = _q.queue_enabled()
        if out["enabled"]:
            out.update(_q.queue_depths())
            out["reachable"] = out.get("pending", -1) >= 0
            # DD-CYN-0116 G-11: どの処理役が何を抱えているか。
            # 処理役を落としたとき、誰が何を抱えたまま落ちたかがここで分かる。
            out["inflight_by_worker"] = _q.worker_processing_map()
    except Exception as _e:
        out["error"] = str(_e)

    # 依頼の正本は publish_jobs 側。状態ごとの件数と、再配送の回数を数える。
    c = get_db()
    try:
        rows = c.execute(
            "SELECT status, count(*) AS n FROM publish_jobs GROUP BY status"
        ).fetchall()
        out["jobs"] = {r["status"]: r["n"] for r in rows}
        _rd = c.execute(
            "SELECT count(*) AS n FROM publish_jobs WHERE status = 'failed'"
        ).fetchone()
        out["failed"] = _rd["n"] if _rd else 0
        # Redis に居ない 'pending' = 掃き寄せ待ち (Q-3)
        out["orphan_pending"] = (out.get("jobs") or {}).get("pending", 0)
    except Exception as _e:
        out["jobs_error"] = str(_e)
    finally:
        c.close()

    try:
        import core.job_queue as _q2

        _p, _pr, _hb = _q2._keys()
        out["redelivered"] = _q2.get_client().hlen(_hb)
    except Exception:
        out["redelivered"] = None
    return out

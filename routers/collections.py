"""Collections endpoints (/api/collections/*)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from db import get_db, new_id
from core.auth import _require_admin, _require_authenticated
from core.constants import ACCESS_LEVELS
from core.audit import _log_audit, log_admin_change
from core.errors import api_error
# ga-close-v3 PartD D-3: 伏字件数の数え方は guardrail.py の 1 か所に集約する。
from guardrail import PII_COUNT_TIER, pii_counts_from_db

router = APIRouter(tags=["collections"])


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


RAG_STRATEGIES = {"simple", "hybrid_bm25", "contextual"}


# ============================================================
# Stage R7 C-4: Smart Ingestion Stage 2/3 状態遷移
# ============================================================
# 遷移パス (内部設計メモ / Phase 3 Recon Agent J §1-3 中):
#   draft → ingested → ready
#   draft → publishing → ready / failed (legacy 経路、互換維持)
#   publishing → stopped (中断)
#
# migration 0002 で collections.status の CHECK に 'ingested' を追加。
# Phase 3 Recon Agent J で grep ヒット 0 だった機能を本実装で 3+ 件に。

VALID_STATE_TRANSITIONS = {
    ("draft", "ingested"): "ingest 完了 (Stage 2 → Stage 3 入口)",
    ("draft", "publishing"): "legacy publish 開始 (Stage 1 → publishing)",
    ("ingested", "ready"): "publish 完了 (Stage 3 → Ready)",
    ("ingested", "publishing"): "ingest 後の本 publish 開始",
    ("publishing", "ready"): "publish 完了",
    ("publishing", "failed"): "publish 失敗",
    ("publishing", "stopped"): "publish 中断",
    ("failed", "draft"): "失敗からのリトライ",
    ("ready", "draft"): "再 ingest のための差し戻し",
}


def transition_collection_state(col_id: str, from_state: str, to_state: str, conn=None) -> bool:
    """Smart Ingestion 状態遷移ヘルパー (Stage 2/3 経路)。

    Stage R7 C-4 で新設。VALID_STATE_TRANSITIONS の合法遷移のみ許可する。
    """
    if (from_state, to_state) not in VALID_STATE_TRANSITIONS:
        return False
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE collections SET status = ? WHERE id = ? AND status = ?",
            (to_state, col_id, from_state),
        )
        if own_conn:
            conn.commit()
        return cur.rowcount > 0
    finally:
        if own_conn:
            conn.close()


@router.get("/api/collections/{collection_id}/provenance", response_model=None)
def get_collection_provenance(request: Request, collection_id: str):
    """Collection の Provenance 履歴を返す (filename, version 降順).
    存在しない collection_id を指定した場合は 404 を返す。"""
    _require_admin(request)
    conn = get_db()
    try:
        col = conn.execute("SELECT id FROM collections WHERE id = ?", (collection_id,)).fetchone()
        if not col:
            raise api_error("COLLECTION_NOT_FOUND", f"Collection not found: {collection_id}", 404)
        rows = conn.execute(
            """SELECT filename, sha256, file_size, version,
                      published_at, published_by, is_current
               FROM document_provenance
               WHERE collection_id = ?
               ORDER BY filename ASC, version DESC""",
            (collection_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "collection_id": collection_id,
        "provenance": [dict(r) for r in rows],
    }


@router.patch("/api/collections/{col_id}/archive", response_model=None)
def archive_collection(col_id: str, request: Request):
    from core.auth import _require_admin

    user = _require_admin(request)
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not row:
            raise api_error("NOT_FOUND", "collection not found", status=404)
        conn.execute(
            "UPDATE collections SET archived_at = ?, archived_by = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), user["id"], col_id),
        )
        _log_audit(conn, "collection_archived", col_id, f"by={user['id']}")
        conn.commit()
    finally:
        conn.close()
    log_admin_change(user["id"], "collection", col_id, "archive")
    return {"id": col_id, "status": "archived"}


@router.patch("/api/collections/{col_id}/unarchive", response_model=None)
def unarchive_collection(col_id: str, request: Request):
    from core.auth import _require_admin

    user = _require_admin(request)
    conn = get_db()
    try:
        conn.execute(
            "UPDATE collections SET archived_at = NULL, archived_by = NULL WHERE id = ?",
            (col_id,),
        )
        _log_audit(conn, "collection_unarchived", col_id, f"by={user['id']}")
        conn.commit()
    finally:
        conn.close()
    log_admin_change(user["id"], "collection", col_id, "unarchive")
    return {"id": col_id, "status": "unarchived"}


@router.get("/api/collections", response_model=None)
def list_collections(
    request: Request,
    workspace_id: str = None,
    include_archived: bool = False,
    limit: int | None = None,
    offset: int = 0,
    q: str | None = None,
):
    """UX-4: include_archived=true でアーカイブ済み Collection も返す."""
    from server import rows_to_list

    user = _require_authenticated(request)
    if limit is not None and limit not in (10, 20, 50, 100):
        raise HTTPException(status_code=400, detail="limitは10/20/50/100のいずれかです")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offsetは0以上です")
    conn = get_db()
    try:
        where_parts: list[str] = []
        params: list = []
        if workspace_id:
            where_parts.append("workspace_id = ?")
            params.append(workspace_id)
        # authz-fix-v1: 非admin は自分の所属WSの collection のみに絞る (admin は全件=広域維持)。
        # レスポンス形は不変・件数のみ変化。手本: list_workspaces のスコープ絞り。
        if (user or {}).get("role") != "admin":
            where_parts.append("workspace_id IN (SELECT workspace_id FROM workspace_users WHERE user_id = ?)")
            params.append((user or {}).get("id"))
            # C-1(b): 非adminには catalog (routers/catalog.py の access_level 絞り) と同等以上の
            # 可視性絞りを足す: confidential は見せない。加えて allowed_roles_json が設定済みなら
            # 自分の role を含むものだけ (NULL/空/'[]' は rag.py の allowed_roles_json 解釈と同じく
            # 「全ロール許可」扱い)。role 名は VALID_ROLES = admin/viewer のみで引用符付き LIKE に
            # 部分一致の誤爆は無く、SQLite/Postgres どちらのバックエンドでも同じに動く。
            # total (COUNT) と一覧が同じ WHERE を共有するためページングも崩れない。
            where_parts.append("(access_level IS NULL OR access_level != 'confidential')")
            where_parts.append(
                "(allowed_roles_json IS NULL OR allowed_roles_json = '' "
                "OR allowed_roles_json = '[]' OR allowed_roles_json LIKE ?)"
            )
            params.append('%"' + ((user or {}).get("role") or "viewer") + '"%')
        if not include_archived:
            where_parts.append("archived_at IS NULL")
        if q:
            where_parts.append("name LIKE ?")
            params.append(f"%{q}%")
        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        total = None
        if limit is not None:
            total = conn.execute(
                f"SELECT COUNT(*) FROM collections {where_sql}",
                params,
            ).fetchone()[0]

        pagination_sql = ""
        pagination_params: list = []
        if limit is not None:
            pagination_sql = " LIMIT ? OFFSET ?"
            pagination_params = [limit, offset]
        collections = rows_to_list(
            conn.execute(
                f"SELECT * FROM collections {where_sql} " f"ORDER BY created_at DESC {pagination_sql}",
                params + pagination_params,
            ).fetchall()
        )

        for col in collections:
            col["file_ids"] = [
                r["file_id"]
                for r in conn.execute(
                    "SELECT file_id FROM collection_files WHERE collection_id = ?", (col["id"],)
                ).fetchall()
            ]
            # rawmode-partC: raw_only 列不在の旧DBでも bool で必ず返す
            col["raw_only"] = bool(col.get("raw_only") or 0)
            # DD-CYN-0132 4-4: 外へ出してよいかの印。列不在の旧DBでも bool で必ず返す (既定 False)。
            col["egress_allowed"] = bool(col.get("egress_allowed") or 0)
    finally:
        conn.close()
    if limit is None:
        return collections
    return {"items": collections, "total": total, "limit": limit, "offset": offset}


@router.post("/api/collections", response_model=None, summary="コレクション作成 (admin 限定)")
async def create_collection(request: Request):
    user = _require_admin(request)
    body = await parse_body_pydantic(request)
    name = body.get("name")
    workspace_id = body.get("workspace_id")
    file_ids = body.get("file_ids", [])
    access_level = body.get("access_level", "public")
    # C-8: access_level の Python 側検証 (db.py collections の CHECK と同値・下の rag_strategy 検証と同型)。
    if access_level not in ACCESS_LEVELS:
        raise HTTPException(400, f"access_level は {sorted(ACCESS_LEVELS)} のいずれか")
    allowed_roles = body.get("allowed_roles") or ["admin", "viewer"]
    if not isinstance(allowed_roles, list):
        raise HTTPException(400, "allowed_roles must be a list")
    # C-1 (版5 追補): 明示 confidential 作成でも「confidential なのに viewer 可」の矛盾を作らない
    # (PUT 側の規則と同一)。明示指定の矛盾は 400、既定 roles のままなら viewer を落とす。
    if access_level == "confidential" and "viewer" in allowed_roles:
        if body.get("allowed_roles"):
            raise HTTPException(400, "access_level='confidential' の Collection に viewer は許可できません")
        allowed_roles = [r for r in allowed_roles if r != "viewer"] or ["admin"]
    rag_strategy = (body.get("rag_strategy") or "hybrid_bm25").strip()
    if rag_strategy not in RAG_STRATEGIES:
        raise HTTPException(400, f"rag_strategy は {sorted(RAG_STRATEGIES)} のいずれか")
    # ga-finish-P4 (rawmode-receptor-close-20260727): 伏字を迂回する受け口のうち raw_mode を
    # 閉じる。raw_mode は collections.rag_mode='raw' を書き、chat 側の「rawモード Collection は
    # Guardrail をバイパス」分岐 (routers/chat.py の rag_mode='raw' 判定) へ到達していた。
    # 列 (collections.rag_mode) と過去データは保全する (migration は行わない)。
    # berth 差異: raw_only (masked 層を作らない取り込み・admin 限定) は姉妹系統では
    # masked-only §9-7 で廃止済みだが berth は当該変更を受けておらず現存する。本同送は
    # raw_mode のみを対象とし、raw_only の既存挙動は温存する。
    if bool(body.get("raw_mode", False)):
        raise HTTPException(400, "raw_mode (伏字なし取り込み) は廃止されました")
    raw_only = bool(body.get("raw_only", False))
    # rawmode-partC: raw_only=true の Collection 作成は admin 限定。
    # role gate は list_collections の authz-fix-v1 inline `role != "admin"` 判定を踏襲。
    if raw_only and (user or {}).get("role") != "admin":
        raise HTTPException(403, "raw_only の Collection 作成は admin のみ可能です")

    if not name or not workspace_id:
        raise HTTPException(400, "name and workspace_id are required")

    conn = get_db()
    try:
        ws_row = conn.execute("SELECT id FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        if not ws_row:
            conn.close()
            raise HTTPException(404, f"Workspace not found: {workspace_id}")
        classification_filter = body.get("classification_filter") or []
        if classification_filter and isinstance(classification_filter, list):
            ws_src_rows = conn.execute(
                "SELECT source_id FROM workspace_sources WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
            ws_src_ids = [r["source_id"] for r in ws_src_rows]
            if ws_src_ids:
                placeholders = ",".join(["?"] * len(ws_src_ids))
                file_rows = conn.execute(
                    f"SELECT id, classification FROM files WHERE source_id IN ({placeholders})",
                    ws_src_ids,
                ).fetchall()
                filter_set = set(classification_filter)
                matched_ids: list = []
                for fr in file_rows:
                    cls_raw = fr["classification"]
                    if not cls_raw:
                        continue
                    try:
                        cls = json.loads(cls_raw)
                    except Exception:
                        continue
                    if cls.get("category") in filter_set:
                        matched_ids.append(fr["id"])
                file_ids = list({*file_ids, *matched_ids})
        valid_file_ids: list = []
        for fid in file_ids or []:
            if not conn.execute("SELECT 1 FROM files WHERE id = ?", (fid,)).fetchone():
                # 従来どおり: 実在しない file_id は黙って外す (挙動維持)
                continue
            # 所属検証: 実在しても当該 workspace の source に属さない file は取り込ませない
            # (従来は存在確認のみで、他WSの資料を混入できた)。所属は workspace_sources 経由で見る
            # (unlinked-files の JOIN と同じ形)。
            _in_ws = conn.execute(
                "SELECT 1 FROM files f JOIN workspace_sources ws ON ws.source_id = f.source_id "
                "WHERE f.id = ? AND ws.workspace_id = ?",
                (fid, workspace_id),
            ).fetchone()
            if not _in_ws:
                raise HTTPException(400, f"file_id '{fid}' は workspace '{workspace_id}' に属していません")
            valid_file_ids.append(fid)

        # C-1(a): 機密ファイルを含むのに既定のまま public で作られる穴を塞ぐ (安全側導出)。
        # ファイル側の機密度は files.sensitivity (classifier.detect_sensitivity:
        # restricted > confidential > internal > public・スキャン時に必ず書かれる) と
        # files.sensitivity_level (utils.metadata.score_sensitivity: confidential が最上位・
        # メタデータ補完時に書かれる) の2列に在る。files.classification 列は Smart Ingestion
        # 14カテゴリの JSON で category に 'confidential' は現れないため、実判定は
        # sensitivity 系2列で行う (sensitivity 側の 'restricted' は confidential より強いので同扱い)。
        _wants_confidential = (
            isinstance(classification_filter, list) and "confidential" in classification_filter
        )
        _has_confidential_file = False
        if valid_file_ids:
            _ph_cf = ",".join(["?"] * len(valid_file_ids))
            _has_confidential_file = (
                conn.execute(
                    f"SELECT 1 FROM files WHERE id IN ({_ph_cf}) "
                    "AND (sensitivity IN ('confidential', 'restricted') "
                    "OR sensitivity_level = 'confidential') LIMIT 1",
                    valid_file_ids,
                ).fetchone()
                is not None
            )
        if _wants_confidential or _has_confidential_file:
            if "access_level" in body and access_level == "public":
                # 明示 public は黙って倒さず拒否する (指定と実体の矛盾を作らせない)
                raise HTTPException(400, "機密区分のファイルを含むため access_level='public' にはできません")
            if "access_level" not in body:
                access_level = "confidential"
            if not body.get("allowed_roles"):
                allowed_roles = ["admin"]

        # rawmode-partC: raw_only 列は並列のDB移行で追加されるため、列不在の旧DBでも
        # 作成APIが落ちないよう PRAGMA table_info で存在確認する (migrations の動的列対応と同じ流儀)。
        _has_raw_only_col = any(
            r[1] == "raw_only" for r in conn.execute("PRAGMA table_info(collections)").fetchall()
        )
        if raw_only and not _has_raw_only_col:
            raise HTTPException(400, "このDBは raw_only 列未対応のため raw_only Collection を作成できません")

        cid = new_id()
        try:
            _acl_json = json.dumps(allowed_roles, ensure_ascii=False)
            # ga-finish-P4: raw_mode 受け口を閉じたため、新規作成の rag_mode は常に NULL。
            # 列へ 'raw' を書く経路は存在しない (列と過去データは保全)。
            _rag_mode = None
            if _has_raw_only_col:
                conn.execute(
                    "INSERT INTO collections (id, name, workspace_id, access_level, "
                    "allowed_roles_json, acl_roles, rag_strategy, rag_mode, raw_only) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        cid,
                        name,
                        workspace_id,
                        access_level,
                        _acl_json,
                        _acl_json,
                        rag_strategy,
                        _rag_mode,
                        1 if raw_only else 0,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO collections (id, name, workspace_id, access_level, "
                    "allowed_roles_json, acl_roles, rag_strategy, rag_mode) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, name, workspace_id, access_level, _acl_json, _acl_json, rag_strategy, _rag_mode),
                )
            for fid in valid_file_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO collection_files (collection_id, file_id) VALUES (?, ?)",
                    (cid, fid),
                )
            conn.commit()
            if raw_only:
                # rawmode-partC: raw_only=true 作成であることを既存の監査ログ機構に記録
                # (_log_audit は失敗時サイレント継続のため作成成功後の追記として安全)。
                _log_audit(
                    conn,
                    "collection_created_raw_only",
                    target=cid,
                    detail=f"raw_only=true name={name} by={(user or {}).get('id')}",
                    user_id=(user or {}).get("id"),
                )
        except HTTPException:
            raise
        except Exception as e:
            conn.close()
            raise HTTPException(400, f"Collection作成失敗: {e}")
    finally:
        conn.close()
    return {
        "id": cid,
        "name": name,
        "status": "draft",
        "access_level": access_level,
        "allowed_roles": allowed_roles,
        "rag_strategy": rag_strategy,
        # ga-finish-P4: 受け口を閉じたので常に False (応答キーは互換のため残す)。
        "raw_mode": False,
        "raw_only": raw_only,
    }


@router.get("/api/collections/{col_id}", response_model=None)
def get_collection_by_id(request: Request, col_id: str):
    """PHASE 0-C: Collection 単体取得"""
    _require_admin(request)
    conn = get_db()
    try:
        col = conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Collection not found")
        out = dict(col)
        out["file_ids"] = [
            r["file_id"]
            for r in conn.execute("SELECT file_id FROM collection_files WHERE collection_id = ?", (col_id,)).fetchall()
        ]
        # rawmode-partC: raw_only 列不在の旧DBでも bool で必ず返す
        out["raw_only"] = bool(out.get("raw_only") or 0)
        # DD-CYN-0132 4-4: 外へ出してよいかの印 (既定 False)
        out["egress_allowed"] = bool(out.get("egress_allowed") or 0)
    finally:
        conn.close()
    return out


@router.get("/api/collections/{col_id}/publish-summary", response_model=None)
def get_collection_publish_summary(request: Request, col_id: str):
    """v3.5.0 Phase2 (完了ログ用): masked tier の chunks.pii_summary を集計して
    マスキング件数・ラベル別内訳・除外数・ファイル数を返す読み取り専用 EP。

    - DB スキーマ非変更／既存 API 非改変（新規 additive EP）。
    - 伏字・暗号化ロジックには一切触れず、既に保存済みの集計値を読むだけ。
    - raw との二重計上を避けるため masked tier のみを一次ソースにする。
    """
    _require_admin(request)
    conn = get_db()
    try:
        col = conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Collection not found")
        # rawmode-partC: raw_only Collection は masked tier を持たないため、masked 由来の
        # 件数は「偽の0」ではなく null を返す (列不在の旧DBでは常に False = 従来挙動)。
        raw_only_flag = bool(dict(col).get("raw_only") or 0)
        placeholder_only_files: list = []
        skipped_details: list = []
        stage_seconds: dict = {}
        if raw_only_flag:
            labels = None
            pii_chunks = None
            chunk_count = None
            excluded_count = None
        else:
            # ga-close-v3 PartD D-3: 数え方は guardrail.pii_counts_from_db に集約した。
            #   旧実装は masked 層を 5 種の許可リスト
            #   {PERSON_JP,PHONE_JP,EMAIL,MYNUMBER,CREDIT} でだけ数えており、
            #   URL / IPV4 / PHONE_LAND / PASSPORT / SSN / IBAN / 資格情報しか当たって
            #   いない塊は伏字が効いていても 0 件として落ちていた
            #   (公開済み「デモ資料一式」実測: 旧 361 / 全型 2121)。
            #   ここで数え直さない = 許可リストを復活させないこと。
            _counts = pii_counts_from_db(conn, collection_id=col_id)
            labels = _counts["labels"]
            pii_chunks = _counts["pii_chunks"]
            chunk_count = _counts["chunk_count"]
            excluded_count = conn.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE collection_id = ? AND tier = ? AND excluded = 1",
                (col_id, PII_COUNT_TIER),
            ).fetchone()["n"]
        # vision-placeholder-warn-20260727: 中身が1文字も入らなかったファイル。
        #   chunks.content は暗号文で保存されるため、索引から数え直すことはできない
        #   (平文と誤認して常に0を返す＝この Part が塞ごうとしている「やっていないのに
        #   成功を返す」を自分で作ることになる)。判定は平文がある取り込みの瞬間に一度だけ
        #   行い、その結果を取り込み操作ログへ残してここで読み出す。
        try:
            _plog = conn.execute(
                "SELECT metadata_json FROM processing_logs "
                "WHERE log_type = 'ingest' AND metadata_json LIKE ? "
                "ORDER BY id DESC LIMIT 1",
                (f'%"stage": "done"%{col_id}%',),
            ).fetchone()
            if _plog and _plog["metadata_json"]:
                _pm = json.loads(_plog["metadata_json"]) or {}
                if _pm.get("collection_id") == col_id:
                    placeholder_only_files = list(_pm.get("placeholder_only_files") or [])
                    # DD-CYN-0091 C: 飛ばしたファイルの一覧 (done イベント由来・additive)
                    skipped_details = list(_pm.get("skipped_details") or [])
                    # DD-CYN-0115 H-5: 処理の内訳 (6工程・done イベント由来・additive)
                    stage_seconds = dict(_pm.get("stage_seconds") or {})
        except Exception:
            placeholder_only_files = []
        file_count = conn.execute(
            "SELECT COUNT(*) AS n FROM collection_files WHERE collection_id = ?",
            (col_id,),
        ).fetchone()["n"]
        # receiptfix-20260723: 受領書の「所要時間 0.0s」是正。この Collection の最新 completed
        #   publish job の実経過秒を additive に返す (publish_history は workspace 単位で
        #   collection_id を持たないため publish_jobs の created_at/updated_at から算出)。
        elapsed_seconds = None
        try:
            _job = conn.execute(
                "SELECT created_at, updated_at FROM publish_jobs "
                "WHERE collection_id = ? AND status = 'completed' "
                "ORDER BY updated_at DESC LIMIT 1",
                (col_id,),
            ).fetchone()
            if _job and _job["created_at"] and _job["updated_at"]:
                from datetime import datetime as _dt
                _fmt = "%Y-%m-%d %H:%M:%S"
                elapsed_seconds = round(
                    (_dt.strptime(_job["updated_at"], _fmt) - _dt.strptime(_job["created_at"], _fmt)).total_seconds(),
                    1,
                )
        except Exception:
            elapsed_seconds = None
    finally:
        conn.close()
    return {
        "collection_id": col_id,
        "chunk_count": chunk_count,
        "excluded_count": excluded_count,
        "file_count": file_count,
        "pii_count": pii_chunks,
        "pii_labels": labels,
        "raw_only": raw_only_flag,
        "elapsed_seconds": elapsed_seconds,
        # DD-CYN-0115 H-5 (additive): 処理の内訳 6工程の累積秒
        "stage_seconds": stage_seconds,
        # vision-placeholder-warn-20260727 (additive・既存キー不変)
        "placeholder_only_count": len(placeholder_only_files),
        "placeholder_only_files": [os.path.basename(_f) for _f in placeholder_only_files[:50]],
        # DD-CYN-0091 C (additive): 飛ばしたファイルの一覧 (ファイル名+理由)
        "skipped_details": skipped_details[:50],
    }


@router.put("/api/collections/{col_id}", response_model=None)
async def update_collection(col_id: str, request: Request):
    _require_admin(request)
    body = await parse_body_pydantic(request)
    conn = get_db()
    try:
        col = conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Collection not found")
        if col["status"] != "draft":
            raise HTTPException(400, "Can only update draft collections")

        if "name" in body:
            conn.execute("UPDATE collections SET name = ? WHERE id = ?", (body["name"], col_id))
        if "access_level" in body:
            # C-8: access_level の Python 側検証 (作成時と同型・db.py の CHECK と同値)
            if body["access_level"] not in ACCESS_LEVELS:
                raise HTTPException(400, f"access_level は {sorted(ACCESS_LEVELS)} のいずれか")
            conn.execute("UPDATE collections SET access_level = ? WHERE id = ?", (body["access_level"], col_id))
            # C-1(PUT同期): confidential へ変えたのに現在の allowed_roles に viewer が残ると
            # 「confidential なのに viewer 可」の矛盾になる。body に allowed_roles が無い場合は
            # 現在値から 'viewer' を安全側で落とす (空になるなら ['admin'])。
            if body["access_level"] == "confidential" and "allowed_roles" not in body:
                try:
                    _cur_roles = json.loads(dict(col).get("allowed_roles_json") or "[]")
                except Exception:
                    _cur_roles = []
                if isinstance(_cur_roles, list) and "viewer" in _cur_roles:
                    _new_roles = [r for r in _cur_roles if r != "viewer"] or ["admin"]
                    _new_roles_json = json.dumps(_new_roles, ensure_ascii=False)
                    conn.execute(
                        "UPDATE collections SET allowed_roles_json = ?, acl_roles = ? WHERE id = ?",
                        (_new_roles_json, _new_roles_json, col_id),
                    )
        if "allowed_roles" in body:
            ar = body["allowed_roles"]
            if not isinstance(ar, list):
                raise HTTPException(400, "allowed_roles must be a list")
            # C-1(PUT同期): 更新後の状態が「confidential なのに viewer 可」になる明示指定は拒否
            # (access_level は同時指定があればその値、無ければ現在値で判定)。
            _eff_access = body.get("access_level", dict(col).get("access_level"))
            if _eff_access == "confidential" and "viewer" in ar:
                raise HTTPException(400, "access_level='confidential' の Collection に viewer は許可できません")
            _ar_json = json.dumps(ar, ensure_ascii=False)
            # C-1(PUT同期): acl_roles は allowed_roles_json と同期する正規列 (作成時の INSERT と同じ形)。
            # 従来は allowed_roles_json だけ更新され acl_roles が古いまま残っていた。
            conn.execute(
                "UPDATE collections SET allowed_roles_json = ?, acl_roles = ? WHERE id = ?",
                (_ar_json, _ar_json, col_id),
            )
        if "rag_strategy" in body:
            rs = (body["rag_strategy"] or "").strip()
            if rs not in RAG_STRATEGIES:
                raise HTTPException(400, f"rag_strategy は {sorted(RAG_STRATEGIES)} のいずれか")
            conn.execute(
                "UPDATE collections SET rag_strategy = ? WHERE id = ?",
                (rs, col_id),
            )
        for _bk, _col in (("chunk_size", "chunk_size"), ("chunk_overlap", "chunk_overlap")):
            if _bk in body:
                v = body[_bk]
                if v in (None, ""):
                    conn.execute(f"UPDATE collections SET {_col} = NULL WHERE id = ?", (col_id,))
                else:
                    try:
                        iv = int(v)
                        if iv < 0:
                            raise ValueError("negative")
                        conn.execute(
                            f"UPDATE collections SET {_col} = ? WHERE id = ?",
                            (iv, col_id),
                        )
                    except (TypeError, ValueError):
                        raise HTTPException(400, f"{_bk} は 0 以上の整数を指定してください")
        if "rag_mode" in body:
            rm = body["rag_mode"]
            if rm in (None, ""):
                conn.execute("UPDATE collections SET rag_mode = NULL WHERE id = ?", (col_id,))
            else:
                rm_s = str(rm).strip().lower()
                if rm_s not in ("lite", "standard", "hq"):
                    raise HTTPException(400, "rag_mode は lite/standard/hq または空欄")
                conn.execute(
                    "UPDATE collections SET rag_mode = ? WHERE id = ?",
                    (rm_s, col_id),
                )
        if "raw_only" in body:
            # rawmode-partC: raw_only は作成時のみ指定可能。現在値と異なる変更要求は拒否する
            # (列不在の旧DBでは現在値=False として扱う)。
            _cur_raw_only = bool(dict(col).get("raw_only") or 0)
            if bool(body["raw_only"]) != _cur_raw_only:
                raise HTTPException(400, "raw_only は作成後変更できません")
        if "egress_allowed" in body:
            # DD-CYN-0132 4-4: 外へ出してよいかの印。この PUT は関数冒頭で _require_admin 済み
            #   なので admin のみ到達する。API は印を持つだけで、実際に送るかの宛先判定はしない
            #   (別層の役目)。
            conn.execute(
                "UPDATE collections SET egress_allowed = ? WHERE id = ?",
                (1 if bool(body["egress_allowed"]) else 0, col_id),
            )
        if bool(body.get("raw_mode", False)):
            # ga-finish-P4: raw_mode は更新の受け口でも明示的に拒否する
            # (従来は無視されて 200 を返していた)。
            raise HTTPException(400, "raw_mode (伏字なし取り込み) は廃止されました")
        if "file_ids" in body:
            # link-files と同じ 2 つの検証 (作成時 create_collection と同じ判定) を、file_ids の
            # 総入れ替え経路にも掛ける。従来は外部キーだけで、他 workspace の資料の混入と、
            # 機密区分ファイルの public コレクションへの結線ができた。DELETE より前に検証する。
            _put_ids = body["file_ids"]
            if not isinstance(_put_ids, list) or not all(isinstance(x, str) for x in _put_ids):
                raise HTTPException(400, "file_ids must be a list of ids")
            _existing_put_ids: list = []
            for fid in dict.fromkeys(_put_ids):
                if not conn.execute("SELECT 1 FROM files WHERE id = ?", (fid,)).fetchone():
                    # 実在しない file_id は従来どおり下の INSERT (外部キー違反 → 400) に任せる
                    continue
                _in_ws = conn.execute(
                    "SELECT 1 FROM files f JOIN workspace_sources ws ON ws.source_id = f.source_id "
                    "WHERE f.id = ? AND ws.workspace_id = ?",
                    (fid, col["workspace_id"]),
                ).fetchone()
                if not _in_ws:
                    raise HTTPException(
                        400, f"file_id '{fid}' は workspace '{col['workspace_id']}' に属していません"
                    )
                _existing_put_ids.append(fid)
            # 同じリクエストで access_level も変える場合は、変更後の値で判定する。
            _eff_access_put = body.get("access_level", dict(col).get("access_level")) or "public"
            if _existing_put_ids and _eff_access_put == "public":
                _ph_put = ",".join(["?"] * len(_existing_put_ids))
                _has_confidential_file = (
                    conn.execute(
                        f"SELECT 1 FROM files WHERE id IN ({_ph_put}) "
                        "AND (sensitivity IN ('confidential', 'restricted') "
                        "OR sensitivity_level = 'confidential') LIMIT 1",
                        _existing_put_ids,
                    ).fetchone()
                    is not None
                )
                if _has_confidential_file:
                    raise HTTPException(
                        400, "機密区分のファイルを含むため access_level='public' のコレクションには結線できません"
                    )
            # 同じ file_id の重複指定は、下の INSERT で主キー (collection_id, file_id) の一意制約違反になる。
            # 従来はそれも「存在しないfile_id」と返していて不正確だった。DB に触る前に検出して正しい文言で
            # 400 を返す (黙って重複を除くと API の挙動が変わるので、エラーのままにする)。SQLite / Postgres 共通。
            if len(set(_put_ids)) != len(_put_ids):
                raise HTTPException(400, "重複したfile_idが含まれています")
            conn.execute("DELETE FROM collection_files WHERE collection_id = ?", (col_id,))
            try:
                for fid in body["file_ids"]:
                    conn.execute(
                        "INSERT INTO collection_files (collection_id, file_id) VALUES (?, ?)",
                        (col_id, fid),
                    )
            except _INTEGRITY_ERRORS as e:
                # Postgres では失敗した取引が aborted のまま残るので、明示的に巻き戻してから返す
                # (finally の close も rollback するが、ここで確実に片付けておく)。
                try:
                    conn.rollback()
                except Exception:
                    pass
                # 一意制約違反 (psycopg: SQLSTATE 23505 / sqlite3: "UNIQUE constraint failed") と
                # 外部キー違反 (23503 / "FOREIGN KEY constraint failed") を区別する。
                # 重複は上で弾いているので、ここへ来る一意制約違反は同時更新との競合のみ。
                if getattr(e, "sqlstate", None) == "23505" or "UNIQUE constraint failed" in str(e):
                    raise HTTPException(400, "重複したfile_idが含まれています") from e
                raise HTTPException(400, "存在しないfile_idが含まれています") from e

        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": col_id}


@router.delete("/api/collections/{col_id}", response_model=None)
def delete_collection(request: Request, col_id: str):
    from server import _purge_chunks_for_collection, _purge_chunks_for_source

    _require_admin(request)
    conn = get_db()
    try:
        # fix-v3 (A2-F2): BM25 索引再構築のため削除前に workspace_id を取得しておく。
        _ws_row = conn.execute("SELECT workspace_id FROM collections WHERE id = ?", (col_id,)).fetchone()
        _ws_id = _ws_row["workspace_id"] if _ws_row else None
        # cascade-source-cleanup (key-vector-fix-20260721): 削除前に、この collection が
        # 使っていた取り込み元 (source) の候補を控える。削除後にどこからも使われて
        # いない source だけを連鎖削除する (他 collection が使う source は残す)。
        # DD-CYN-0115 M-11③: 消す作業がまとまり→資料→取り込み元の3段階になっていた
        # 実因はここに在り、案内の文言ではなく連鎖そのものが欠けていた。
        _cand_sources = [
            r["source_id"]
            for r in conn.execute(
                "SELECT DISTINCT f.source_id FROM collection_files cf "
                "JOIN files f ON f.id = cf.file_id WHERE cf.collection_id = ?",
                (col_id,),
            ).fetchall()
        ]
        _purge_chunks_for_collection(conn, col_id)
        conn.execute("DELETE FROM collection_files WHERE collection_id = ?", (col_id,))
        conn.execute("DELETE FROM collections WHERE id = ?", (col_id,))
        # fix-v3 (A2-F5): Collection 削除の監査ログ (delete_workspace/delete_source と対称化)。
        _log_audit(conn, "collection_deleted", col_id)
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
    # fix-v3 (A2-F2): 削除コミット後に BM25 索引を再構築する。従来は delete 経路が
    # rebuild_bm25_from_db を呼ばず in-memory BM25 索引が stale のままで、同一 WS に生
    # コレクションが残る限り削除済みチャンクが RAG 回答に残留する漏洩があった (実機再現済)。
    # publish 経路 (rag.py:1662) と同型の再構築をコミット後に行い索引を最新化する。
    if _ws_id:
        try:
            from rag import rebuild_bm25_from_db
            rebuild_bm25_from_db(_ws_id)
        except Exception:
            pass
    return {"ok": True}


# ─── Publish endpoints ──────────────────────────────────────


@router.get("/api/collections/{col_id}/publish-diff", response_model=None)
# ingest-eventloop-unblock-20260727: 対象ファイル全件の sha256 を取るため、コーパスが
#   大きいとイベントループ上で GB 単位の読み込みが走る。await は1つも無いので `def` にして
#   スレッドプールへ回す (挙動不変)。
def publish_diff(request: Request, col_id: str):
    """PORTABILITY FIX 20260527 Stage2 D-1: 再 Publish 前の差分チェック。

    file_hashes に記録された前回 Publish 時の sha256 とファイルシステム上の
    現在の sha256 を比較し、new/modified/deleted の件数を返す。
    フロントはこれを見て「差分なし」なら確認ダイアログを出す。
    """
    _require_admin(request)
    import hashlib as _hl
    import os as _os

    conn = get_db()
    try:
        col = conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Collection not found")
        files = conn.execute(
            "SELECT f.id, f.path FROM files f JOIN collection_files cf ON f.id = cf.file_id "
            "WHERE cf.collection_id = ?",
            (col_id,),
        ).fetchall()
        stored = {
            r["file_path"]: r["sha256"]
            for r in conn.execute(
                "SELECT file_path, sha256 FROM file_hashes WHERE collection_id = ?",
                (col_id,),
            ).fetchall()
        }
        current_fs: dict[str, str] = {}
        for f in files:
            fpath = f["path"]
            if not fpath or not _os.path.exists(fpath):
                continue
            try:
                with open(fpath, "rb") as fp:
                    current_fs[fpath] = _hl.sha256(fp.read()).hexdigest()
            except Exception:
                continue
        new_files = sum(1 for p in current_fs if p not in stored)
        modified_files = sum(1 for p, h in current_fs.items() if p in stored and stored[p] != h)
        deleted_files = sum(1 for p in stored if p not in current_fs)
        return {
            "has_changes": (new_files + modified_files + deleted_files) > 0,
            "new_files": new_files,
            "modified_files": modified_files,
            "deleted_files": deleted_files,
        }
    finally:
        conn.close()


# DD-CYN-0091 B (DD-CYN-0115 M-4 / P3-4): dup-publish-guard-20260710 (dupguard-port-20260713)
# の「同一ファイルの別コレクション重複publish 遮断」は撤去した。判定の包み関数
# _raise_if_duplicate_file_publish と 3 か所の呼び出し (publish / publish_stream /
# publish_async)、および rag.py 側の判定 2 関数もあわせて消してある (死材を残さない)。
#   根拠: 主キー (chunks.chunk_id) にまとまりの識別子を含めたため、同じファイルが別の
#   まとまりに在っても主キーはぶつからない。同一まとまりへの再publish は従来どおり
#   file_hashes の差分で更新される (姉妹系統の routers/collections.py:643-646 と同じ)。
@router.post("/api/collections/{col_id}/publish", response_model=None)
# ingest-eventloop-unblock-20260727 (GA ブロッカー①):
#   この関数は publish_collection_iter を await 無しで最後まで回す。PDF 抽出・チャンク化・
#   伏字・埋め込み・保存のすべてがイベントループ上で動くため、大型 PDF の取り込み中は
#   / も /api/ready も応答できなくなっていた (実測: 90サンプル中 89 が HTTP 000)。
#   画面は最初の進み具合が届くまで「準備中…」を出したまま待つ作りのため、この間は
#   取り込みが止まって見える (DD-CYN-0115 M-6 の backend 側の実因)。
#   本文に await は1つも無いので `async def` を `def` にするだけでよい。FastAPI が
#   同期の経路操作をスレッドプールへ回すため、実行内容・応答形・ガード・履歴記録は不変で
#   イベントループだけが解放される。SSE 版 publish_stream (下) は元から `def` で同じ実行模型。
#   なお berth では queue 有効時に publish_jobs を 1 秒間隔でポーリングする待ち合わせが
#   この関数の中に在り、その time.sleep もイベントループ上で回っていた (同じ理由で解消する)。
def publish(request: Request, col_id: str):
    from server import (
        compute_exclude_paths_for_collection,
        _resolve_collection_chunking,
        _finalize_publish_success,
        row_to_dict,
        logger,
    )
    from rag import publish_collection
    from pipeline_types import PipelineResult

    _require_admin(request)
    conn = get_db()
    try:
        col = conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Collection not found")

        file_rows = conn.execute(
            "SELECT f.path FROM files f JOIN collection_files cf ON f.id = cf.file_id WHERE cf.collection_id = ?",
            (col_id,),
        ).fetchall()
        file_paths = [r["path"] for r in file_rows]

        if not file_paths:
            raise HTTPException(400, "Collectionにファイルがありません。ファイルを追加してからPublishしてください。")

        # Phase 2: queue 有効なら enqueue し、完了までポーリングして collection を返す（同じ EP・同じ返却形）。
        from core.job_queue import queue_enabled as _queue_enabled, enqueue_or_defer as _enqueue

        if _queue_enabled():
            job_id = new_id()
            conn.execute(
                "INSERT INTO publish_jobs (id, collection_id, status, total, message) VALUES (?, ?, 'pending', ?, ?)",
                (job_id, col_id, len(file_paths), "Queued"),
            )
            conn.commit()
            conn.close()  # ポーリング中に接続を抱えない（close は冪等・finally の再 close は無害）
            # DD-CYN-0116 Q-3: 積めなくても 500 にしない。pending 行が正本として残り、
            # Redis 復旧後に worker の掃き寄せが拾う。ここはそのままポーリングを続ける。
            _enqueue({"id": job_id, "type": "publish", "col_id": col_id})
            import time as _time_q

            deadline = _time_q.time() + 3600
            while _time_q.time() < deadline:
                _c = get_db()
                try:
                    jr = _c.execute("SELECT status FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
                finally:
                    _c.close()
                if jr and jr["status"] in ("completed", "failed", "stopped"):
                    break
                _time_q.sleep(1)
            _c = get_db()
            try:
                return row_to_dict(_c.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone())
            finally:
                _c.close()

        excluded_paths = compute_exclude_paths_for_collection(conn, col_id)
        workspace_id = col["workspace_id"]
        _ws_acl = conn.execute("SELECT acl_config FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        _pdf_mode = "fast"
        if _ws_acl and _ws_acl["acl_config"]:
            try:
                _pdf_mode = (json.loads(_ws_acl["acl_config"]) or {}).get("pdf_mode") or "fast"
            except Exception:
                _pdf_mode = "fast"

        conn.execute("UPDATE collections SET status = 'publishing' WHERE id = ?", (col_id,))
        conn.commit()

        import time as _time

        t_start = _time.perf_counter()
        publish_error = None
        _done_event = None
        try:
            _cs, _co = _resolve_collection_chunking(col_id)
            # allinone A4: 表示用 pipeline_result を SSE 経路と同じ per-publish 値に揃えるため done event を捕捉する。
            #   ラッパ publish_collection は chunk_count しか返さず、表示は _finalize_publish_success の
            #   workspace 全体累積になり SSE(per-publish) と食い違っていた(同期 excluded_count=43 vs SSE=1 等)。
            #   本ループは publish_collection と同一挙動(ラッパは本 iterator を drain するだけ・除外判定/履歴記録は不変)。
            from rag import publish_collection_iter as _publish_collection_iter
            for _ev in _publish_collection_iter(
                col_id, file_paths, chunk_size=_cs, chunk_overlap=_co, excluded_paths=excluded_paths, pdf_mode=_pdf_mode
            ):
                if _ev.get("stage") == "error":
                    raise Exception(_ev.get("message", "Publish失敗"))
                if _ev.get("stage") == "done":
                    _done_event = _ev
            chunk_count = int((_done_event or {}).get("chunk_count", 0) or 0)
            conn.execute(
                "UPDATE collections SET status = 'ready', chunk_count = ?, last_published_at = ? WHERE id = ?",
                (chunk_count, datetime.now().isoformat(timespec="seconds"), col_id),
            )
            _log_audit(conn, "collection_published", target=col_id, detail=f"Published with {chunk_count} chunks")
        except Exception as e:
            publish_error = str(e)
            conn.execute("UPDATE collections SET status = 'failed' WHERE id = ?", (col_id,))
            _log_audit(conn, "collection_publish_failed", target=col_id, detail=str(e), result="failure")
            logger.exception(f"publish failed: {e}")

        elapsed = _time.perf_counter() - t_start
        conn.commit()

        history_row = None
        if publish_error is None:
            history_row = _finalize_publish_success(conn, col_id, workspace_id or "", file_paths, elapsed)
            conn.commit()

        result = row_to_dict(conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone())
    finally:
        conn.close()
    if history_row is not None:
        # allinone A4: 表示は per-publish(done event) を優先し SSE と一致させる。
        #   _finalize_publish_success(=workspace 全体累積) は publish_history 記録用に不変のまま使い、
        #   ここでの「今回の publish」表示だけを done event の per-publish 値で上書きする(スキーマ不変・値訂正)。
        _de = _done_event or {}
        pipeline_result = PipelineResult(
            workspace_id=history_row["workspace_id"],
            doc_count=history_row["doc_count"],
            chunk_count=int(_de.get("chunk_count", history_row["chunk_count"]) or 0),
            pii_count=int(_de.get("pii_count", history_row["pii_count"]) or 0),
            excluded_count=int(_de.get("excluded_count", history_row["excluded_count"]) or 0),
            avg_chunk_chars=history_row["avg_chunk_chars"],
            elapsed_seconds=history_row["elapsed_seconds"],
        )
        result["pipeline_result"] = pipeline_result.to_display()
    # vision-placeholder-warn-20260727: 同期版 publish でも、中身が入らなかったファイルを
    #   応答へ返し、取り込み操作ログへ残す (publish-summary はここから読む)。
    _ph_files = list((_done_event or {}).get("placeholder_only_files") or [])
    result["placeholder_only_count"] = len(_ph_files)
    result["placeholder_only_files"] = _ph_files
    if (_done_event or {}).get("placeholder_warning"):
        result["placeholder_warning"] = _done_event["placeholder_warning"]
    try:
        from server import _log_processing as _lp

        _lp(
            "ingest",
            f"完了(同期): {int((_done_event or {}).get('chunk_count', 0) or 0)} チャンクを索引化",
            level="success", job_id=f"sync-{col_id}",
            metadata={
                "stage": "done",
                "chunk_count": int((_done_event or {}).get("chunk_count", 0) or 0),
                "collection_id": col_id,
                "placeholder_only_files": _ph_files,
                # DD-CYN-0115 H-5 (additive): 同期経路でも6工程の内訳を残す
                "skipped_details": list((_done_event or {}).get("skipped_details") or []),
                "stage_seconds": dict((_done_event or {}).get("stage_seconds") or {}),
            },
        )
    except Exception:
        pass
    return result


def _publish_event_from_row(row) -> dict:
    """publish_jobs 行を publish_collection_iter 互換のイベント dict へ写像する。"""
    status = row["status"]
    stage = row["stage"] or ""
    if status == "completed":
        return {
            "stage": "done", "current": row["progress"], "total": row["total"],
            "chunk_count": row["progress"], "message": row["message"] or "完了しました",
        }
    if status == "failed":
        return {"stage": "error", "message": row["message"] or "失敗しました"}
    if status == "stopped":
        return {"stage": "stopped", "message": row["message"] or "停止しました"}
    return {
        "stage": stage or "running", "current": row["progress"], "total": row["total"],
        "message": row["message"] or "",
    }


def _tail_publish_job_sse(job_id: str, col_id: str):
    """publish_jobs（DB 正本）を尾追いし、進捗を SSE として配信する。

    実処理は別プロセスの worker が担い、ここは DB を読んで流すだけ（status/履歴の二重書きをしない）。
    クライアント切断後も worker は処理を継続する（durability の狙いどおり）。
    """
    import time as _time

    last = None
    terminal = {"completed", "failed", "stopped"}
    deadline = _time.time() + 3600  # 保険: worker 未起動でも無限ブロックしない
    while _time.time() < deadline:
        c = get_db()
        try:
            row = c.execute(
                "SELECT status, stage, progress, total, message FROM publish_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        finally:
            c.close()
        if row is None:
            yield f"data: {json.dumps({'stage': 'error', 'message': 'ジョブが見つかりません'}, ensure_ascii=False)}\n\n"
            return
        sig = (row["status"], row["stage"], row["progress"], row["total"])
        if sig != last:
            yield f"data: {json.dumps(_publish_event_from_row(row), ensure_ascii=False)}\n\n"
            last = sig
        if row["status"] in terminal:
            return
        _time.sleep(1)


@router.get("/api/collections/{col_id}/publish/stream", response_model=None)
def publish_stream(request: Request, col_id: str):
    """SSEでPublish進捗をリアルタイム配信する。"""
    from server import (
        compute_exclude_paths_for_collection,
        _resolve_collection_chunking,
        _finalize_publish_success,
    )
    from rag import publish_collection_iter

    _require_admin(request)
    conn = get_db()
    try:
        col = conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Collection not found")
        file_rows = conn.execute(
            "SELECT f.path FROM files f JOIN collection_files cf ON f.id = cf.file_id WHERE cf.collection_id = ?",
            (col_id,),
        ).fetchall()
        file_paths = [r["path"] for r in file_rows]
        excluded_paths = compute_exclude_paths_for_collection(conn, col_id)
        _ws_acl = conn.execute("SELECT acl_config FROM workspaces WHERE id = ?", (col["workspace_id"],)).fetchone()
        _pdf_mode = "fast"
        if _ws_acl and _ws_acl["acl_config"]:
            try:
                _pdf_mode = (json.loads(_ws_acl["acl_config"]) or {}).get("pdf_mode") or "fast"
            except Exception:
                _pdf_mode = "fast"
    finally:
        conn.close()

    if not file_paths:
        raise HTTPException(400, "Collectionにファイルがありません。ファイルを追加してからPublishしてください。")

    # Phase 2: queue 有効なら enqueue し、publish_jobs を尾追いして SSE 配信する（同じ EP・同じ SSE 形）。
    # 実処理は worker が同一の publish_collection_iter で実行（伏字/暗号化は同一適用）。
    from core.job_queue import queue_enabled as _queue_enabled, enqueue_or_defer as _enqueue

    if _queue_enabled():
        job_id = new_id()
        _c = get_db()
        try:
            _c.execute(
                "INSERT INTO publish_jobs (id, collection_id, status, total, message) VALUES (?, ?, 'pending', ?, ?)",
                (job_id, col_id, len(file_paths), "Queued"),
            )
            _c.commit()
        finally:
            _c.close()
        # DD-CYN-0116 Q-3: 積めなくても 500 にしない。pending 行が正本として残り、
        # Redis 復旧後に worker の掃き寄せが拾う。SSE は publish_jobs を尾追いする
        # 作りなので、拾われた時点から進捗が流れ始める。
        _enqueue({"id": job_id, "type": "publish", "col_id": col_id})
        return StreamingResponse(
            _tail_publish_job_sse(job_id, col_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def event_generator():
        import time as _time

        t_start = _time.perf_counter()
        c = get_db()
        try:
            c.execute("UPDATE collections SET status = 'publishing' WHERE id = ?", (col_id,))
            c.commit()
        finally:
            c.close()

        final_event = None
        try:
            _cs, _co = _resolve_collection_chunking(col_id)
            for event in publish_collection_iter(
                col_id, file_paths, chunk_size=_cs, chunk_overlap=_co, excluded_paths=excluded_paths, pdf_mode=_pdf_mode
            ):
                final_event = event
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("stage") in ("error", "stopped"):
                    break

            elapsed = _time.perf_counter() - t_start
            # DB接続リーク修正: 本区間で例外が起きると except 節で変数 c が
            # 再束縛され元 conn の close 手段が消えるため、try/finally で
            # 再束縛前に確実に close する (処理順・挙動は不変)。
            c = get_db()
            try:
                stage = (final_event or {}).get("stage")
                if stage == "done":
                    chunk_count = final_event.get("chunk_count", 0)
                    c.execute(
                        "UPDATE collections SET status = 'ready', chunk_count = ?, last_published_at = ? WHERE id = ?",
                        (chunk_count, datetime.now().isoformat(timespec="seconds"), col_id),
                    )
                    _log_audit(c, "collection_published", target=col_id, detail=f"Published with {chunk_count} chunks")
                    ws_row = c.execute("SELECT workspace_id FROM collections WHERE id = ?", (col_id,)).fetchone()
                    ws_id = ws_row["workspace_id"] if ws_row else ""
                    _finalize_publish_success(c, col_id, ws_id, file_paths, elapsed)
                elif stage == "stopped":
                    c.execute("UPDATE collections SET status = 'draft' WHERE id = ?", (col_id,))
                    _log_audit(
                        c,
                        "collection_publish_stopped",
                        target=col_id,
                        detail=final_event.get("message", "stopped"),
                        result="failure",
                    )
                else:
                    c.execute("UPDATE collections SET status = 'failed' WHERE id = ?", (col_id,))
                    detail = (final_event or {}).get("message", "unknown error")
                    _log_audit(c, "collection_publish_failed", target=col_id, detail=detail, result="failure")
                c.commit()
            finally:
                c.close()
        except Exception as e:
            # FIX-019: SSE エラーメッセージ汎用化 (内部パス/SQL リテラル漏洩防止)
            import logging as _logging_for_err
            import uuid as _uuid_for_err

            error_id = _uuid_for_err.uuid4().hex[:12]
            _logging_for_err.getLogger("cynovela.collections").exception(
                f"publish stream 内部エラー error_id={error_id} col_id={col_id}: {e}"
            )
            err = {"stage": "error", "message": "内部エラー", "error_id": error_id}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            try:
                c = get_db()
                try:
                    c.execute("UPDATE collections SET status = 'failed' WHERE id = ?", (col_id,))
                    c.commit()
                finally:
                    c.close()
            except Exception:
                pass
        finally:
            # FIX-046: SSE クライアント切断時の status='publishing' 固着回避 (safety-net)。
            # 正常完了 / stopped / failed のいずれにも遷移していない場合のみ failed に戻す。
            try:
                _c_safety = get_db()
                try:
                    _row = _c_safety.execute("SELECT status FROM collections WHERE id = ?", (col_id,)).fetchone()
                    if _row and _row["status"] == "publishing":
                        _c_safety.execute(
                            "UPDATE collections SET status = 'failed' WHERE id = ?",
                            (col_id,),
                        )
                        _c_safety.commit()
                finally:
                    _c_safety.close()
            except Exception:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/collections/{col_id}/publish/stop", response_model=None)
def stop_publish(request: Request, col_id: str):
    """P2-C / Phase 2: 進行中の Publish に停止を要求する。

    停止/キャンセルは publish_jobs.cancel_requested（DB 正本）へ書く。別プロセスの worker は
    これを定期ポーリングして実 Publish を中断する（プロセス内メモリ依存を解消）。
    standalone（プロセス内スレッド）では同一プロセスの in-process Event も併せて立て即時停止させる。
    """
    from rag import request_publish_stop

    _require_admin(request)
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE publish_jobs SET cancel_requested = 1 "
            "WHERE collection_id = ? AND status IN ('pending', 'running')",
            (col_id,),
        )
        conn.commit()
        affected = cur.rowcount
    finally:
        conn.close()
    in_proc = request_publish_stop(col_id)
    if affected > 0 or in_proc:
        return {"status": "stopping", "collection_id": col_id}
    return {"status": "not_running", "collection_id": col_id}


@router.post("/api/collections/{col_id}/publish/recover", response_model=None)
def recover_publishing_collection(request: Request, col_id: str):
    """Phase 0c B-2(i): "publishing" で固着した Collection を draft に戻す。"""
    from rag import request_publish_stop

    _require_admin(request)
    conn = get_db()
    try:
        col = conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Collection not found")
        if col["status"] != "publishing":
            return {
                "ok": True,
                "collection_id": col_id,
                "previous_status": col["status"],
                "recovered": False,
                "message": f"recover 不要: status={col['status']}",
            }

        try:
            request_publish_stop(col_id)
        except Exception:
            pass

        conn.execute("UPDATE collections SET status = 'draft' WHERE id = ?", (col_id,))
        affected = conn.execute(
            "UPDATE publish_jobs SET status = 'failed', "
            "error = COALESCE(error, 'recovered from stuck publishing'), "
            "updated_at = datetime('now') "
            "WHERE collection_id = ? AND status IN ('pending', 'running')",
            (col_id,),
        ).rowcount
        _log_audit(
            conn,
            "collection_publish_recovered",
            target=col_id,
            detail=f"recovered from publishing → draft (affected jobs: {affected})",
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "collection_id": col_id,
        "previous_status": "publishing",
        "recovered": True,
        "affected_jobs": affected,
    }


@router.post("/api/collections/{col_id}/publish/async", response_model=None)
async def publish_async(request: Request, col_id: str):
    """非同期 Publish: job_id を発行して即座に返す。"""
    from server import (
        compute_exclude_paths_for_collection,
        _resolve_collection_chunking,
        _run_publish_background,
    )

    _require_admin(request)
    conn = get_db()
    try:
        col = conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Collection not found")

        file_rows = conn.execute(
            "SELECT f.path FROM files f JOIN collection_files cf ON f.id = cf.file_id " "WHERE cf.collection_id = ?",
            (col_id,),
        ).fetchall()
        file_paths = [r["path"] for r in file_rows]
        if not file_paths:
            raise HTTPException(400, "Collectionにファイルがありません。ファイルを追加してからPublishしてください。")

        _existing = conn.execute(
            "SELECT id FROM publish_jobs WHERE collection_id = ? AND status IN ('pending','running')",
            (col_id,),
        ).fetchone()
        if _existing:
            raise HTTPException(409, "Publish already in progress for this collection")

        excluded_paths = compute_exclude_paths_for_collection(conn, col_id)
        _cs, _co = _resolve_collection_chunking(col_id)
        # P1-5: 非同期 publish でも Workspace の pdf_mode を反映する (手動 publish と同じ取得方法)
        _ws_acl = conn.execute("SELECT acl_config FROM workspaces WHERE id = ?", (col["workspace_id"],)).fetchone()
        _pdf_mode = "fast"
        if _ws_acl and _ws_acl["acl_config"]:
            try:
                _pdf_mode = (json.loads(_ws_acl["acl_config"]) or {}).get("pdf_mode") or "fast"
            except Exception:
                _pdf_mode = "fast"

        job_id = new_id()
        conn.execute(
            "INSERT INTO publish_jobs (id, collection_id, status, total, message) " "VALUES (?, ?, 'pending', ?, ?)",
            (job_id, col_id, len(file_paths), "Queued"),
        )
        conn.commit()
    finally:
        conn.close()

    # Phase 2: queue 有効なら Redis に積む（別プロセスの worker が消費＝durable）。
    # 無効（standalone 既定）なら従来どおりプロセス内スレッドで実行＝挙動不変。
    from core.job_queue import queue_enabled as _queue_enabled, enqueue_or_defer as _enqueue

    if _queue_enabled():
        # DD-CYN-0116 Q-3: 積めなくても 500 にしない。publish_jobs の pending 行が
        # 正本として残り、Redis 復旧後に worker の掃き寄せが拾う。
        _enqueue({"id": job_id, "type": "publish", "col_id": col_id})
    else:
        threading.Thread(
            target=_run_publish_background,
            args=(job_id, col_id, file_paths, excluded_paths, _cs, _co, _pdf_mode),
            daemon=True,
        ).start()

    return {"job_id": job_id, "collection_id": col_id, "status": "pending"}


# ─── Collection lock ────────────────────────────────────────


@router.post("/api/collections/{col_id}/lock", response_model=None)
def acquire_collection_lock(col_id: str, request: Request):
    """PHASE UX-3: Publish 同時実行防止のためのコレクションロック取得。"""
    from datetime import timedelta

    _require_admin(request)
    c = get_db()
    try:
        existing = c.execute(
            "SELECT locked_by, locked_at FROM collection_locks WHERE collection_id = ?",
            (col_id,),
        ).fetchone()
        if existing:
            try:
                locked_at = datetime.fromisoformat(existing["locked_at"])
            except Exception:
                locked_at = datetime.now() - timedelta(hours=3)
            if datetime.now() - locked_at < timedelta(hours=2):
                raise HTTPException(
                    status_code=423,
                    detail=f"既に locked_by={existing['locked_by']} がロック中 (locked_at={existing['locked_at']})",
                )
        locked_by = request.client.host if request.client else "unknown"
        c.execute(
            "INSERT INTO collection_locks (collection_id, locked_by) VALUES (?, ?) "
            "ON CONFLICT(collection_id) DO UPDATE SET "
            "locked_by=excluded.locked_by, locked_at=datetime('now')",
            (col_id, locked_by),
        )
        c.commit()
    finally:
        c.close()
    return {"ok": True, "collection_id": col_id, "locked_by": locked_by}


@router.delete("/api/collections/{col_id}/lock", response_model=None)
def release_collection_lock(request: Request, col_id: str):
    """PHASE UX-3: コレクションロック解放。"""
    _require_admin(request)
    c = get_db()
    try:
        c.execute("DELETE FROM collection_locks WHERE collection_id = ?", (col_id,))
        c.commit()
    finally:
        c.close()
    return {"ok": True}


# DD-CYN-0132 4-1 (姉妹系統の unlinked-files-20260817 の移植): 再スキャンで見つかった新しい
# ファイルは、どのコレクションにも自動では紐づけない (利用者が意図しない資料が黙って
# 取り込まれると、マスキングと権限の設計に触れる)。代わりに「紐づいていないファイル」を
# 見せる口と、選んで紐づける口を置く。紐づけただけでは公開しない。公開は従来どおり
# 利用者が Publish を押す。姉妹系統 routers/collections.py の同名2口と同じ振る舞い。
@router.get("/api/collections/{col_id}/unlinked-files", response_model=None, summary="未結線ファイル一覧 (admin 限定)")
def get_unlinked_files(request: Request, col_id: str):
    _require_admin(request)
    conn = get_db()
    try:
        col = conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Collection not found")
        rows = conn.execute(
            "SELECT f.id, f.path, f.name FROM files f "
            "JOIN workspace_sources ws ON ws.source_id = f.source_id "
            "WHERE ws.workspace_id = ? "
            "AND f.id NOT IN (SELECT file_id FROM collection_files WHERE collection_id = ?) "
            "ORDER BY f.path",
            (col["workspace_id"], col_id),
        ).fetchall()
        files = [{"id": r["id"], "path": r["path"], "name": r["name"]} for r in rows]
        return {"count": len(files), "files": files}
    finally:
        conn.close()


@router.post("/api/collections/{col_id}/link-files", response_model=None, summary="ファイルをコレクションへ結線 (admin 限定)")
async def link_files(col_id: str, request: Request):
    _require_admin(request)
    body = await parse_body_pydantic(request)
    file_ids = body.get("file_ids")
    if not isinstance(file_ids, list) or not all(isinstance(x, str) for x in file_ids):
        raise HTTPException(400, "file_ids must be a list of ids")
    conn = get_db()
    try:
        col = conn.execute("SELECT * FROM collections WHERE id = ?", (col_id,)).fetchone()
        if not col:
            raise HTTPException(404, "Collection not found")
        if col["status"] == "publishing":
            raise HTTPException(409, "Publish進行中はファイル構成を変更できません")
        already = {
            r["file_id"]
            for r in conn.execute(
                "SELECT file_id FROM collection_files WHERE collection_id = ?", (col_id,)
            ).fetchall()
        }
        # 未結線の同じ file_id の重複指定は、下の INSERT で主キー (collection_id, file_id) の一意制約違反に
        # なる (already はループ内で更新されない)。従来はそれも「存在しないfile_id」と返していて不正確だった。
        # PUT /api/collections/{id} と同じく DB に書く前に検出して正しい文言で 400 を返す (黙って重複を
        # 除かない)。結線済み id (already) は従来どおり読み飛ばすので、その再指定・重複は対象外のまま。
        _to_link = [fid for fid in file_ids if fid not in already]
        if len(set(_to_link)) != len(_to_link):
            raise HTTPException(400, "重複したfile_idが含まれています")
        # 作成時 (create_collection) と同じ 2 つの検証を結線経路にも掛ける。
        # 従来この経路は存在確認 (外部キー) だけで、(1) 他 workspace の資料を混入でき、
        # (2) 機密区分のファイルを public コレクションへ結線できた (作成時の C-1(a) を素通り)。
        _new_ids = [fid for fid in dict.fromkeys(file_ids) if fid not in already]
        _existing_new_ids: list = []
        for fid in _new_ids:
            if not conn.execute("SELECT 1 FROM files WHERE id = ?", (fid,)).fetchone():
                # 実在しない file_id は従来どおり下の INSERT (外部キー違反 → 400) に任せる
                continue
            # 所属検証: 作成時と同じ JOIN (workspace_sources 経由)
            _in_ws = conn.execute(
                "SELECT 1 FROM files f JOIN workspace_sources ws ON ws.source_id = f.source_id "
                "WHERE f.id = ? AND ws.workspace_id = ?",
                (fid, col["workspace_id"]),
            ).fetchone()
            if not _in_ws:
                raise HTTPException(
                    400, f"file_id '{fid}' は workspace '{col['workspace_id']}' に属していません"
                )
            _existing_new_ids.append(fid)
        # 機密度検証: 作成時 C-1(a) と同じ判定列 (sensitivity / sensitivity_level)。
        # 作成時は「機密ファイルを含む明示 public」を 400 で拒否するので、ここでも
        # public コレクションへの機密ファイル結線を同じく 400 で拒否する (黙って格上げしない)。
        if _existing_new_ids and (col["access_level"] or "public") == "public":
            _ph_lf = ",".join(["?"] * len(_existing_new_ids))
            _has_confidential_file = (
                conn.execute(
                    f"SELECT 1 FROM files WHERE id IN ({_ph_lf}) "
                    "AND (sensitivity IN ('confidential', 'restricted') "
                    "OR sensitivity_level = 'confidential') LIMIT 1",
                    _existing_new_ids,
                ).fetchone()
                is not None
            )
            if _has_confidential_file:
                raise HTTPException(
                    400, "機密区分のファイルを含むため access_level='public' のコレクションには結線できません"
                )
        try:
            for fid in file_ids:
                if fid in already:
                    continue
                conn.execute(
                    "INSERT INTO collection_files (collection_id, file_id) VALUES (?, ?)",
                    (col_id, fid),
                )
        except _INTEGRITY_ERRORS as e:
            # Postgres では失敗した取引が aborted のまま残るので、明示的に巻き戻してから返す
            # (finally の close も rollback するが、ここで確実に片付けておく)。
            try:
                conn.rollback()
            except Exception:
                pass
            # 一意制約違反 (psycopg: SQLSTATE 23505 / sqlite3: "UNIQUE constraint failed") と
            # 外部キー違反 (23503 / "FOREIGN KEY constraint failed") を区別する。
            # 重複は上で弾いているので、ここへ来る一意制約違反は同時更新との競合のみ。
            if getattr(e, "sqlstate", None) == "23505" or "UNIQUE constraint failed" in str(e):
                raise HTTPException(400, "重複したfile_idが含まれています") from e
            raise HTTPException(400, "存在しないfile_idが含まれています") from e
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "id": col_id}

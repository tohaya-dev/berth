"""Data sources endpoints (/api/sources/*)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request

from db import get_db, new_id
from core.auth import _require_admin, _require_authenticated
from core.audit import _log_audit

# ga-close-v3 PartA (2026-07-27): アップロード保存先 (_uploads_root) と
# /api/sources/upload を撤去した。取り込みは取り込みフォルダ経由に一本化する。

router = APIRouter(tags=["sources"])


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


_INTEGRITY_ERRORS = _integrity_error_types()


@router.get("/api/sources", response_model=None)
def list_sources(
    request: Request,
    limit: int | None = None,
    offset: int = 0,
    q: str | None = None,
    workspace_id: str | None = None,
    sort: str = "created_at_desc",
):
    """GUI修正2 #35: archived_at IS NULL のもののみ返す。
    BETA-pagination: limit/offset/q/workspace_id でページネーション・検索を有効化。"""
    from server import rows_to_list

    user = _require_authenticated(request)
    if limit is not None and limit not in (10, 20, 50, 100):
        raise HTTPException(status_code=400, detail="limitは10/20/50/100のいずれかです")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offsetは0以上です")
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (接続リークで書き込みロック残留を防ぐ)
    try:
        where_parts: list[str] = ["s.archived_at IS NULL"]
        params: list = []
        if q:
            where_parts.append("s.name LIKE ?")
            params.append(f"%{q}%")
        if workspace_id:
            where_parts.append("s.id IN (SELECT source_id FROM workspace_sources WHERE workspace_id = ?)")
            params.append(workspace_id)
        # authz-fix-v1: 非admin は自分の所属WSに紐づく source のみ (admin は全件=広域維持)。
        if (user or {}).get("role") != "admin":
            where_parts.append(
                "s.id IN (SELECT source_id FROM workspace_sources WHERE workspace_id IN "
                "(SELECT workspace_id FROM workspace_users WHERE user_id = ?))"
            )
            params.append((user or {}).get("id"))
        where_sql = "WHERE " + " AND ".join(where_parts)

        total = None
        if limit is not None:
            total = conn.execute(f"SELECT COUNT(*) FROM sources s {where_sql}", params).fetchone()[0]

        pagination_sql = ""
        pagination_params: list = []
        if limit is not None:
            pagination_sql = " LIMIT ? OFFSET ?"
            pagination_params = [limit, offset]
        # P1-7: sort はSQLインジェクション防止のためホワイトリストで解決する（生値は絶対に埋め込まない）
        _SORT_MAP = {
            "created_at_desc": "s.created_at DESC",
            "created_at_asc": "s.created_at ASC",
            "name_asc": "s.name ASC",
            "name_desc": "s.name DESC",
        }
        order_clause = _SORT_MAP.get(sort, "s.created_at DESC")
        sources = rows_to_list(
            conn.execute(
                f"SELECT s.* FROM sources s {where_sql} " f"ORDER BY {order_clause} {pagination_sql}",
                params + pagination_params,
            ).fetchall()
        )
    finally:
        conn.close()
    if limit is None:
        return sources
    return {"items": sources, "total": total, "limit": limit, "offset": offset}


@router.get("/api/sources/{source_id}", response_model=None)
def get_source(request: Request, source_id: str):
    _require_admin(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (接続リークで書き込みロック残留を防ぐ)
    try:
        row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            raise HTTPException(404, "source not found")
        out = dict(row)
        # fix062 A5: 1 source は workspace_sources 中間テーブルで複数 workspace に所属可能。
        # workspace_id 単数ではなく workspace_ids 配列で返す。
        ws_rows = conn.execute(
            "SELECT workspace_id FROM workspace_sources WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        out["workspace_ids"] = [r["workspace_id"] for r in ws_rows]
    finally:
        conn.close()
    return out


def _normalize_source_path(path: str) -> str:
    """C-5: source path の正規化 (登録・重複判定で使う共通形)。

    /a/b と /a/b/ のような表記揺れを1つの形へ落とす。保存にもこの形を使う。
    """
    return os.path.normpath(path)


def _find_existing_source(conn, normalized_path: str):
    """C-5: 同一正規化パスの既存 source 行を返す (無ければ None)。

    既存データには生パス (正規化前) が保存されている可能性があるため、
    比較は両辺を正規化して行う。sources は少数運用なので全行比較で足りる
    (完全な UNIQUE 制約化は既存データ衝突リスクがあるためアプリ層判定に留める)。
    """
    rows = conn.execute("SELECT id, name, path FROM sources").fetchall()
    for r in rows:
        if os.path.normpath(r["path"] or "") == normalized_path:
            return r
    return None


def _resolve_source_path(normalized_path: str) -> str:
    """source path を境界判定用の実体パスへ解決する。

    走査側 (server.py の _do_scan ほか) は abspath(expanduser(path)) で読むので、同じ順で
    解決し、その上で realpath を掛けて `..` と symlink を畳む。存在しないパスでも
    realpath は例外にせず、存在する所までを解決して残りを繋ぐ (存在確認はここではしない)。
    """
    return os.path.realpath(os.path.abspath(os.path.expanduser(normalized_path)))


def _source_boundary_roots() -> list:
    """POST /api/sources が受け付ける根 (実体パス) の一覧。GET /api/browse と同じ決め方。

    1. 足してある取り込み元 (_load_ingest_roots) の host_path
    2. それが空のときだけ /app/ingest (在れば。コンテナ / Kubernetes 形態)、無ければホーム
    routers/files.py の browse_folders と同じ規則をここに持つ (files.py 側がこの
    モジュールを関数内 import しているため、循環を避けて逆向きの import はしない)。
    """
    try:
        _roots = _load_ingest_roots() or []
    except Exception:
        _roots = []
    _out: list = []
    for _r in _roots:
        _hp = (_r.get("host_path") or "").strip() if isinstance(_r, dict) else ""
        if not _hp:
            continue
        try:
            _out.append(os.path.realpath(os.path.abspath(os.path.expanduser(_hp))))
        except Exception:
            continue
    if not _out:
        _ingest_box = "/app/ingest"
        if os.path.isdir(_ingest_box):
            _out.append(os.path.realpath(_ingest_box))
        else:
            _out.append(os.path.realpath(os.path.expanduser("~")))
    return _out


def _is_inside_root(target: str, root: str) -> bool:
    """target が root 自身か、その配下か (前方一致は区切り文字つきで見る)。"""
    if target == root:
        return True
    _prefix = root if root.endswith(os.sep) else root + os.sep
    return target.startswith(_prefix)


def _assert_source_path_in_ingest_roots(normalized_path: str) -> str:
    """取り込み元の外を指す source path を 403 で断る。中なら解決済みの実体パスを返す。

    `/app/ingest/../etc`・`/app/ingest/x/../../etc`・根の外へ逃げる symlink・根の外の
    絶対パスは、いずれも realpath 後に根の配下でなくなるのでここで落ちる。
    """
    try:
        _target = _resolve_source_path(normalized_path)
    except Exception as e:
        raise HTTPException(400, f"Invalid path: {e}") from e
    for _root in _source_boundary_roots():
        if _is_inside_root(_target, _root):
            return _target
    raise HTTPException(403, "この場所を使うには取り込み元に足してください")


@router.post("/api/sources", response_model=None)
async def create_source(request: Request):
    from server import _start_scan

    _require_admin(request)
    body = await parse_body_pydantic(request)
    name = body.get("name")
    path = body.get("path")
    auto_scan = body.get("auto_scan", True)
    if not name or not path:
        raise HTTPException(400, "name and path are required")
    # PHASE A: パストラバーサル / 機密パス拒否
    _lower = path.strip().lower()
    _forbidden_schemes = ("file://", "data://", "ftp://", "javascript:")
    if any(_lower.startswith(s) for s in _forbidden_schemes):
        raise HTTPException(400, "URL scheme is not allowed in path")
    if "\\" in path:
        raise HTTPException(400, "Windows path separator is not allowed")
    _normalized = _normalize_source_path(path)
    _forbidden_prefixes = (
        "/etc",
        "/var/root",
        "/var/db",
        "/private/etc",
        "/private/var/root",
        "/root",
        "/sys",
        "/proc",
        "/boot",
    )
    _forbidden_substrings = (
        "/.ssh",
        "/.aws",
        "/.gnupg",
        "/Library/Keychains",
        "/Library/Application Support/com.apple.sharedfilelist",
        "/.kube",
        "/.config/gh",
        "/.netrc",
    )
    if any(_normalized == p or _normalized.startswith(p + "/") for p in _forbidden_prefixes):
        raise HTTPException(400, f"system path is not allowed: {_normalized}")
    if any(s in _normalized for s in _forbidden_substrings):
        raise HTTPException(400, f"sensitive path is not allowed: {_normalized}")
    if ".." in path.split(os.sep):
        raise HTTPException(400, "relative path traversal is not allowed")
    # ingest-path-fullfix-20260921 C-1: フォルダ選択 (GET /api/browse) と同じ境界を登録側にも掛ける。
    # 従来は禁止接頭辞と `..` だけを見ており、取り込み元の外の絶対パスや、根の外へ逃げる
    # symlink をそのまま登録できた。DB へ書く前に判定する (保存値は従来どおり正規化済みパス)。
    _assert_source_path_in_ingest_roots(_normalized)
    sid = new_id()
    conn = get_db()
    # connleak-fix-20260709 (アプリ版検証済み修正の逐語ポート):
    # 旧実装は INSERT が例外 (例: UNIQUE constraint failed: sources.name) を投げると
    # conn を close せずに抜けていた。SQLite は INSERT 時に暗黙 BEGIN で書き込み
    # トランザクションを開くため、close 漏れ = 書き込みロック残留となり、以後の
    # 全書き込みが busy_timeout(30s) 超過の "database is locked" になっていた
    # (フロントでは「追加失敗: Failed to fetch」)。同名 source は 409 で穏当に弾く。
    try:
        # C-5: 同一正規化パスの既存 source があれば二重登録として 409 で案内する
        # (/a/b と /a/b/ の表記揺れで別 source が2つできるのを防ぐ)。
        _dup = _find_existing_source(conn, _normalized)
        if _dup:
            raise HTTPException(
                409,
                f"同じ場所のソースが既に存在します: id={_dup['id']} name={_dup['name']}",
            )
        # ingest-path-fullfix-20260921 C-2: 同名は INSERT の前に SELECT で弾く (第一線)。
        # Postgres では一意制約違反の例外が sqlite3.IntegrityError ではないため、従来は
        # 同名登録が 500 になっていた。競合で擦り抜けた分は下の except が拾う (第二線)。
        _same_name = conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()
        if _same_name:
            raise HTTPException(409, f"同名のソースが既に存在します: {name}")
        # C-5: 保存は正規化済みパスで行う (従来は生 path を保存していた)。
        conn.execute(
            "INSERT INTO sources (id, name, path) VALUES (?, ?, ?)",
            (sid, name, _normalized),
        )
        _log_audit(conn, "source_created", sid, name)
        conn.commit()
    except _INTEGRITY_ERRORS as e:
        # Postgres では失敗した取引が aborted のまま残るので、明示的に巻き戻してから返す
        # (finally の close も rollback するが、ここで確実に片付けておく)。
        try:
            conn.rollback()
        except Exception:
            pass
        raise HTTPException(409, f"同名のソースが既に存在します: {name}") from e
    # 例外で接続を開いたまま抜けると暗黙BEGINの書き込みトランザクションが残留し、
    # 以後の全書き込みが busy_timeout(30s) 超過の "database is locked" になるため、
    # finally で必ず close する (close は未コミットの変更をロールバックする)。
    finally:
        conn.close()
    if auto_scan:
        # DD-CYN-0116 Q-1: 走査も publish と同じく列を通す。_start_scan が
        # 「列が有効なら積む／無効ならプロセス内スレッド」を内蔵している。
        # 従来はここが _do_scan 直スレッドだったため、走査だけが列を通らず
        # 落ちても拾い直しが効かなかった (API Pod と運命を共にしていた)。
        _start_scan(sid)
    # C-5: 保存値 (正規化済みパス) をそのまま返す
    return {"id": sid, "name": name, "path": _normalized, "auto_scan": auto_scan}


@router.get("/api/sources/{source_id}/open-in-finder", response_model=None)
def open_source_in_finder(request: Request, source_id: str):
    """Open the source path in OS file manager (macOS Finder / Windows Explorer / Linux xdg-open)."""
    _require_admin(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する
    try:
        source = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    finally:
        conn.close()
    if not source:
        raise HTTPException(404, "Source not found")
    src_path = os.path.abspath(os.path.expanduser(source["path"]))
    if not os.path.exists(src_path):
        raise HTTPException(
            404,
            f"このソースのパスが現在の環境に存在しません。別のマシンで登録されたソースの可能性があります（path={src_path}）。",
        )
    # fix2-D: コンテナ実行時は OS ファイルマネージャーを起動できない (xdg-open 不在・そもそも
    #   コンテナから Mac の Finder は開けない)。subprocess を呼ばず、ホスト側の場所を案内するだけにして
    #   500/未捕捉例外を出さない。standalone (非コンテナ) は従来どおり Finder/Explorer/xdg-open。
    if os.path.exists("/run/.containerenv") or os.path.exists("/.dockerenv"):
        _box = "/app/ingest"
        # pathdisplay-20260706: 固定文言の決め打ちを廃し、管理者が Settings で申告した
        # 「取り込みフォルダの実際の場所」(settings key: ingest.host_path) を参照する。
        # 未申告時はパスを含まない中立文言（申告環境で嘘の案内をしない）。表示専用・保存値不変。
        _host_base = ""
        try:
            _c2 = get_db()
            # connleak-fix-20260709: 例外時も必ず close する (外側 except は握り潰しのため内側で保証)
            try:
                _row = _c2.execute("SELECT value FROM settings WHERE key = ?", ("ingest.host_path",)).fetchone()
            finally:
                _c2.close()
            _host_base = (_row["value"] or "").strip() if _row else ""
        except Exception:
            _host_base = ""
        if src_path == _box or src_path.startswith(_box + os.sep):
            if _host_base:
                _host_hint = _host_base.rstrip("/") + src_path[len(_box):]
                _msg = f"コンテナ実行のため Finder は開けません。実際の場所: {_host_hint}"
            else:
                _msg = "コンテナ実行のため Finder は開けません。取り込みフォルダ（起動時に指定した場所）内の該当フォルダを開いてください。"
        else:
            _msg = f"コンテナ実行のため Finder は開けません。コンテナ内パス: {src_path}"
        return {"ok": True, "path": src_path, "opened_with": "container", "container": True, "message": _msg}
    try:
        if sys.platform == "darwin":
            if os.path.isdir(src_path):
                subprocess.run(["open", src_path], check=False)
            else:
                subprocess.run(["open", "-R", src_path], check=False)
            label = "Finder"
        elif sys.platform.startswith("win"):
            if os.path.isdir(src_path):
                subprocess.run(["explorer", src_path], check=False)
            else:
                subprocess.run(["explorer", "/select,", src_path], check=False)
            label = "エクスプローラー"
        else:
            target = src_path if os.path.isdir(src_path) else os.path.dirname(src_path)
            subprocess.run(["xdg-open", target], check=False)
            label = "ファイルマネージャー"
    except FileNotFoundError as e:
        raise HTTPException(500, f"OSのファイルマネージャーコマンドが見つかりません: {e}")
    except Exception as e:
        raise HTTPException(500, f"フォルダを開けませんでした: {e}")
    return {"ok": True, "path": src_path, "opened_with": label}


@router.post("/api/sources/{source_id}/scan/cancel", response_model=None)
def cancel_scan(request: Request, source_id: str):
    """BLOCK B-6 / C-11: 進行中のスキャンに中止要求を出す。

    走査中でなければ 409 (走っていないものへの中止を ok にしない)。
    中止要求は DB (sources.cancel_requested=1) に立てて worker プロセスの走査へも
    届くようにし、同一プロセス内の即応用にプロセス内フラグも従来どおり立てる。
    """
    import server

    _require_admin(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する
    try:
        src = conn.execute(
            "SELECT id, status, file_count FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if not src:
            raise HTTPException(404, "Source not found")
        if (src["status"] or "") != "scanning":
            raise HTTPException(409, "この source は走査中ではありません (中止する走査がありません)")
        conn.execute("UPDATE sources SET cancel_requested = 1 WHERE id = ?", (source_id,))
        _log_audit(conn, "scan_cancel_requested", source_id)
        conn.commit()
    finally:
        conn.close()
    # 同一プロセスで走っている走査への即応 (従来経路を維持)
    server._scan_cancel_flags[source_id] = True
    return {
        "ok": True,
        "status": "cancel_requested",
        "source_id": source_id,
        # C-11(5): 現在の処理済み件数 (走査中は25ファイル毎の途中 commit で更新される)
        "file_count_so_far": src["file_count"],
    }


@router.post("/api/sources/{source_id}/scan", response_model=None)
def scan_source(request: Request, source_id: str):
    from server import _claim_scan, _do_scan, row_to_dict

    _require_admin(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する
    try:
        source = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    finally:
        conn.close()
    if not source:
        raise HTTPException(404, "Source not found")
    # C-6: 排他クレーム (rowcount による原子的な status='scanning' の取得)。
    # 取れなければ二重走査として開始しない。
    if not _claim_scan(source_id):
        raise HTTPException(409, "この source は走査中です (二重走査は開始しない)")

    _do_scan(source_id, already_claimed=True)

    conn = get_db()
    try:
        source = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    finally:
        conn.close()
    return row_to_dict(source)


@router.post("/api/sources/{source_id}/scan/async", response_model=None)
def scan_source_async(request: Request, source_id: str):
    """DD-CYN-0143 M-3: 走査を「開始」と「進み具合」に分ける口。
    即座に返り、走査は別スレッドで走る。進み具合は GET /api/sources/{id} の
    status (scanning → ready) と file_count で見る。中止は scan/cancel。"""
    import threading

    from server import _claim_scan, _do_scan

    _require_admin(request)
    conn = get_db()
    try:
        source = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    finally:
        conn.close()
    if not source:
        raise HTTPException(404, "Source not found")
    # 早期 409 (read-then-act の事前チェック。残すが正は下のクレーム)
    if (source["status"] or "") == "scanning":
        raise HTTPException(409, "この source は走査中です (二重走査は開始しない)")
    # C-6: 排他の正はここ = rowcount による原子的クレーム (TOCTOU を塞ぐ)
    if not _claim_scan(source_id):
        raise HTTPException(409, "この source は走査中です (二重走査は開始しない)")

    try:
        threading.Thread(
            target=_do_scan, args=(source_id, True), daemon=True, name=f"scan-{source_id}"
        ).start()
    except BaseException:
        # スレッドが起こせなかったらクレームを手放す (status='scanning' の宙吊り防止)
        _c = get_db()
        try:
            _c.execute(
                "UPDATE sources SET status = 'failed' WHERE id = ? AND status = 'scanning'",
                (source_id,),
            )
            _c.commit()
        finally:
            _c.close()
        raise
    return {
        "ok": True,
        "source_id": source_id,
        "status": "scan_started",
        "progress_hint": f"GET /api/sources/{source_id} の status と file_count で進み具合を見る",
    }


@router.get("/api/sources/{source_id}/files", response_model=None)
def list_source_files(request: Request, source_id: str):
    from server import rows_to_list

    _require_admin(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する
    try:
        files = rows_to_list(conn.execute("SELECT * FROM files WHERE source_id = ? ORDER BY name", (source_id,)).fetchall())
    finally:
        conn.close()
    for f in files:
        if isinstance(f.get("categories"), str):
            try:
                f["categories"] = json.loads(f["categories"])
            except Exception:
                f["categories"] = []
        if isinstance(f.get("classification"), str):
            try:
                f["classification"] = json.loads(f["classification"])
            except Exception:
                f["classification"] = None
    return files


@router.delete("/api/sources/{source_id}", response_model=None)
def delete_source(request: Request, source_id: str):
    """source 削除: DB 行とチャンクを削除する。

    ga-close-v3 PartA (2026-07-27): 旧実装はアップロード保管領域
    `store/uploads/{source_id}/` を rmtree していたが、アップロード受け口の撤去に伴い
    アプリ内部に資料の写しが作られなくなったため不要になった。取り込みフォルダ側の
    原本は従来どおり一切触らない。
    """
    from server import _purge_chunks_for_source

    _require_admin(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        # 削除前にパスを取得（アップロード由来か判定するため）
        src_row = conn.execute("SELECT path FROM sources WHERE id = ?", (source_id,)).fetchone()
        src_path = src_row["path"] if src_row else None
        # fix-v3 (A2-F2): BM25 再構築のため、削除前に影響を受ける workspace_id を取得しておく
        # (sources 削除で workspace_sources が FK CASCADE で消えるため事前に控える)。
        _affected_ws = [r["workspace_id"] for r in conn.execute(
            "SELECT workspace_id FROM workspace_sources WHERE source_id = ?", (source_id,)
        ).fetchall()]
        _purge_chunks_for_source(conn, source_id)
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        _log_audit(conn, "source_deleted", source_id)
        conn.commit()
    finally:
        conn.close()
    # fix-v3 (A2-F2): 削除コミット後に影響 WS の BM25 索引を再構築 (delete 経路の stale 索引
    # 経由で削除済みチャンクが RAG 回答に残留する漏洩を防ぐ。delete_collection と同型)。
    for _wsid in _affected_ws:
        try:
            from rag import rebuild_bm25_from_db
            rebuild_bm25_from_db(_wsid)
        except Exception:
            pass
    # ga-close-v3 PartA (2026-07-27): store/uploads/{source_id}/ の rmtree を撤去した。
    # アップロード受け口が無くなり、アプリが自分の中に資料の写しを作ることが無くなったため、
    # 削除連鎖でアプリ内部の写しを消す処理は不要になった。取り込みフォルダ側の原本は
    # 従来どおり一切触らない (ユーザー指定パスの source は元ファイルを保持する)。
    return {"ok": True}


# ============================================================
# DD-CYN-0032 B4: 取り込み元 (根) を画面から足す・見る・外す
# ------------------------------------------------------------
#   受け取り手が端末を叩かずに済むようにするための受け口。決定 3-4 に従い、
#   足すときは「フォルダを辿って選ぶ」形だけを用意し、フルパスの手入力は受け付けない
#   (受け取った値が、直前に返した一覧に在るものと一致しない限り足さない)。
#   同時に複数は選ばせない (1回に1件だけ)。
#
#   管理者だけが使える (_require_admin)。閲覧者には一覧も出さない・受け付けない。
#   足していない場所を断る作りは従来のまま (ここで境界を緩めない)。
#
#   入れ物 (コンテナ) で動く形態では、受け取り手の機械のフォルダを画面から辿れない
#   (本体は入れ物の中にいる)。その形態では「見る・外す」だけを画面から行い、
#   「足す」は入口の1行を画面に出して案内する。can_add_from_screen でそれを伝える。
# ============================================================


def _ingest_roots_file() -> str:
    """控えの場所。書く側 (入口スクリプト) と読む側 (ここ) で同じ場所を指す。"""
    _base = os.environ.get("CYNOVELA_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "store"
    )
    return os.path.join(_base, "ingest-roots.json")


def _in_container() -> bool:
    """入れ物 (コンテナ) の中で動いているか。中なら機械のフォルダを辿れない。"""
    return os.path.isdir("/app/ingest") and os.path.abspath("/app") == os.path.abspath(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


# ── DD-CYN-0116 F-5: 取り込み元の正本を Pod ごとのファイルから共有の DB へ移す ──
# 従来は store/ingest-roots.json だけが正本だった。K8s 形態では API が2レプリカで走り、
# この置き場は各コンテナの書き込み層に落ちる。∴ 片方で足した取り込み元がもう片方に無く、
# 画面を開き直すと一覧が変わっていた (どちらの Pod が答えたかで違う)。Pod を作り直すと
# 両方消える。/app/store/models は読み取り専用でも親の /app/store は書けるため、
# 書き込み自体は成功してしまい、黙って割れていた。
#
# 正本 = settings 表の1行 (key='ingest.roots')。全レプリカが同じ Postgres を見るので
# 割れない。新しい表は作らない (settings は同型の使い方が既に多数ある)。
# ファイル側は入口スクリプト (./launch.sh --add) が本体未起動でも使うため残し、
# 起動時に一度だけ DB へ取り込む (_migrate_ingest_roots_to_db)。
_INGEST_ROOTS_KEY = "ingest.roots"


def _read_ingest_roots_db(conn=None) -> list | None:
    """settings から根の一覧を読む。行が無ければ None (未移行)。"""
    _own = conn is None
    _c = get_db() if _own else conn
    try:
        _row = _c.execute(
            "SELECT value FROM settings WHERE key = ?", (_INGEST_ROOTS_KEY,)
        ).fetchone()
    except Exception:
        return None
    finally:
        if _own:
            _c.close()
    if _row is None or _row["value"] is None:
        return None
    try:
        _r = json.loads(_row["value"]) or []
    except Exception:
        return None
    return [x for x in _r if isinstance(x, dict) and x.get("host_path")]


def _write_ingest_roots_db(conn, roots: list) -> None:
    """settings へ根の一覧を書く。呼び元の接続・取引にそのまま載せる
    (直後の監査書き込みと同じ取引に入れて、片方だけ残る状態を作らない)。"""
    _v = json.dumps(roots, ensure_ascii=False)
    _cur = conn.execute(
        "UPDATE settings SET value = ? WHERE key = ?", (_v, _INGEST_ROOTS_KEY)
    )
    if getattr(_cur, "rowcount", 0) == 0:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)", (_INGEST_ROOTS_KEY, _v)
        )


def _load_ingest_roots() -> list:
    """根の一覧を返す。正本は DB。DB に行が無いときだけファイルへ退く。"""
    _db_roots = _read_ingest_roots_db()
    if _db_roots is not None:
        return _db_roots
    _p = _ingest_roots_file()
    if not os.path.isfile(_p):
        return []
    try:
        with open(_p, encoding="utf-8") as _f:
            _d = json.load(_f)
        _r = _d.get("roots") or []
        return [x for x in _r if isinstance(x, dict) and x.get("host_path")]
    except Exception:
        return []


def migrate_ingest_roots_to_db() -> int:
    """起動時に一度だけ、ファイル側の根を DB へ取り込む。

    既に DB 側に行があれば何もしない (二重取り込みを防ぐ)。
    Returns: 取り込んだ件数 (-1 = 既に移行済みで何もしなかった)
    """
    if _read_ingest_roots_db() is not None:
        return -1
    _p = _ingest_roots_file()
    _roots: list = []
    if os.path.isfile(_p):
        try:
            with open(_p, encoding="utf-8") as _f:
                _d = json.load(_f)
            _roots = [
                x
                for x in (_d.get("roots") or [])
                if isinstance(x, dict) and x.get("host_path")
            ]
        except Exception:
            _roots = []
    _c = get_db()
    try:
        _write_ingest_roots_db(_c, _roots)
        _c.commit()
    finally:
        _c.close()
    return len(_roots)


def _ingest_roots_helper():
    """入口スクリプトと同じ部品を使う (中の名前の付け方・書式を1か所に保つ)。"""
    _repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo not in sys.path:
        sys.path.insert(0, _repo)
    from scripts import ingest_roots as _h  # noqa: PLC0415

    return _h


def _browse_start_dir() -> str:
    """新しい根を選ぶときの出発点。

    A2 の実測に従って決めた: 端末側の `./launch.sh --add` は、いまも機械のどのフォルダでも
    根にできる (osascript のフォルダ選択に範囲の制限が無い)。∴ 画面側の出発点を家 ($HOME)
    にしても、管理者が既に持っている力は広がらない。ここで見せるのはフォルダの名前だけで、
    中の資料は根として足すまで一切読めない (読む側の境界は従来どおり根の集合である)。
    """
    return os.path.realpath(os.path.expanduser("~"))


def _list_subdirs(target: str) -> list:
    _out = []
    try:
        for _n in sorted(os.listdir(target)):
            if _n.startswith("."):
                continue
            _p = os.path.join(target, _n)
            try:
                if os.path.isdir(_p) and not os.path.islink(_p):
                    _out.append({"name": _n, "path": os.path.realpath(_p)})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, "このフォルダは読めません")
    except FileNotFoundError:
        raise HTTPException(404, "このフォルダはありません")
    return _out


@router.get("/api/ingest-roots", response_model=None)
def list_ingest_roots(request: Request):
    """いま足してある取り込み元の一覧 (管理者のみ)。"""
    _require_admin(request)
    _roots = _load_ingest_roots()
    _out = []
    for _r in _roots:
        _hp = _r.get("host_path") or ""
        _out.append(
            {
                "name": _r.get("name"),
                "label": _r.get("label") or _r.get("name"),
                "host_path": _hp,
                "exists": bool(_hp) and os.path.isdir(_hp),
            }
        )
    return {
        "roots": _out,
        # 画面から足せるか。入れ物の中では機械のフォルダを辿れないため足せない。
        "can_add_from_screen": not _in_container(),
        # 足したものがすぐ読めるか。入れ物では起動し直しが要る (束縛は起動時にしか張れない)。
        "restart_required_to_apply": _in_container(),
        # コンテナ / Kubernetes 形態では入口スクリプトは使えない。ノードの取り込みフォルダの下へ置く。
        "add_from_terminal": (
            'mkdir -p "$BERTH_DATA_DIR/ingest/<folder>"' if _in_container() else "./launch.sh --add"
        ),
        "start_dir": _browse_start_dir() if not _in_container() else "",
    }


@router.get("/api/ingest-roots/browse", response_model=None)
def browse_for_ingest_root(request: Request, path: str | None = None):
    """新しい根を選ぶためのフォルダ辿り (管理者のみ・フォルダ名だけを返す)。

    手入力を受け付けないための取り決め: 画面はここが返した path しか
    POST /api/ingest-roots へ送れない。サーバ側でも、受け取った値が実在する
    フォルダであることと、家の下であることを必ず見る。
    """
    _require_admin(request)
    if _in_container():
        raise HTTPException(
            400, "この形態では画面からフォルダを辿れません。ノードの取り込みフォルダ (Pod からは /app/ingest) の下にフォルダを置いてください。"
        )
    _home = _browse_start_dir()
    _target = os.path.realpath(os.path.abspath(os.path.expanduser(path or _home)))
    if _target != _home and not _target.startswith(_home + os.sep):
        raise HTTPException(403, "ここから外は選べません")
    if not os.path.isdir(_target):
        raise HTTPException(404, "このフォルダはありません")
    _parent = None if _target == _home else os.path.dirname(_target)
    return {
        "current_path": _target,
        "parent_path": _parent,
        "home_path": _home,
        "folders": _list_subdirs(_target),
    }


@router.post("/api/ingest-roots", response_model=None)
async def add_ingest_root(request: Request):
    """取り込み元を1件足す (管理者のみ・1回に1件だけ)。"""
    _require_admin(request)
    if _in_container():
        raise HTTPException(
            400, "この形態では画面から足せません。ノードの取り込みフォルダ (Pod からは /app/ingest) の下にフォルダを置いてください。"
        )
    _body = await parse_body_pydantic(request)
    _path = _body.get("path")
    if not _path or not isinstance(_path, str):
        raise HTTPException(400, "path が要ります")
    # 決定 3-4: 同時に複数は選ばせない。配列で来たら断る。
    if isinstance(_body.get("paths"), list):
        raise HTTPException(400, "一度に足せるのは1件だけです")
    _real = os.path.realpath(os.path.abspath(os.path.expanduser(_path)))
    _home = _browse_start_dir()
    if _real != _home and not _real.startswith(_home + os.sep):
        raise HTTPException(403, "ここから外は足せません")
    if not os.path.isdir(_real):
        raise HTTPException(400, "フォルダではありません")
    _h = _ingest_roots_helper()
    _f = _ingest_roots_file()
    os.makedirs(os.path.dirname(_f), exist_ok=True)
    # DD-CYN-0116 F-5: 正本は DB。名前の採番は入口スクリプトと同じ部品を使うため、
    # DB 側の一覧を _h が期待する形へ載せ替えてから採番する。
    _data = _h._load(_f)
    _db_roots = _read_ingest_roots_db()
    if _db_roots is not None:
        _data["roots"] = _db_roots
    for _r in _data["roots"]:
        if _r.get("host_path") == _real:
            return {"ok": True, "name": _r.get("name"), "already": True}
    _name = _h.assign_name(_data, _real)
    _label = os.path.basename(_real.rstrip("/")) or _real
    _data["roots"].append({"name": _name, "host_path": _real, "label": _label})
    _c = get_db()
    try:
        # 正本の更新と監査を同じ取引に入れる (片方だけ残る状態を作らない)
        _write_ingest_roots_db(_c, _data["roots"])
        _log_audit(_c, "ingest_root_add", _name, _label)
        _c.commit()
    finally:
        _c.close()
    # 入口スクリプト (本体未起動でも使う) 用の写しも更新しておく。
    # ここが落ちても正本は DB 側に入っているので、取り込み元は失われない。
    try:
        _h._save(_f, _data)
    except Exception:
        pass
    return {"ok": True, "name": _name, "label": _label, "already": False}


@router.delete("/api/ingest-roots/{name}", response_model=None)
def remove_ingest_root(request: Request, name: str):
    """取り込み元を1件外す (管理者のみ)。原本には触らない。"""
    _require_admin(request)
    _h = _ingest_roots_helper()
    _f = _ingest_roots_file()
    # DD-CYN-0116 F-5: 正本は DB。ファイルは入口スクリプト用の写し。
    _data = _h._load(_f)
    _db_roots = _read_ingest_roots_db()
    if _db_roots is not None:
        _data["roots"] = _db_roots
    _before = len(_data["roots"])
    _data["roots"] = [r for r in _data["roots"] if r.get("name") != name]
    if len(_data["roots"]) == _before:
        raise HTTPException(404, "その取り込み元はありません")
    _c = get_db()
    try:
        _write_ingest_roots_db(_c, _data["roots"])
        _log_audit(_c, "ingest_root_remove", name, "")
        _c.commit()
    finally:
        _c.close()
    try:
        _h._save(_f, _data)
    except Exception:
        pass
    return {"ok": True, "restart_required_to_apply": _in_container()}

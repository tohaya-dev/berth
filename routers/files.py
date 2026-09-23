"""ファイル系エンドポイント。

- /api/documents/{id}/metadata (PATCH): ビジネスメタデータ更新
- /api/browse (GET): フォルダブラウザ
- /api/folder-scan-preview (POST): フォルダ走査プレビュー
- /api/files/{id}/preview (GET): ファイル内容プレビュー (先頭2000文字)

ga-close-v3 PartA (2026-07-27): /api/upload (POST) を撤去した。
受け取ったファイルを store/uploads/ にアプリ内部の生ファイルとして書き出す唯一の経路で、
暗号化も伏字もされず、ワークスペース削除でも消えず、K8s では受け口 Pod と処理 Pod が
別なので構造的に必ず失敗していた。取り込みは取り込みフォルダ経由に一本化する。
"""

from __future__ import annotations

import os
from pathlib import Path

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request

from db import get_db
from core.auth import _require_admin, _require_authenticated
from core.errors import api_error
from core.audit import _log_audit, log_admin_change


router = APIRouter(tags=["files"])


# ------------------------------------------------------------
# DD-CYN-0032 B4: 取り込み元 (根) の控えを毎回読む。
#   書く側は3つ: 入口スクリプト (./launch.sh --add ほか)・本体の起動時・画面 (/api/ingest-roots)。
#   読む側をここ1か所に揃えることで、画面から足したものが起動し直さずに効く。
#   読めない/無いときは None を返し、呼ぶ側が従来の道 (起動時に確定した一覧) に退く。
# ------------------------------------------------------------
def _ingest_roots_now():
    # DD-CYN-0116 F-5: 読み手を2実装のまま残さない。正本 (settings 表) を読む
    # routers/sources.py の口へ寄せる。ファイルへの退避もそちらが持っている。
    # 読めないときは従来どおり None を返し、呼ぶ側が起動時に確定した一覧へ退く。
    try:
        from routers.sources import _load_ingest_roots as _lir  # noqa: PLC0415

        _r = _lir()
        return _r or None
    except Exception:
        return None




@router.patch("/api/documents/{document_id}/metadata", response_model=None)
async def update_document_metadata(document_id: str, request: Request):
    """ビジネスメタデータ + 自動分類を更新.

    Stage R5-fix P1 #9: sensitivity_level / doc_type 変更は admin 限定。
    owner / department / project は認証済みユーザー全員可。
    """
    from core.auth import _require_authenticated

    user = _require_authenticated(request)
    body = await parse_body_pydantic(request)
    allowed = {"owner", "department", "project", "sensitivity_level", "doc_type"}
    updates = {k: v for k, v in (body or {}).items() if k in allowed}
    # Stage R5-fix P1 #9: sensitivity_level / doc_type は admin のみ変更可
    privileged_fields = {"sensitivity_level", "doc_type"}
    if any(k in updates for k in privileged_fields):
        if user.get("role") != "admin":
            raise api_error(
                "PERMISSION_DENIED",
                "sensitivity_level / doc_type の変更は admin のみ可能です",
                status=403,
            )
    if not updates:
        raise api_error("NO_VALID_FIELDS", "No valid fields to update", status=400)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = get_db()
    try:
        before = conn.execute(
            "SELECT owner, department, project, sensitivity_level, doc_type " "FROM files WHERE id = ?",
            (document_id,),
        ).fetchone()
        if not before:
            raise api_error("NOT_FOUND", "document not found", status=404)
        # MED-1 (authz-fix-v1): オブジェクト所属検査。非admin はこの document の source が
        # 自分の所属WSに紐づく場合のみ更新可 (他WSの doc メタデータ改ざんを 403 で閉じる)。
        # admin は広域維持。多段解決 (files→source→workspace_sources→workspace_users) は
        # catalog.py のメンバーシップ・スコープと同型 (1ソース複数WS所属を IN で正しく扱う)。
        if user.get("role") != "admin":
            _member = conn.execute(
                "SELECT 1 FROM files f WHERE f.id = ? AND f.source_id IN "
                "(SELECT source_id FROM workspace_sources WHERE workspace_id IN "
                "(SELECT workspace_id FROM workspace_users WHERE user_id = ?))",
                (document_id, user.get("id")),
            ).fetchone()
            if not _member:
                raise api_error(
                    "PERMISSION_DENIED",
                    "このドキュメントを変更する権限がありません",
                    status=403,
                )
        conn.execute(
            f"UPDATE files SET {set_clause} WHERE id = ?",
            list(updates.values()) + [document_id],
        )
        conn.commit()
    finally:
        conn.close()
    log_admin_change(user["id"], "document", document_id, "update", dict(before), updates)
    return {"id": document_id, "status": "updated", "updates": updates}


@router.get("/api/browse", response_model=None)
def browse_folders(request: Request, path: str = ""):
    """Task 5: フォルダブラウザ。指定パス配下のサブフォルダ一覧を返す。

    クエリ:
      path: 探索対象のフルパス。省略時は $HOME。

    レスポンス:
      {current_path, parent_path | null, home_path, folders: [{name, path, type}]}

    セキュリティ:
      - ホームディレクトリ外へのアクセスは 403
      - フォルダのみ列挙 (ファイル・symlink・隠しフォルダは除外)
      - realpath で symlink 解決後、home + os.sep の前方一致でアクセス制御
    """
    # DD-CYN-0032 B1/B4: 他の2系統と同じ形に揃える (決定 13-1)。
    #   ① 管理者専用にする。従来この系統だけ閲覧者でもホストのフォルダを列挙できた
    #      (他の2系統は以前から _require_admin)。断る側へ揃えるので緩めていない。
    #   ② 境界を「足してある取り込み元の集合」にする。従来はホームまるごとが見えており、
    #      足していない場所を断る作りが無かった。控えは入口 (./launch.sh --add ほか) と
    #      画面 (/api/ingest-roots) が書き、ここが毎回読む。
    _require_admin(request)
    home = os.path.realpath(os.path.expanduser("~"))
    roots = _ingest_roots_now() or []

    # fix-folder-ingest-20260618 の名残: 入れ物で動く形では /app/ingest がそのまま根である。
    # 控えが空のときだけ、従来どおり /app/ingest (無ければホーム) に退く。
    if not roots:
        _ingest_box = "/app/ingest"
        _fallback = os.path.realpath(_ingest_box) if os.path.isdir(_ingest_box) else home
        roots = [{"name": os.path.basename(_fallback) or "root", "label": _fallback, "host_path": _fallback}]

    if not path:
        # 仮想の最上位: 足してある取り込み元を、そのままフォルダとして並べる。
        resp = {
            "current_path": "",
            "parent_path": None,
            "home_path": home,
            "folders": [
                {"name": r.get("label") or r.get("name"), "path": r["host_path"], "type": "directory"}
                for r in roots
            ],
            "files": [],
        }
        if not roots:
            resp["no_roots"] = True
        return resp

    try:
        target = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    except Exception as e:
        raise HTTPException(400, f"Invalid path: {e}")

    # いずれかの取り込み元の中 (その根自身を含む) でなければ断る。
    # q3b-fix-20260922 D7: 根の側も realpath で解決した値で比べる (POST /api/sources と同じ
    # _source_boundary_roots / _is_inside_root)。従来は控えの生の値 (末尾の `/` つき、symlink、
    # `/` そのもの) と realpath 済みの target を比べていたため、正当な根の下でも 403 になった。
    from routers.sources import _is_inside_root, _source_boundary_roots  # noqa: PLC0415

    matched_root = None
    for rp in _source_boundary_roots():
        if _is_inside_root(target, rp):
            matched_root = rp
            if target == rp:
                break
    if matched_root is None:
        raise HTTPException(403, "この場所を使うには取り込み元に足してください")
    if not os.path.isdir(target):
        raise HTTPException(404, f"Folder not found: {target}")

    folders: list[dict] = []
    files: list[dict] = []
    try:
        for entry in sorted(os.listdir(target)):
            if entry.startswith("."):
                continue  # 隠しエントリはスキップ
            full = os.path.join(target, entry)
            try:
                if os.path.islink(full):
                    continue  # symlink は除外 (箱の外へ逃げる経路を断つ・従来挙動踏襲)
                if os.path.isdir(full):
                    folders.append({"name": entry, "path": full, "type": "directory"})
                elif os.path.isfile(full):
                    # fix-folder-ingest-20260618: 目視確認用にファイルも返す (非選択・取り込み単位はフォルダのまま)。
                    files.append({"name": entry, "path": full, "type": "file"})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(403, f"Permission denied: {target}")

    # DD-CYN-0032 B1: 根そのものに居るときの「上へ」は、仮想の最上位 (取り込み元の一覧) へ戻す。
    #   他の2系統と同じ振る舞いにする ("" は「最上位へ戻れる」の意で、null は「戻れない」)。
    parent_path = "" if target == matched_root else os.path.dirname(target)
    return {
        "current_path": target,
        "parent_path": parent_path,
        "home_path": home,
        "folders": folders,
        "files": files,
    }


@router.post("/api/folder-scan-preview", response_model=None)
async def folder_scan_preview(request: Request):
    """PHASE M-3: 指定フォルダを再帰スキャンし、拡張子別件数と推定処理時間を返す。

    Request body: {"folder_path": "/path/to/folder", "recursive": true}
    Response:
      {
        "files": {"pdf": N, "docx": N, "xlsx": N, ..., "images": N,
                  "skipped": {"video": N, "audio": N, "other": N}},
        "estimated_time_sec": int,
        "image_processing_time_sec": int,
        "total_supported": int
      }
    """
    _require_admin(request)
    body = await parse_body_pydantic(request)
    folder_path = (body or {}).get("folder_path") or ""
    recursive = bool((body or {}).get("recursive", True))
    if not folder_path:
        raise HTTPException(400, "folder_path は必須です")

    # /api/sources と同等のパス検証 (Defense-in-Depth)
    # q3b-fix-20260922 D3/D4: 従来は禁止接頭辞と `..` だけを見ており、取り込み元の外の
    # フォルダも (読めるものなら) 辿れた。POST /api/sources と同じ順で、
    #   ① 入口規則 (scheme / `\\` / `..` / 機密パス。core/source_path_policy に寄せた写し)
    #   ② 取り込み元の境界 (realpath して根の配下か。routers/sources.py の同じ関数)
    #   ③ realpath 済みの値に対する機密パス規則 (`//etc` や根の外へ抜ける symlink を拾う)
    # を通し、以後の走査は realpath 済みの値で行う。
    from core.source_path_policy import assert_realpath_not_forbidden, check_source_path_policy  # noqa: PLC0415
    from routers.sources import _assert_source_path_in_ingest_roots  # noqa: PLC0415

    _normalized = check_source_path_policy(folder_path, field="folder_path")
    _target = _assert_source_path_in_ingest_roots(_normalized)
    assert_realpath_not_forbidden(_target)

    folder = Path(_target)
    if not folder.is_dir():
        raise HTTPException(400, f"フォルダが見つかりません: {folder}")

    counts: dict = {
        "pdf": 0,
        "docx": 0,
        "xlsx": 0,
        "pptx": 0,
        "txt": 0,
        "md": 0,
        "csv": 0,
        "html": 0,
        "eml": 0,
        "zip": 0,
        "images": 0,
        "skipped": {"video": 0, "audio": 0, "other": 0},
    }
    image_exts = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif"}
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    audio_exts = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}
    map_ext = {
        ".pdf": "pdf",
        ".docx": "docx",
        ".doc": "docx",
        ".xlsx": "xlsx",
        ".xls": "xlsx",
        ".pptx": "pptx",
        ".txt": "txt",
        ".md": "md",
        ".csv": "csv",
        ".html": "html",
        ".htm": "html",
        ".eml": "eml",
        ".zip": "zip",
    }

    def _bump(ext: str) -> None:
        if ext in image_exts:
            counts["images"] += 1
        elif ext in video_exts:
            counts["skipped"]["video"] += 1
        elif ext in audio_exts:
            counts["skipped"]["audio"] += 1
        elif ext in map_ext:
            counts[map_ext[ext]] += 1
        else:
            counts["skipped"]["other"] += 1

    # os.walk + onerror=lambda で PermissionError を silent skip。
    # 旧実装の Path.rglob は権限エラーで途中停止し HTTP 500 化していた。
    try:
        if recursive:
            for _root, _dirs, filenames in os.walk(str(folder), onerror=lambda _e: None, followlinks=False):
                for fname in filenames:
                    try:
                        ext = os.path.splitext(fname)[1].lower()
                    except Exception:
                        continue
                    _bump(ext)
        else:
            for entry in folder.iterdir():
                try:
                    if not entry.is_file():
                        continue
                    ext = entry.suffix.lower()
                except OSError:
                    continue
                _bump(ext)
    except (PermissionError, OSError) as e:
        raise HTTPException(400, f"フォルダの走査に失敗しました: {e}")

    total_supported = sum(v for k, v in counts.items() if k not in ("skipped", "images")) + counts["images"]
    # 推定: テキスト系 0.3s/件、画像 (caption モード時) 3s/件 → 設定モードで切替
    img_per_file = 3
    try:
        from core.config import CYNOVELA_CONFIG as _CFG

        mode = (_CFG.get("image") or {}).get("processing_mode", "filename_only")
        if mode != "caption":
            img_per_file = 0.05
    except Exception:
        pass
    text_count = total_supported - counts["images"]
    estimated_text = int(text_count * 0.3)
    estimated_image = int(counts["images"] * img_per_file)
    return {
        "files": counts,
        "total_supported": total_supported,
        "estimated_time_sec": estimated_text + estimated_image,
        "image_processing_time_sec": estimated_image,
    }


@router.get("/api/files/{file_id}/preview", response_model=None)
def file_preview(file_id: str, request: Request):
    """P2-2: ファイルプレビュー（先頭2000文字）"""
    # FIX-025: in-line role 検査 → _require_role helper 統一 (FIX-020 後の追加対応)
    from core.auth import _require_role

    # DD-CYN-0021 §2-1: 撤去済み職能名を許可リストから除去。有効ロールは admin / viewer の
    # 2 つだけ (正 = core/constants.py VALID_ROLES) で、viewer は元から不許可のため挙動不変。
    _fp_user = _require_role(request, ("admin",))
    conn = get_db()
    try:
        row = conn.execute("SELECT id, name, path, mime_type FROM files WHERE id = ?", (file_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")
    path = row["path"] or ""
    name = row["name"] or ""
    # sokessan-fix-a8-20260711: 原本ファイルプレビュー(伏字前本文の先頭2000字)閲覧を監査に残す。
    try:
        _fp_ca = get_db()
        try:
            _log_audit(
                _fp_ca,
                "file_preview",
                file_id,
                detail=(name or "")[:120],
                ip_address=(request.client.host if request.client else None),
                user_id=(_fp_user.get("id") if isinstance(_fp_user, dict) else None),
            )
        finally:
            _fp_ca.close()
    except Exception:
        pass
    ext = os.path.splitext(name)[1].lower()
    binary_exts = {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".zip",
        ".tar",
        ".gz",
        ".bin",
    }
    if ext in binary_exts:
        return {"preview": None, "reason": "binary", "name": name}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(2000)
        return {"preview": text, "name": name, "truncated": True}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="ファイルが見つかりません（パス不正）")
    except Exception as e:
        return {"preview": None, "reason": f"read_error: {e}", "name": name}

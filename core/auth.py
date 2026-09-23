"""認証ヘルパー。

server.py から以下を切り出した:
- get_user_from_token: Authorization ヘッダーからユーザーを解決
- _audit_auth_failure: 認証/認可失敗を audit_logs に記録
- _require_admin: 管理者APIで使用（demo bypass 対応）

_state.config 経由で demo bypass を判定する。
mutable session 辞書は state.sessions に集約。
"""

from __future__ import annotations

import json as _json_mod

import jwt as _pyjwt

from fastapi import HTTPException, Request

import config
from db import get_db

import state as _state
from core.audit import _log_audit, set_audit_actor


def _remember_audit_actor(request: Request, user: dict | None) -> None:
    """ga-close-v3 PartB (B-1): 認証が済んだ時点で「誰が・どこから」を実行文脈に置く。

    以降 `_log_audit()` が、呼出側の明示指定が無い欄をこれで埋める。
    権限判定・監査の判定ロジックには一切関与しない (失敗してもサイレント継続)。
    """
    try:
        set_audit_actor(
            user_id=(user or {}).get("id") or (user or {}).get("user_id"),
            ip_address=(request.client.host if getattr(request, "client", None) else None),
        )
    except Exception:
        pass


# 認証不要 EP の正本集合。`server.py` の custom_openapi() で
# 「この path には 401/403 を documented に注入しない」判定に使う。
# `tests/test_schemathesis.py` も同集合を import して PUBLIC_PATHS として参照する（重複定義回避）。
# 追加・削除は本ファイルで一元管理する。
PUBLIC_PATHS: frozenset[str] = frozenset({
    # health (5)
    "/api/health",
    "/api/health/db",
    "/api/health/vector",
    "/api/health/guardrails",
    "/api/health/detailed",
    # auth: login / logout は schemathesis 上 unauthenticated 扱い
    "/api/auth/login",
    "/api/auth/logout",
    # demo / mode
    "/api/demo/role-switch",
    "/api/mode",
    # 一部 GET 系（POST/PUT は admin だが GET は public）
    "/api/sessions",
    "/api/pii-detections",
    "/api/guardrails/blocked-topics",
    "/api/cost/estimate",
    "/api/llm/list-models",
    "/api/settings/classifier",
    "/api/settings/pii-mode",
    "/api/settings/reranker/test",
    # 静的・ドキュメント
    "/",
    "/chat-popup",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
})


def get_user_from_token(request: Request) -> dict | None:
    """Extract user from Authorization header.
    Supported token format:
    - 'Bearer {hex32}': state.sessions から引く

    fix-security-batch-v2 (2026-05-28) Sub-2G-1 (CRIT-3): 削除済み (is_active=0) ユーザーの
    トークン継続利用を防ぐため、DB 取得後に is_active チェックを追加。
    is_active=0 のときは None を返し、呼出側 (_require_admin/_require_authenticated) で 401 が発火する。

    DD-CYN-0116 X-6 U-1 (姉妹系統の C-B5 の移植): 固定トークン受理経路
    'Bearer demo-token-{user_id}' を封鎖した。従来は --demo 起動時に限り、後続文字列を
    users.id として引き当てて認証を通していた。--demo は本製品の既定の見せ方であり、
    実測で demo-token-user-admin だけで管理者 API に到達できた
    (GET /api/settings/llm・GET /api/admin/users とも 200)。合言葉を知らない相手に
    管理者権限が渡るため、--demo 起動であっても受理しない。
    正規の認証経路は /api/auth/login が発行する JWT のみ。
    本製品の文書 (docs/architecture.md・security-policy.md・operations.md・concept.md・
    known-limitations.md) は以前から「廃止済み」と書いており、コードだけが取り残されていた。
    """
    auth = request.headers.get("Authorization", "")
    # U-1: demo-token-* は起動形態によらず一律拒否（--demo でも通さない）
    if auth.startswith("Bearer demo-token-"):
        return None
    if auth.startswith("Bearer "):
        token = auth[7:]
        sess = _state.sessions.get(token)
        if sess:
            conn = get_db()
            try:
                user = conn.execute(
                    "SELECT * FROM users WHERE id = ? AND COALESCE(is_active, 1) = 1",
                    (sess["user_id"],),
                ).fetchone()
            finally:
                conn.close()
            if user:
                _remember_audit_actor(request, dict(user))
                return dict(user)
            # 削除済みユーザーのトークンは state.sessions からも除去 (housekeeping)
            _state.sessions.pop(token, None)
    return None


def _get_jwt_secret() -> str:
    """JWT 署名シークレット。金庫（Fernet）の鍵とは別実体の署名専用鍵を参照する。
    事実19追補(K8s幹 <development commit> 相当): 公知フォールバック文字列を撤去し fail-closed 化。
    鍵不在時は config 側が暗号乱数で生成・永続化するため「鍵なし署名」は発生しない。
    auth.py 側に新規 env 読みは追加しない（config の既存解決結果を消費するのみ）。

    part6-20260726（二役分離）: 参照先を config._KEY（金庫鍵）から
    config._JWT_SIGNING_KEY（署名専用鍵）へ移した。実体の解決は config 側に閉じており、
    berth では env CYNOVELA_SECRET_KEY 供給時は HMAC 導出（複数レプリカで一致）、
    未供給時は <CYNOVELA_DATA_DIR>/db/jwt/secret.key のファイル方式になる
    （_load_or_create_jwt_signing_key の docstring 参照）。
    2026-07-05 に一鍵二役へ寄せた際の残件の実施であり、公知フォールバックは復活させない
    （署名鍵が無いときは config 側が暗号乱数で生成・chmod 600 する。金庫鍵と同じ作り）。
    署名鍵を替えても金庫の中身（暗号文）とパスワード再発行の経路には影響しない。
    """
    key = config._JWT_SIGNING_KEY
    return key.decode() if isinstance(key, bytes) else key


def jwt_ttl_hours() -> float:
    """N-1: JWT アクセストークンの有効期間 (時間) を返す。8時間固定の直書きを廃止した。

    優先順位: DB settings 表の 'auth.token_ttl_hours' → 環境変数 CYNOVELA_TOKEN_TTL_HOURS
    → 既定 8 (既定値は変えない・決定§77)。値は float 可で、0.01〜720 時間にクランプする。
    settings の読み方は routers/settings.py と同じ SELECT だが、core→routers の import は
    しない (向きの規律) ため DB 直読みをここに閉じる。DB 不達や不正値は次の候補へ落ちる。
    """
    import os as _os

    _candidates: list = []
    try:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", ("auth.token_ttl_hours",)
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            _candidates.append(row["value"])
    except Exception:
        pass
    _candidates.append(_os.environ.get("CYNOVELA_TOKEN_TTL_HOURS"))
    hours = 8.0
    for _c in _candidates:
        if _c in (None, ""):
            continue
        try:
            hours = float(_c)
            break
        except (TypeError, ValueError):
            continue
    # クランプ: 36秒 (0.01h) 〜 30日 (720h)
    return max(0.01, min(hours, 720.0))


def _audit_auth_failure(request: Request, reason: str) -> None:
    """認証/認可失敗を audit_logs に記録する。失敗自体はサイレントに継続。"""
    try:
        ip = request.client.host if request.client else None
        path = str(getattr(request, "url", "")) if request else ""
        conn = get_db()
        try:
            _log_audit(
                conn,
                "auth_failed",
                target=path,
                # berth-ga-final-cleanup (q5 B7): reason は URL のパス引数を含み得るため、JSON を文字列連結で作らない
                detail=_json_mod.dumps({"reason": reason}, ensure_ascii=False),
                ip_address=ip,
                result="failure",
                category="security",
            )
        finally:
            conn.close()
    except Exception:
        pass


def hash_api_key(plaintext: str) -> str:
    """DD-CYN-0132 4-2: 鍵の平文を sha256 でハッシュ化する。平文は保存しない。"""
    import hashlib

    return hashlib.sha256((plaintext or "").encode("utf-8")).hexdigest()


def _resolve_api_key(request: Request, token: str) -> dict | None:
    """DD-CYN-0132 4-2: cyn_ 鍵を検証し、紐づく利用者 dict を返す (無効なら None)。

    - key_hash で引き当て、revoked_at が非 NULL なら拒否。
    - 紐づく user が is_active でなければ拒否。
    - 返す利用者の role は鍵の role (発行時に発行者役割を上限にクランプ済み)。
    - request.state に api_key_id / api_key_scope / api_key_role を載せ、
      capabilities 口や各経路の範囲絞りが参照できるようにする。
    """
    import json as _json

    key_hash = hash_api_key(token)
    conn = get_db()
    try:
        krow = conn.execute(
            "SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
            (key_hash,),
        ).fetchone()
        if not krow:
            return None
        krow = dict(krow)
        urow = conn.execute(
            "SELECT * FROM users WHERE id = ? AND COALESCE(is_active, 1) = 1",
            (krow["user_id"],),
        ).fetchone()
        if not urow:
            return None
        # last_used_at を更新 (監査用・失敗しても無視)
        try:
            from db import _now_iso  # type: ignore
        except Exception:
            _now_iso = None
        try:
            conn.execute(
                "UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?",
                (krow["id"],),
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
    user = dict(urow)
    # 鍵の役割で上書き (発行時に発行者役割を上限にクランプ済み。閲覧者鍵は viewer 固定)
    # agentK K-3 (q5 B3): 鍵の役割は発行時点で固定されるため、利用者を降格
    # (PATCH /api/admin/users/{id} role=viewer) しても admin 鍵は admin のまま使えた。
    # 鍵の役割と利用者の「現在の」役割の低い方を実効役割にする (鍵は名乗る利用者を超えない)。
    # 失効ではなく要求時の再解決にしたのは、再昇格で鍵が自然に復活し、鍵の行 (監査) を
    # 触らずに済むため。is_active=0 は上の SELECT で既に拒否している。
    _key_role = krow.get("role")
    if _key_role:
        _rank = {"viewer": 1, "admin": 2}
        _user_role = user.get("role") or "viewer"
        user["role"] = (
            _key_role if _rank.get(_key_role, 0) <= _rank.get(_user_role, 0) else _user_role
        )
    user["user_id"] = user["id"]
    try:
        _scope = _json.loads(krow.get("scope_json") or "{}")
        if not isinstance(_scope, dict):
            _scope = {}
    except Exception:
        _scope = {}
    try:
        request.state.api_key_id = krow["id"]
        request.state.api_key_scope = _scope
        request.state.api_key_role = user["role"]  # K-3: 実効役割 (鍵 × 利用者の現在役割)
    except Exception:
        pass
    _remember_audit_actor(request, user)
    return user


def enforce_api_key_scope(request: Request, workspace_id: str | None = None,
                          collection_ids: list | None = None) -> list:
    """DD-CYN-0136 2-6: 用途限定の鍵 (cyn_) の scope を API 層でも強制する。

    従来この強制は MCP 層 (_scope_check) にしか無く、鍵で直に /api/chat 等を叩くと
    scope 外の workspace/collection に到達できた (経路間の乖離。総決算で実測)。
    JWT (鍵でない) は従来どおり各 API の権限判定に委ね、ここでは絞らない。

    agentK K-2 (q5 B2): 返り値として「実効コレクション一覧」を返す。
    従来は名指しされた collection_ids が範囲外かだけを見ており、名指しが空だと
    呼出側 (chat._narrow_to_requested_collections / chunks) が「WS 内全件」に広げていた
    (= scope.collections が空指定で無効化される)。scope.collections が有るのに名指しが
    空なら scope の集合そのものを返し、呼出側はこれを検索対象にする。
    鍵でない・scope なし・collections 軸なしのときは受け取った collection_ids をそのまま返す
    (従来挙動そのまま)。
    """
    _requested = [c for c in (collection_ids or []) if c]
    scope = getattr(request.state, "api_key_scope", None)
    if not isinstance(scope, dict) or not scope:
        return _requested
    ws_allow = scope.get("workspaces") or []
    col_allow = scope.get("collections") or []
    if ws_allow and workspace_id and workspace_id not in ws_allow:
        raise HTTPException(403, f"この鍵は作業場所 {workspace_id} の範囲外です")
    if col_allow:
        for _c in _requested:
            if _c not in col_allow:
                raise HTTPException(403, f"この鍵はコレクション {_c} の範囲外です")
        if not _requested:
            return [str(c) for c in col_allow if c]
    return _requested


# agentK K-5 (q5 B5): 初回パスワード変更ゲートを通さずに到達できる EP の正本集合。
# 初回パスワード変更の GUI 流れ (frontend/js/state.js _enterApp → /api/auth/me →
# _showMustChangePasswordModal → /api/auth/change-password) と、ログアウト・
# ロック画面解除 (verify-password) に必要な最小集合。変更以外の操作は通さない。
MUST_CHANGE_EXEMPT_PATHS: frozenset[str] = frozenset({
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/change-password",
    "/api/auth/verify-password",
})


def _enforce_must_change_gate(request: Request, user: dict) -> None:
    """agentK K-5 (q5 B5): must_change_password=1 の利用者は、免除 EP 以外を 403 で止める。

    従来このゲートは _require_admin にしか無く、_require_role(request, {"admin"}) を使う
    reports 系 3 EP と /api/files/{id}/preview は配布物の固定初期 PW のまま到達できた。
    認証の共通経路 (_require_authenticated) に置くことで、鍵 (cyn_)・JWT・旧 hex32 の
    全経路と、_require_admin / _require_role / _require_admin_or_self / 直呼びの全呼出側に
    一律で効く。must_change=0 の稼働/テストは無影響。
    """
    if not user.get("must_change_password"):
        return
    try:
        path = request.scope.get("path") or request.url.path
    except Exception:
        from urllib.parse import urlsplit as _urlsplit

        path = _urlsplit(str(getattr(request, "url", "") or "")).path
    if path in MUST_CHANGE_EXEMPT_PATHS:
        return
    _audit_auth_failure(request, "must_change_password")
    raise HTTPException(403, "初回パスワードの変更が必要です。パスワードを変更してから操作してください。")


def _require_admin(request: Request) -> dict:
    """管理者APIで使用。Batch-B S1-3: JWT 対応のため _require_authenticated 経由に統一。"""
    user = _require_authenticated(request)
    if user.get("role") != "admin":
        _audit_auth_failure(request, "admin_required")
        raise HTTPException(403, "管理者権限が必要です")
    # must-change-gate (pre-ga-fix-all-20260720): 初回パスワード変更が必要な管理者は、変更を済ませるまで
    # 管理操作を通さない (配布物の固定初期PW対策・「変更以外の操作を通さない」)。
    # agentK K-5: ゲート本体は _require_authenticated 内の _enforce_must_change_gate へ移し
    # (免除 EP は MUST_CHANGE_EXEMPT_PATHS)、ここは二重の守りとして残す。
    if user.get("must_change_password"):
        raise HTTPException(403, "初回パスワードの変更が必要です。パスワードを変更してから操作してください。")
    return user


def _require_authenticated(request: Request) -> dict:
    """Batch-B S1-3: JWT トークンを検証する。

    返却 dict は user 行全体に加え `user_id` キーを含める（既存呼び出し元との互換性維持）。

    DD-CYN-0116 X-6 U-1 (姉妹系統の C-B5 の移植): demo-token-* の後方互換受理を撤去した。
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        _audit_auth_failure(request, "unauthenticated")
        raise HTTPException(401, "認証が必要です")
    token = auth[7:]

    # U-1: demo-token-* は起動形態によらず 401（固定トークンでの到達を封鎖）
    if token.startswith("demo-token-"):
        _audit_auth_failure(request, "demo_token_rejected")
        raise HTTPException(401, "認証が必要です")

    # DD-CYN-0132 4-2: 用途を限定した鍵 (api_keys)。平文は cyn_<...> 形式。
    #   sha256 で引き当て、失効していなければ紐づく利用者を返す。鍵の役割/範囲は
    #   request.state に載せ、capabilities 口や各経路の範囲絞りが参照できるようにする。
    if token.startswith("cyn_"):
        _u = _resolve_api_key(request, token)
        if _u is None:
            _audit_auth_failure(request, "api_key_rejected")
            raise HTTPException(401, "認証が必要です")
        _enforce_must_change_gate(request, _u)  # K-5
        return _u

    # JWT 形式（`xxx.yyy.zzz`）の検証
    if token.count(".") == 2:
        try:
            payload = _pyjwt.decode(
                token, _get_jwt_secret(), algorithms=["HS256"]
            )
        except _pyjwt.ExpiredSignatureError:
            _audit_auth_failure(request, "token_expired")
            raise HTTPException(401, "Token expired")
        except _pyjwt.InvalidTokenError:
            _audit_auth_failure(request, "invalid_token")
            raise HTTPException(401, "Invalid token")
        from db import get_db as _get_db
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ? AND COALESCE(is_active, 1) = 1",
                (payload["sub"],),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            _audit_auth_failure(request, "user_deactivated")
            raise HTTPException(401, "User deactivated")
        result = dict(row)
        # agentK K-1 (q5 B1): パスワード変更 (本人 change-password / 管理者 reset-password) より
        # 前に発行されたアクセストークンを拒否する。users.password_changed_at (UTC epoch 秒) と
        # JWT の iat (同じ UTC epoch 秒・routers/auth.py login/refresh が付与) を比べる。
        # 列が無い旧 DB / 未変更 (NULL) は従来どおり通す。iat 無しの JWT は本製品が発行しない
        # 形なので拒否する (fail-closed)。同一秒内 (iat == changed_at) は変更直後に発行する
        # 新トークンを通すため許容する (漏れ窓は最大 1 秒)。
        _pca = result.get("password_changed_at")
        if _pca not in (None, "", 0):
            try:
                _pca_i = int(_pca)
            except (TypeError, ValueError):
                _pca_i = None
            if _pca_i is not None:
                _iat = payload.get("iat")
                try:
                    _iat_i = int(_iat) if _iat is not None else None
                except (TypeError, ValueError):
                    _iat_i = None
                if _iat_i is None or _iat_i < _pca_i:
                    _audit_auth_failure(request, "token_issued_before_password_change")
                    raise HTTPException(401, "Token expired")
        result["user_id"] = row["id"]
        _remember_audit_actor(request, result)
        _enforce_must_change_gate(request, result)  # K-5
        return result

    # 旧 hex32 セッショントークン: 既存の state.sessions 経由
    user = get_user_from_token(request)
    if not user:
        _audit_auth_failure(request, "unauthenticated")
        raise HTTPException(401, "認証が必要です")
    result = dict(user)
    result["user_id"] = user.get("id")
    _remember_audit_actor(request, result)
    _enforce_must_change_gate(request, result)  # K-5
    return result


def _require_role(request: Request, allowed_roles) -> dict:
    """allowed_roles のいずれかに合致するロールを要求する。

    Stage R5-3 で routers/ の inline `role != "admin"` パターンを統一する用途。
    allowed_roles は set / list / tuple のいずれでも可。
    """
    # fix-bug1: _require_authenticated 経由に統一し JWT トークンを受理する。
    # 従来は get_user_from_token のみで JWT 非対応 → reports 系 EP 等が JWT で 401 になっていた。
    # (_require_admin と同じ「Batch-B S1-3: JWT 対応」方針に揃える)
    user = _require_authenticated(request)
    allowed = set(allowed_roles)
    role = user.get("role")
    if role not in allowed:
        _audit_auth_failure(request, f"role_not_allowed:{role}")
        raise HTTPException(403, "権限がありません")
    return user


def _require_admin_or_self(request: Request, target_user_id: str) -> dict:
    """admin もしくは target_user_id 本人のみを許可する。

    FIX-030 で `routers/users.py` 等の「admin OR self」in-line パターンを統一。
    role 変更等の特権操作判定は呼出側で `user.get("role") == "admin"` で別途実施。
    """
    # fix-kenobi: _require_authenticated 経由に統一し JWT トークンを受理する。
    # 従来は get_user_from_token のみで JWT 非対応 → PATCH /api/users/{id} が
    # JWT の admin/self でも常に 401 になっていた (_require_role の fix-bug1 /
    # _require_admin の Batch-B S1-3 と同じ JWT 対応がここだけ漏れていた)。
    user = _require_authenticated(request)
    is_admin = user.get("role") == "admin"
    is_self = user.get("id") == target_user_id
    if not is_admin and not is_self:
        _audit_auth_failure(request, f"admin_or_self_required:target={target_user_id}")
        raise HTTPException(403, "他のユーザーの情報を変更できません")
    return user


# ── オブジェクト/テナント単位の認可ヘルパー (authz-fix-v1) ───────────────────
# ロール境界 (anon/viewer/admin) の上に、オブジェクト所有権・WS所属を足す共通窓口。
# 既存の散在実装 (workspaces.py:649-661 get_workspace_chunks / list_workspaces /
# sessions.py のメンバーシップ・所有権検査) を 1 箇所に集約し、各アクセス点へ
# 機械的に 1 行差し込む。admin は従来どおり広域アクセスを保持する (検査スキップ)。
def require_ws_membership(user: dict, workspace_id: str, conn) -> None:
    """要求者が当該 workspace のメンバーか admin であることを検証する。

    admin は広域アクセスを保持 (検査スキップ)。非 admin が workspace_users に
    未所属なら 403。conn は呼出側が管理する (本関数は close しない)。
    """
    if (user or {}).get("role") == "admin":
        return
    member = conn.execute(
        "SELECT 1 FROM workspace_users WHERE workspace_id = ? AND user_id = ?",
        (workspace_id, (user or {}).get("id")),
    ).fetchone()
    if not member:
        raise HTTPException(403, "このワークスペースへのアクセス権がありません")


def require_session_owner(user: dict, session_id: str, conn) -> None:
    """要求者が当該 session の所有者か admin であることを検証する。

    admin は広域アクセスを保持。非 admin が他人の session_id を指定したら 403。
    存在しない session は「これから自分用に作られる」ため許容 (チェックを通す)。
    予測可能な暗黙 session_id (ws_{wsid}_{uid}) を悪用した cross-user 履歴
    read+write を閉じる。session_id 生成方式自体は変更しない (所有権検査のみ)。
    """
    if not session_id or (user or {}).get("role") == "admin":
        return
    row = conn.execute(
        "SELECT user_id FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is not None and row["user_id"] != (user or {}).get("id"):
        raise HTTPException(403, "このセッションへのアクセス権がありません")

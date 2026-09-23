"""監査ログヘルパー (`_log_audit` / `log_admin_change` / `_AUDIT_CATEGORY_MAP`)。

server.py から切り出した汎用ヘルパー。複数ルーターから利用される。
EventBus が初期化済みであれば emit、未初期化なら直接 audit_logs テーブルへ INSERT する。
"""

from __future__ import annotations

import contextvars
import json

from db import get_db, new_id


# ga-close-v3 PartB (B-1): 監査行の「誰が (利用者)」「どこから (接続元IP)」を埋めるための実行文脈。
# 認証が済んだ時点で core/auth.py が set_audit_actor() で置き、_log_audit() は
# 「呼出側が明示しなかった欄の既定値」としてのみ参照する。
# 監査の判定 (category 決定・記録するか否か) には一切関与しない。記録する項目を埋めるだけ。
_audit_actor_var: contextvars.ContextVar[dict] = contextvars.ContextVar("audit_actor", default={})


def set_audit_actor(user_id: str | None = None, ip_address: str | None = None) -> None:
    """実行中のリクエストの利用者 / 接続元を記録する。空値は上書きしない。

    ContextVar なのでリクエスト毎に独立 (asyncio Task / threadpool のいずれも
    呼出時点のコンテキストを複製するため、他リクエストへ漏れない)。
    """
    try:
        current = dict(_audit_actor_var.get() or {})
        if user_id:
            current["user_id"] = str(user_id)
        if ip_address:
            current["ip_address"] = str(ip_address)
        _audit_actor_var.set(current)
    except Exception:
        pass


def get_audit_actor() -> dict:
    """現在の実行文脈の {user_id, ip_address}。未設定なら空 dict。"""
    try:
        return dict(_audit_actor_var.get() or {})
    except Exception:
        return {}


# 監査ログのカテゴリマッピング
_AUDIT_CATEGORY_MAP: dict[str, str] = {
    "chat_query": "chat",
    "chat_query_general": "chat",
    "chat_retrieved": "chat",  # §段2 masking-rework: retrieve 後の tier/doc_ids 記録
    "LOW_CONFIDENCE_FALLBACK": "chat",
    "COMPARE_QUERY": "chat",
    "source_created": "source",
    "source_deleted": "source",
    "auto_scan_complete": "sync",
    "auto_scan_error": "sync",
    "ws_sync_config_updated": "sync",
    "workspace_created": "workspace",
    "workspace_updated": "workspace",
    "workspace_deleted": "workspace",
    "workspace_archived": "workspace",
    "workspace_unarchived": "workspace",
    "collection_published": "publish",
    "publish_started": "publish",
    "publish_complete": "publish",
    "collection_archived": "publish",
    "collection_unarchived": "publish",
    "user_created": "user",
    "user_updated": "user",
    "user_deactivated": "user",
    "user_password_reset": "user",
    "backup_created": "backup",
    "backup_restored": "backup",
    "backup_deleted": "backup",
    "session_created": "session",
    "feedback_saved": "feedback",
    "PROMPT_INJECTION_BLOCKED": "security",
    "pii_detected": "security",
    "auth_failed": "security",
    "features_updated": "workspace",
}


def _audit_category(action: str) -> str:
    return _AUDIT_CATEGORY_MAP.get(action, "other")


def _log_audit(
    conn,
    action: str,
    target: str = "",
    detail: str = "",
    *,
    ip_address: str | None = None,
    result: str = "success",
    category: str | None = None,
    user_id: str | None = None,
    tier: str | None = None,
    document_ids: list[str] | None = None,
    endpoint: str | None = None,
) -> None:
    """P3-1: 監査ログを記録する。EventBus 経由で AuditLogListener に通知する。
    既存呼び出し箇所はシグネチャを変えない (互換性維持)。
    キーワード引数 ip_address/result/category は audit_logs 拡張カラム用。

    §段2: 追加 keyword 引数 (masking-rework-overnight-v5):
      user_id: 認証済みユーザの id。audit_logs.user_id 列に格納される。
      tier: 'raw' / 'masked'。どの保管庫を引いたかの記録。detail JSON 内に追記。
      document_ids: 引いた chunk/doc id のリスト。detail JSON 内に追記。
    既存呼出は全て無指定で動くため後方互換。

    引数の `conn` は後方互換のため受け取るが、EventBus リスナー側で独自に
    DB 接続を取得するため未使用。失敗時もサイレントに継続する。"""
    if category is None:
        category = _audit_category(action)
    # ga-close-v3 PartB (B-1): 呼出側が渡さなかった欄「だけ」を実行文脈で補う。
    # 明示指定 (既存 8 箇所) は常に優先されるので既存挙動は変わらない。
    if user_id is None or ip_address is None:
        _actor = get_audit_actor()
        if user_id is None:
            user_id = _actor.get("user_id")
        if ip_address is None:
            ip_address = _actor.get("ip_address")
    # §段2: detail に tier / document_ids を埋め込む (audit_logs に専用列を作らず JSON で表現)
    # DD-CYN-0132 2-4: 外向き送信の宛先 (endpoint) も同じ方式で detail JSON に残す。
    if tier is not None or document_ids is not None or endpoint is not None:
        try:
            _existing = json.loads(detail) if detail else {}
            if not isinstance(_existing, dict):
                _existing = {"detail": detail}
        except Exception:
            _existing = {"detail": detail}
        if tier is not None:
            _existing["tier"] = tier
        if document_ids is not None:
            _existing["document_ids"] = list(document_ids)[:50]  # 上限 50 件で長大化抑止
        if endpoint is not None:
            _existing["endpoint"] = str(endpoint)
        detail = json.dumps(_existing, ensure_ascii=False)
    try:
        from services.event_bus import event_bus as _eb

        _eb.emit(
            action,
            {
                "target": target,
                "detail": detail,
                "ip_address": ip_address,
                "result": result,
                "category": category,
                "user_id": user_id,
            },
        )
    except Exception:
        # フォールバック: EventBus 未初期化の場合は直接書き込み
        try:
            conn.execute(
                "INSERT INTO audit_logs "
                "(id, action, target, detail, ip_address, result, category, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), action, target, detail, ip_address, result, category, user_id),
            )
            conn.commit()
        except Exception:
            pass


def log_admin_change(
    changed_by: str,
    entity_type: str,
    entity_id: str | None,
    action: str,
    before_value: dict | None = None,
    after_value: dict | None = None,
) -> None:
    """管理操作変更を記録する. 失敗時はサイレントに無視.

    entity_type: 'policy' / 'user' / 'setting' / 'guardrail' / 'blocked_topic' /
                 'document' など
    action:      'create' / 'update' / 'delete'
    """
    try:
        conn = get_db()
        try:
            conn.execute(
                """INSERT INTO admin_change_log
                   (id, changed_by, entity_type, entity_id, action,
                    before_value, after_value)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id(),
                    changed_by or "unknown",
                    entity_type,
                    entity_id,
                    action,
                    json.dumps(before_value, ensure_ascii=False) if before_value else None,
                    json.dumps(after_value, ensure_ascii=False) if after_value else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass

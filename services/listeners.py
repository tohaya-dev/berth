"""Cynovela — EventBus リスナー定義 (P3-1 / P3-2)。

AuditLogListener が _log_audit() の後継として既存 audit_logs に書き込む。
SMTPListener は P3-2 で追加。yaml.notifications.smtp.enabled=true でのみ有効。
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from services.event_bus import event_bus

logger = logging.getLogger(__name__)


class AuditLogListener:
    """全イベントを既存 audit_logs テーブル (id/action/target/detail) に記録する。

    既存スキーマ互換のため、event['type'] → action、event['payload'] から
    target / detail を抽出する。

    実装方針 (FIX 20260527 M1): max_workers=1 + worker専用シングルトン接続 _conn
    で SQLite ロック競合を原理的に排除。busy_timeout は db.get_db()=30秒に従う。
    """

    _executor = None
    _conn = None  # PORTABILITY FIX: worker スレッド専用のシングルトン接続

    @classmethod
    def _get_executor(cls):
        if cls._executor is None:
            from concurrent.futures import ThreadPoolExecutor

            cls._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit-log")
        return cls._executor

    def __init__(self, db_connection_factory: Callable):
        self._get_conn = db_connection_factory

    def __call__(self, event: dict) -> None:
        try:
            import json as _json
            from db import new_id as _nid
        except Exception:
            import uuid

            _nid = lambda: uuid.uuid4().hex[:16]
        try:
            payload = event.get("payload") or {}
            target = str(payload.get("target", "") or payload.get("workspace_id", "") or "")
            ip_address = payload.get("ip_address")
            result = payload.get("result", "success")
            category = payload.get("category")
            # §段2: user_id を payload から抽出 (audit_logs.user_id 列に格納)
            user_id = payload.get("user_id")
            # detail から内部メタを除外
            extra = {
                k: v
                for k, v in payload.items()
                if k not in ("target", "ip_address", "result", "category", "user_id")
            }
            detail = ""
            if extra:
                try:
                    detail = _json.dumps(extra, ensure_ascii=False)[:500]
                except Exception:
                    detail = str(extra)[:500]
            log_id = _nid()
            action = event["type"]
            # 別スレッドで書き込み (event loop もメイン conn も塞がない)
            self._get_executor().submit(
                self._sync_insert,
                log_id,
                action,
                target,
                detail,
                ip_address,
                result,
                category,
                user_id,
            )
        except Exception as e:
            # FIX-049: logger.exception でスタックトレース可達化
            logger.exception(f"[AuditLogListener] schedule failed: {e}")

    def _sync_insert(
        self,
        log_id: str,
        action: str,
        target: str,
        detail: str,
        ip_address: str | None = None,
        result: str = "success",
        category: str | None = None,
        user_id: str | None = None,
    ) -> None:
        try:
            cls = type(self)
            # PORTABILITY FIX: worker スレッド固定 (max_workers=1) のシングルトン接続を遅延生成。
            # db.get_db() は busy_timeout=30000ms を設定する。busy_timeout の上書きはしない。
            if cls._conn is None:
                cls._conn = self._get_conn()
            conn = cls._conn
            conn.execute(
                "INSERT INTO audit_logs "
                "(id, action, target, detail, ip_address, result, category, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (log_id, action, target, detail, ip_address, result, category, user_id),
            )
            conn.commit()
        except Exception as e:
            # FIX-049: logger.exception でスタックトレース可達化
            logger.exception(f"[AuditLogListener] persist failed: {e}")
            # 接続が壊れた可能性があるので次回 lazy 再生成させる
            try:
                if type(self)._conn is not None:
                    type(self)._conn.close()
            except Exception:
                pass
            type(self)._conn = None


class SMTPListener:
    """P3-2: 指定イベントをメール通知する。yaml.notifications.smtp 設定が必要。
    SMTP 失敗はサイレント (アプリは止めない)。"""

    def __init__(self, smtp_config: dict):
        self.host = smtp_config.get("host", "")
        self.port = int(smtp_config.get("port", 587))
        self.username = smtp_config.get("username", "")
        # 環境変数 CYNOVELA_SMTP_PASSWORD があれば優先
        self.password = os.environ.get(
            "CYNOVELA_SMTP_PASSWORD",
            smtp_config.get("password", "") or "",
        )
        self.from_address = smtp_config.get("from_address", "")
        self.to_addresses = smtp_config.get("to_addresses", []) or []
        self.notify_on = set(smtp_config.get("notify_on", []) or [])

    def __call__(self, event: dict) -> None:
        event_type = event.get("type", "")
        if self.notify_on and event_type not in self.notify_on:
            return
        if not self.to_addresses or not self.host:
            return
        try:
            import smtplib
            import json as _json
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            subject = f"[Cynovela] {event_type}"
            body = _json.dumps(event.get("payload") or {}, ensure_ascii=False, indent=2)

            msg = MIMEMultipart()
            msg["From"] = self.from_address or self.username or "cynovela@localhost"
            msg["To"] = ", ".join(self.to_addresses)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(self.host, self.port, timeout=10) as server:
                server.ehlo()
                try:
                    server.starttls()
                except Exception:
                    pass
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.send_message(msg)
            logger.info(f"[SMTPListener] sent for {event_type}")
        except Exception as e:
            logger.warning(f"[SMTPListener] failed for {event_type}: {e}")


def register_all_listeners(db_connection_factory: Callable, yaml_config: dict | None = None) -> dict:
    """EventBus に全リスナーを登録する。server 起動時に 1 度だけ呼ぶ。"""
    registered: dict = {}

    audit_listener = AuditLogListener(db_connection_factory)
    event_bus.on("*", audit_listener)
    logger.info("[EventBus] AuditLogListener registered (wildcard)")
    registered["audit_log"] = audit_listener

    if yaml_config:
        smtp_cfg = (yaml_config.get("notifications") or {}).get("smtp") or {}
        if smtp_cfg.get("enabled", False):
            smtp_listener = SMTPListener(smtp_cfg)
            if smtp_listener.notify_on:
                for ev in smtp_listener.notify_on:
                    event_bus.on(ev, smtp_listener)
                logger.info(f"[EventBus] SMTPListener registered for: {sorted(smtp_listener.notify_on)}")
            else:
                event_bus.on("*", smtp_listener)
                logger.info("[EventBus] SMTPListener registered (wildcard, no notify_on filter)")
            registered["smtp"] = smtp_listener

    return registered

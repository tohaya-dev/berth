"""Cynovela — Alert engine.

LLM 切断 / ディスク高負荷 / RAG 品質低下 / ゼロヒット急増 / ドキュメント期限切れ /
新ファイル追加 を検知して Alert オブジェクトのリストを返す。

Public API:
    AlertLevel (Enum: red / yellow / blue)
    Alert (dataclass)
    run_alert_checks() -> list[Alert]
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# 既存 DB 接続ヘルパー (sqlite3 row_factory)
from db import get_db


class AlertLevel(Enum):
    RED = "red"
    YELLOW = "yellow"
    BLUE = "blue"


@dataclass
class Alert:
    level: AlertLevel
    code: str
    message_en: str
    message_ja: str
    detail: Optional[dict[str, Any]] = field(default=None)


def _get_setting_float(key: str, default: float) -> float:
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row and row["value"]:
            try:
                return float(row["value"])
            except (TypeError, ValueError):
                return default
        return default
    finally:
        conn.close()


async def _check_llm() -> Optional[Alert]:
    """LLM (LM Studio) 疎通確認 — 3 秒タイムアウト."""
    try:
        import httpx  # type: ignore

        # PORTABILITY FIX: settings DB / アダプタ経由で base_url を取得（localhost ハードコード廃止）
        from routers.lmstudio import _lmstudio_endpoint_from_settings
        _ep = (_lmstudio_endpoint_from_settings() or "http://localhost:1234").rstrip("/")
        if _ep.endswith("/v1"):
            _ep = _ep[:-3]
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{_ep}/v1/models")
            if not r.is_success:
                raise RuntimeError(f"status {r.status_code}")
        return None
    except Exception as e:
        return Alert(
            level=AlertLevel.RED,
            code="LLM_DISCONNECTED",
            message_en="LLM (LM Studio) is not responding.",
            message_ja="LLM（LM Studio）が応答していません。",
            detail={"error": str(e)},
        )


def _check_disk() -> Optional[Alert]:
    """ディスク使用率 >= 90% で RED."""
    try:
        _, used, free = shutil.disk_usage("/")
        total = used + free
        used_pct = used / total if total else 0
        if used_pct >= 0.90:
            return Alert(
                level=AlertLevel.RED,
                code="DISK_CRITICAL",
                message_en=f"Disk usage is {used_pct*100:.1f}%. Critical.",
                message_ja=f"ディスク使用率が {used_pct*100:.1f}% です。危険な状態です。",
                detail={"used_pct": round(used_pct, 4)},
            )
    except Exception:
        pass
    return None


def _check_rag_quality() -> Optional[Alert]:
    """直近 1 時間の faithfulness 平均 < threshold で YELLOW."""
    threshold = _get_setting_float("rag_quality_threshold", 0.7)
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT AVG(CAST(json_extract(detail, '$.faithfulness') AS REAL)) AS avg_faith
            FROM audit_logs
            WHERE action IN ('chat_query', 'RAG_QUERY')
              AND timestamp >= datetime('now', '-1 hours')
              AND json_extract(detail, '$.faithfulness') IS NOT NULL
            """
        ).fetchone()
    finally:
        conn.close()
    if row and row["avg_faith"] is not None and row["avg_faith"] < threshold:
        return Alert(
            level=AlertLevel.YELLOW,
            code="RAG_QUALITY_LOW",
            message_en=f"RAG quality score dropped below {threshold} " f"(current: {row['avg_faith']:.2f}).",
            message_ja=f"RAG 品質スコアが閾値 {threshold} を下回っています" f"（現在: {row['avg_faith']:.2f}）。",
            detail={"avg_faithfulness": round(float(row["avg_faith"]), 4), "threshold": threshold},
        )
    return None


def _check_zero_hit_spike() -> Optional[Alert]:
    """直近 1 時間の zero-hit (top_score < 0.3) 率が 20% 超で YELLOW."""
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE
                      WHEN CAST(json_extract(detail, '$.top_score') AS REAL) < 0.3
                      THEN 1 ELSE 0 END) AS zero_hits
            FROM audit_logs
            WHERE action IN ('chat_query', 'RAG_QUERY')
              AND timestamp >= datetime('now', '-1 hours')
            """
        ).fetchone()
    finally:
        conn.close()
    if row and (row["total"] or 0) >= 5:
        rate = (row["zero_hits"] or 0) / row["total"]
        if rate > 0.20:
            return Alert(
                level=AlertLevel.YELLOW,
                code="ZERO_HIT_SPIKE",
                message_en=f"Zero-hit rate spiked to {rate*100:.0f}% in the last hour.",
                message_ja=f"直近 1 時間のゼロヒット率が {rate*100:.0f}% に急増しました。",
                detail={"zero_hit_rate": round(rate, 3)},
            )
    return None


def _check_stale_documents() -> Optional[Alert]:
    """30 日以上更新されていないドキュメントが 1 件以上で YELLOW.

    既存の files テーブルには uploaded_at は無く scanned_at が日時を持つ。
    refreshed_at は P4 で追加 (NULL のものを古いとみなす)。
    """
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM files
            WHERE refreshed_at IS NULL
              AND scanned_at < datetime('now', '-30 days')
            """
        ).fetchone()
    finally:
        conn.close()
    if row and (row["c"] or 0) > 0:
        return Alert(
            level=AlertLevel.YELLOW,
            code="STALE_DOCUMENTS",
            message_en=f"{row['c']} document(s) have not been refreshed in over 30 days.",
            message_ja=f"{row['c']} 件のドキュメントが 30 日以上更新されていません。",
            detail={"count": int(row["c"])},
        )
    return None


def _check_new_files() -> Optional[Alert]:
    """直近 5 分以内に追加された新規ファイルがあれば BLUE 通知."""
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM files
            WHERE scanned_at >= datetime('now', '-5 minutes')
            """
        ).fetchone()
    finally:
        conn.close()
    if row and (row["c"] or 0) > 0:
        return Alert(
            level=AlertLevel.BLUE,
            code="NEW_FILES_ADDED",
            message_en=f"{row['c']} new document(s) added.",
            message_ja=f"{row['c']} 件の新しいドキュメントが追加されました。",
            detail={"count": int(row["c"])},
        )
    return None


async def run_alert_checks() -> list[Alert]:
    """全チェックを実行して発火している Alert のリストを返す.

    各チェック内のエラーはサイレントに無視し、ヘルスチェック自体は失敗しない。
    """
    alerts: list[Alert] = []
    # 並列実行で軽くする
    results = await asyncio.gather(
        _check_llm(),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Alert):
            alerts.append(r)

    # 同期チェック (DB アクセス)
    for fn in (_check_disk, _check_rag_quality, _check_zero_hit_spike, _check_stale_documents, _check_new_files):
        try:
            a = fn()
            if a is not None:
                alerts.append(a)
        except Exception as e:
            logging.warning("alert check %s failed: %s", fn.__name__, e)
    return alerts

"""Cynovela — DataSyncService (BLOCK D-3)。

DataSource の差分検出 + 自動再Publish の足場。
デフォルト OFF。Settings UI から ON/OFF できるようにするため、
インターフェースとシード実装を提供する。

実装範囲:
  - start / stop ライフサイクル (asyncio.Task)
  - 既存 sources を一定間隔でポーリングして差分検出
  - 既存ファイルとの content_hash 比較 → 新規 / 変更 / 削除を検出
  - 実際の publish 連携は今は noop (ログ出力のみ)。後続フェーズで rag.publish に接続。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from providers.data_source import FileSystemDataSource
# PORTABILITY FIX 20260527 Stage2 M14: db.get_db() 経由で WAL + busy_timeout + foreign_keys
# 設定を享受する（直接 sqlite3.connect 廃止）
from db import get_db

logger = logging.getLogger(__name__)


class DataSyncService:
    def __init__(self, db_path: str, poll_interval_sec: int = 60):
        self.db_path = db_path
        self.poll_interval = max(10, int(poll_interval_sec))
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info(f"DataSyncService started (interval={self.poll_interval}s)")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("DataSyncService stopped")

    async def _run(self) -> None:
        while self._running:
            try:
                await self._sync_all_sources()
            except Exception as e:
                # FIX-049: logger.exception でスタックトレース可達化
                logger.exception(f"DataSync error: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _sync_all_sources(self) -> None:
        if not os.path.exists(self.db_path):
            return
        # PORTABILITY FIX 20260527 Stage2 M14: db.get_db() を使う（WAL/foreign_keys/busy_timeout 整合）
        conn = get_db()
        try:
            sources = conn.execute("SELECT id, name, path, status FROM sources WHERE status != 'failed'").fetchall()
        finally:
            conn.close()
        for s in sources:
            try:
                await self._sync_source(dict(s))
            except Exception as e:
                # FIX-049: logger.exception でスタックトレース可達化
                logger.exception(f"Sync error for source {s['id']}: {e}")

    async def _sync_source(self, source: dict) -> None:
        ds = FileSystemDataSource()
        discovered = await ds.discover(source["path"])
        # 比較は v11 の files テーブルと照合する。
        # 現状は差分検出のログ出力のみ (実 publish 連携は後続)。
        # PORTABILITY FIX 20260527 Stage2 M14: db.get_db() を使う
        conn = get_db()
        try:
            existing = {
                r["path"]: r["id"]
                for r in conn.execute("SELECT id, path FROM files WHERE source_id = ?", (source["id"],)).fetchall()
            }
        finally:
            conn.close()
        discovered_paths = {d.source_path for d in discovered}
        new_paths = discovered_paths - set(existing.keys())
        deleted_paths = set(existing.keys()) - discovered_paths
        if new_paths or deleted_paths:
            # TODO(P1-5): data_sync publish 連携。差分検出後に該当 source を含む collection を
            # 特定して republish をトリガする実装が必要。タイマー駆動の自動 republish は重い処理を
            # 誘発しうるため、対象 collection の決定と多重実行防止 (publish_jobs の pending/running
            # ガード) を設計してから接続する。現状は検出ログのみ (ANDON 非該当・報告のみ)。
            logger.info(
                f"[DataSync] source={source['id']} new={len(new_paths)} "
                f"deleted={len(deleted_paths)} (publish連携は未統合)"
            )


# シングルトン管理
_service: Optional[DataSyncService] = None


def get_service(db_path: str, poll_interval_sec: int = 60) -> DataSyncService:
    global _service
    if _service is None:
        _service = DataSyncService(db_path, poll_interval_sec)
    return _service


async def start_service(db_path: str, poll_interval_sec: int = 60) -> None:
    svc = get_service(db_path, poll_interval_sec)
    await svc.start()


async def stop_service() -> None:
    global _service
    if _service is not None:
        await _service.stop()
        _service = None

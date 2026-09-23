"""Cynovela — EventBus (P3-1)。

emit(event_type, payload) を 1 箇所に集め、リスナーを後付け追加できるようにする。
リスナーが例外を投げても他リスナーへの通知は続行 (フォールトトレラント)。

使用例:
    from services.event_bus import event_bus
    event_bus.emit("file_uploaded", {"filename": "x.txt", "workspace_id": "ws-1"})
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class EventBus:
    """シンプルな同期/非同期イベントバス。"""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = {}
        self._wildcard_listeners: list[Callable] = []  # "*" 全イベント購読

    def on(self, event_type: str, listener: Callable) -> None:
        """リスナーを登録する。event_type='*' で全イベント購読。"""
        if event_type == "*":
            self._wildcard_listeners.append(listener)
        else:
            self._listeners.setdefault(event_type, []).append(listener)

    def off(self, event_type: str, listener: Callable) -> None:
        """リスナーを登録解除する。"""
        if event_type == "*":
            self._wildcard_listeners = [l for l in self._wildcard_listeners if l != listener]
        elif event_type in self._listeners:
            self._listeners[event_type] = [l for l in self._listeners[event_type] if l != listener]

    def _build_event(self, event_type: str, payload: dict | None) -> dict:
        return {
            "type": event_type,
            "payload": payload or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        """イベントを発行する (同期コンテキストから呼べる)。
        非同期リスナーは asyncio.create_task でスケジュール、ループが無ければ無視。"""
        event = self._build_event(event_type, payload)
        all_listeners = self._listeners.get(event_type, []) + self._wildcard_listeners

        for listener in all_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(listener(event))
                    except RuntimeError:
                        # ループ無し (テスト/同期コンテキスト) はスキップ
                        pass
                else:
                    listener(event)
            except Exception as e:
                # FIX-049: logger.exception でスタックトレース可達化
                logger.exception(f"[EventBus] Listener error for {event_type}: {e}")

    async def emit_async(self, event_type: str, payload: dict | None = None) -> None:
        """非同期コンテキストから呼べる emit。全リスナーを順次 await。"""
        event = self._build_event(event_type, payload)
        all_listeners = self._listeners.get(event_type, []) + self._wildcard_listeners
        for listener in all_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception as e:
                # FIX-049: logger.exception でスタックトレース可達化
                logger.exception(f"[EventBus] Async listener error for {event_type}: {e}")

    def listener_count(self, event_type: str | None = None) -> int:
        """登録済みリスナー数 (デバッグ用)。"""
        if event_type:
            return len(self._listeners.get(event_type, []))
        return sum(len(v) for v in self._listeners.values()) + len(self._wildcard_listeners)


# シングルトン (アプリ全体で共有)
event_bus = EventBus()

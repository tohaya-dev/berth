"""Cynovela — CircuitBreaker (P1-2)。

LM Studio 等の外部サービス障害時にリクエストを遮断し、UI を即座にエラー復帰させる。

状態遷移:
  CLOSED  --(failure_threshold 回連続失敗)-->   OPEN
  OPEN    --(recovery_timeout 秒経過)-->         HALF_OPEN
  HALF_OPEN -- 成功 -->                          CLOSED
  HALF_OPEN -- 失敗 -->                          OPEN
"""

from __future__ import annotations

import time
import asyncio
from enum import Enum
from typing import Callable, Any


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """CircuitBreaker が OPEN 状態のときに送出される例外。"""

    def __init__(self, service_name: str, retry_after: float):
        self.service_name = service_name
        self.retry_after = retry_after
        super().__init__(f"{service_name} は現在利用できません。" f"{retry_after:.0f}秒後に自動的に再試行します。")


class CircuitBreaker:
    def __init__(
        self,
        service_name: str = "LM Studio",
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        enabled: bool = True,
    ):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = float(recovery_timeout)
        self.enabled = enabled

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    def _should_attempt(self) -> bool:
        """ロック保持を前提に、現在試行可能か判定する。"""
        if not self.enabled:
            return True
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN

    def _on_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold or self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """非同期関数を CircuitBreaker 経由で呼ぶ。
        OPEN 状態では CircuitBreakerOpenError を即座に送出する。"""
        async with self._lock:
            if not self._should_attempt():
                retry_after = max(
                    0.0,
                    self.recovery_timeout - (time.monotonic() - self._last_failure_time),
                )
                raise CircuitBreakerOpenError(self.service_name, retry_after)

        try:
            result = await func(*args, **kwargs)
        except CircuitBreakerOpenError:
            raise
        except Exception:
            async with self._lock:
                self._on_failure()
            raise
        else:
            async with self._lock:
                self._on_success()
            return result

    def status(self) -> dict:
        """現在の状態を辞書で返す (ヘルスチェック用)。"""
        elapsed = time.monotonic() - self._last_failure_time if self._last_failure_time else 0.0
        retry_after = max(0.0, self.recovery_timeout - elapsed) if self._state == CircuitState.OPEN else 0.0
        return {
            "state": self._state.value,
            "failure_count": self._failure_count,
            "enabled": self.enabled,
            "service": self.service_name,
            "retry_after_sec": retry_after,
        }

"""アプリ全体で共有するランタイム状態を管理するシングルトンモジュール。

__main__ ブロックで 1 回だけ書き込む。それ以外での書き換えは禁止。
import server した場合は None のまま（意図的な設計）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class AppConfig:
    """argparse から受け取る起動時設定値。"""

    # 起動モード
    demo: bool = False  # --demo: デモデータ + admin bypass
    mock: bool = False  # --mock: LLM/Embedding/Reranker を Mock 化

    # ネットワーク
    host: str = "127.0.0.1"
    port: int = 8765
    lan: bool = False  # --lan: host を 0.0.0.0 に変更
    allow_tailscale: bool = False
    allow_subnet: List[str] = field(default_factory=list)

    # LLM
    lmstudio_url: str = "http://localhost:1234"
    mode: str = "text"  # full/text/lite/lite-en/minimal


# ── ランタイムオブジェクト ─────────────────────────────
# 以下はすべて __main__ ブロックで初期化される。
# import server した場合は None のまま（意図的）。
config: Optional[AppConfig] = None  # 起動設定
adapter: Optional[Any] = None  # LLM adapter (_adapter)
app_config_obj: Optional[Any] = None  # AppConfig object (_app_config)
llm_circuit_breaker: Optional[Any] = None  # _llm_circuit_breaker
llm_semaphore: Optional[Any] = None  # _llm_semaphore
event_bus: Optional[Any] = None  # _event_bus
registered_listeners: Optional[Any] = None
allowed_subnets: List[Any] = []

# 認証セッション (token -> {user_id, role, created_at})
# server.py から移動した mutable global. core/auth.py と routers/auth.py の両方が読み書きする
sessions: dict[str, dict] = {}

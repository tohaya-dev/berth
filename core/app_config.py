"""core.app_config — 起動モード AppConfig dataclass。

config.py の AppConfig (mode / demo / mock + 派生プロパティ) を core/ に re-export する。
state.py の AppConfig（host/port/lan などのネットワーク設定）とは別概念。

Stage R1-1B: 既存 config.py からの re-export ラッパー。
Stage R4 で _wire_providers_for_mode の引数として本格利用する。
"""

from __future__ import annotations

from config import AppConfig

__all__ = ["AppConfig"]

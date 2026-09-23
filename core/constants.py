"""アプリ全体で共有する定数。

server.py から切り出した固定値。複数モジュール（server / routers/）から参照される。
COMPARE_MODEL_PRESETS は LLM プリセット reload 時に要素単位で書き換えられるため、
モジュール属性として共有 dict 参照を保持する。
"""

from __future__ import annotations


# ユーザーロールのホワイトリスト（管理者API）
VALID_ROLES: set[str] = {"admin", "viewer"}


# C-8: collections.access_level のホワイトリスト（db.py の CHECK 制約と同値）。
# routers/collections.py の作成/更新 API が Python 側検証に使う。
ACCESS_LEVELS: tuple[str, ...] = ("confidential", "internal", "public")


# アーカイブ対象（kind -> tablename）
_ARCHIVABLE: dict[str, str] = {
    "source": "sources",
    "workspace": "workspaces",
    "collection": "collections",
}


# 比較対象モデルプリセット（preset reload 時に要素単位で書き換えられる）
COMPARE_MODEL_PRESETS: dict[str, dict] = {
    "lmstudio_local": {
        "label": "LM Studio (Local)",
        "provider": "lmstudio",
        "base_url": "http://localhost:1234",
        "model": "",
    },
    "ollama_local": {
        "label": "Ollama (Local)",
        "provider": "ollama",
        "base_url": "http://localhost:11434",
        "model": "",
    },
    "openrouter": {
        "label": "OpenRouter",
        "provider": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "",
    },
    "openai_compat": {"label": "OpenAI互換 (カスタム)", "provider": "openai_compat", "base_url": "", "model": ""},
    "mock_a": {"label": "Mock A", "provider": "mock", "base_url": "", "model": "mock-a"},
    "mock_b": {"label": "Mock B", "provider": "mock", "base_url": "", "model": "mock-b"},
}


# ロール切替デモ用ワークスペース名
ROLE_DEMO_WS_NAME: str = "ロール切替デモ"



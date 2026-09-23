"""Cynovela — ProviderRegistry。

全Providerを設定から一括生成し、health_check() でまとめて状態を返す。
server.py のモジュールレベル変数 (_adapter / _vector_store / _classifier 等)
と並行して動作させ、health_check API の集約点として機能する。
"""

from __future__ import annotations

from providers.embedding import get_embedding_provider
from providers.vector_store import get_vector_store_provider
from providers.classifier import get_classifier_provider
from providers.reranker import get_reranker_provider
from llm_adapter import get_llm_adapter


class ProviderRegistry:
    """全Providerを設定から一括生成・管理するクラス。"""

    def __init__(self, config: dict):
        llm_cfg = (config or {}).get("llm", {}) or {}
        self.llm = get_llm_adapter(
            base_url=llm_cfg.get("base_url", "http://localhost:1234"),
            provider=llm_cfg.get("provider", "lmstudio"),
            model=llm_cfg.get("model", ""),
            api_key=llm_cfg.get("api_key", ""),
        )
        self.embedding = get_embedding_provider(config or {})
        self.vector_store = get_vector_store_provider(config or {})
        self.classifier = get_classifier_provider(config or {})
        self.reranker = get_reranker_provider(config or {})

    async def health_check(self) -> dict:
        """全Providerの接続状態を一括チェックして返す。"""
        return {
            "llm": await self.llm.test_connection(),
            "embedding": await self.embedding.test_connection(),
            "vector_store": await self.vector_store.test_connection(),
            "reranker": await self.reranker.test_connection(),
        }

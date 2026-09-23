"""Cynovela — RAGStrategy 型定義 (P3-3)。

現在実装済み:
  - SIMPLE: ベクター検索のみ
  - HYBRID_BM25: ベクター + BM25 (デフォルト)

将来実装 (将来実装予定):
  - CONTEXTUAL: チャンク前にLLMで文脈付記 (Anthropic方式)
  - HYPE:       仮回答→検索 (HyDE)
  - GRAPH:      ナレッジグラフベース推論 (Cynovela独自に ACL 引継ぎ)

このファイルは型定義のみ。実装は各戦略実装時に追加する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RAGStrategyType(Enum):
    """RAG 戦略の種別。collections.rag_strategy カラムで使用。"""

    SIMPLE = "simple"
    HYBRID_BM25 = "hybrid_bm25"
    CONTEXTUAL = "contextual"  # 将来 (現在は hybrid_bm25 と同一動作)
    HYPE = "hype"  # 将来
    GRAPH = "graph"  # 将来 (将来)

    @classmethod
    def is_implemented(cls, strategy: str) -> bool:
        return strategy in {cls.SIMPLE.value, cls.HYBRID_BM25.value}

    @classmethod
    def values(cls) -> list[str]:
        return [s.value for s in cls]


@dataclass
class RAGQuery:
    """RAG 検索クエリの標準型。"""

    query_text: str
    collection_ids: list[str]
    top_k: int = 5
    user_role: str = "admin"
    workspace_id: str = ""
    strategy: str = RAGStrategyType.HYBRID_BM25.value
    rerank_enabled: bool = False
    filters: dict | None = None


@dataclass
class RAGResult:
    """RAG 検索結果の標準型。"""

    chunks: list[dict]
    strategy_used: str
    total_searched: int
    acl_filtered_count: int
    search_latency_ms: float
    rerank_latency_ms: float


class BaseRAGStrategy(ABC):
    """全戦略の基底。"""

    @abstractmethod
    async def retrieve(self, query: RAGQuery) -> RAGResult: ...

    @property
    @abstractmethod
    def strategy_type(self) -> RAGStrategyType: ...


# ────────────────────────────────────────────
# GraphRAG 型定義 (将来実装予定 で実装)
# ────────────────────────────────────────────


@dataclass
class GraphNode:
    """ナレッジグラフノード。ACL を保持するのが Cynovela 独自。"""

    node_id: str
    content: str
    node_type: str  # entity / concept / document
    allowed_roles: list[str]  # ACL を引き継ぐ
    metadata: dict | None = None


@dataclass
class GraphEdge:
    source_id: str
    target_id: str
    relation: str  # "relates_to" / "part_of" / "references"
    weight: float = 1.0


class GraphRAGStrategy(BaseRAGStrategy):
    """GraphRAG 戦略 (将来実装予定)。

    設計方針:
    - ドキュメントから Knowledge Graph を構築
    - ノードに ACL を引き継ぐ (Microsoft GraphRAG にない Cynovela 独自機能)
    - ノード間の関係を辿って推論
    """

    @property
    def strategy_type(self) -> RAGStrategyType:
        return RAGStrategyType.GRAPH

    async def retrieve(self, query: RAGQuery) -> RAGResult:
        raise NotImplementedError(
            "GraphRAGStrategy は 将来実装予定 で実装予定。" "現在は hybrid_bm25 を使用してください。"
        )

    async def build_graph(self, chunks: list[dict]) -> tuple[list[GraphNode], list[GraphEdge]]:
        """ドキュメントチャンクから Knowledge Graph を構築する (将来)。"""
        raise NotImplementedError

    async def traverse_with_acl(
        self,
        start_nodes: list[GraphNode],
        user_role: str,
        max_hops: int = 2,
    ) -> list[GraphNode]:
        """ACL を考慮したグラフ探索 (将来)。
        ユーザーの権限で見えないノードはスキップしながら関連ノードを辿る。
        Cynovela GraphRAG の差別化ポイント。"""
        raise NotImplementedError

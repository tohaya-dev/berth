"""Cynovela — パイプライン結果型定義

Publish結果とRAG検索結果を構造化して返すためのdataclass群。
フロントエンドへのJSON返却・publish_historyテーブルへの保存に使用する。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ChunkMeta:
    """Chunk一覧表示用のメタデータ。"""

    chunk_id: str
    source_doc: str  # ファイル名
    page_hint: int | None  # ページ番号（テキストから推定できる場合）
    char_count: int  # このchunkの文字数
    pii_detected: bool  # PIIが検出されてプレースホルダー置換された
    excluded: bool  # RAG対象から除外された（ベクター化なし）
    preview: str  # 先頭100文字のプレビュー（UI表示用）


@dataclass
class PipelineResult:
    """Publish完了時の結果サマリー。server.pyのPublishエンドポイントが返す。"""

    workspace_id: str
    doc_count: int  # 処理したファイル数
    chunk_count: int  # 作成したチャンク総数
    pii_count: int  # PII置換処理したチャンク数
    excluded_count: int  # RAG対象から除外したチャンク数
    avg_chunk_chars: float  # 平均文字数/チャンク（粒度の目安）
    elapsed_seconds: float  # Publish処理にかかった秒数
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_display(self) -> dict:
        """フロントエンド表示用の辞書を返す。絵文字ラベル付き。"""
        return {
            "workspace_id": self.workspace_id,
            "doc_count": self.doc_count,
            "chunk_count": self.chunk_count,
            "avg_chunk_chars": round(self.avg_chunk_chars, 1),
            "pii_count": self.pii_count,
            "excluded_count": self.excluded_count,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "timestamp": self.timestamp,
            # UI表示用のメッセージ
            "summary_lines": [
                f"📄 {self.doc_count}ファイル処理",
                f"🧩 {self.chunk_count}チャンク作成（平均 {self.avg_chunk_chars:.0f}文字/チャンク）",
                *([f"🔒 {self.pii_count}チャンク：PII置換してベクター化"] if self.pii_count > 0 else []),
                *([f"🚫 {self.excluded_count}チャンク：RAG対象から除外"] if self.excluded_count > 0 else []),
                f"⏱️ {self.elapsed_seconds:.1f}秒",
            ],
        }


@dataclass
class ChunkHit:
    """RAG検索でヒットした1件のチャンク情報。"""

    chunk_id: str
    source_doc: str  # ファイル名
    vector_score: float  # ベクター類似度スコア（0〜1）
    bm25_score: float  # BM25キーワードスコア（正規化済み 0〜1）
    hybrid_score: float  # 統合スコア（vector*0.7 + bm25*0.3）
    content_preview: str  # 先頭150文字のプレビュー
    pii_detected: bool  # このchunkにPIIが含まれていた（置換済み）
    rerank_score: float = 0.0  # Phase 2 Step 5: Rerankerが付与したスコア（0なら未適用）


@dataclass
class RetrievalResult:
    """RAGチャット1回の検索・生成結果。デバッグパネル表示に使用。"""

    query: str  # ユーザーのクエリ
    hits: list[ChunkHit]  # ヒットしたchunk（スコア降順）
    prompt_sent: str  # LLMに実際に渡したプロンプト全文
    answer: str  # LLMの回答
    vector_elapsed: float  # ベクター検索にかかった秒数
    llm_elapsed: float  # LLM生成にかかった秒数
    total_elapsed: float  # 合計秒数
    model_id: str  # 使用したモデルID
    n_hits: int  # ヒット件数

    def to_debug_dict(self) -> dict:
        """フロントエンドのデバッグパネル表示用の辞書を返す。"""
        return {
            "query": self.query,
            "n_hits": self.n_hits,
            "hits": [
                {
                    "chunk_id": h.chunk_id,
                    "source_doc": h.source_doc,
                    "vector_score": round(h.vector_score, 3),
                    "bm25_score": round(h.bm25_score, 3),
                    "hybrid_score": round(h.hybrid_score, 3),
                    "rerank_score": round(h.rerank_score, 3),
                    "content_preview": h.content_preview,
                    "pii_detected": h.pii_detected,
                }
                for h in self.hits
            ],
            "prompt_sent": self.prompt_sent,
            "timing": {
                "vector_ms": round(self.vector_elapsed * 1000),
                "llm_ms": round(self.llm_elapsed * 1000),
                "total_ms": round(self.total_elapsed * 1000),
            },
            "model_id": self.model_id,
        }

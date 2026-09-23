"""utils.metadata.enrich — チャンクメタデータ強化。

Stage R6-2D: 薄いラッパー。Stage R8 で実体移植予定。
"""

from typing import Any


def enrich_chunk_metadata(chunk: dict[str, Any]) -> dict[str, Any]:
    """チャンクに分類・感度メタデータを付与する。

    Stage R6 では minimal stub。Stage R8 で classification + sensitivity の結合
    実装を移植する。
    """
    text = chunk.get("content", "") or chunk.get("text", "")
    if not text:
        return chunk

    from utils.metadata.classification import classify_document_type
    from utils.metadata.sensitivity import score_sensitivity

    enriched = dict(chunk)
    try:
        enriched["classification"] = classify_document_type(text)
    except Exception:
        pass
    try:
        enriched["sensitivity"] = score_sensitivity(text)
    except Exception:
        pass
    return enriched


__all__ = ["enrich_chunk_metadata"]

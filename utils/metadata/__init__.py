"""utils.metadata — メタデータ関連の統合パッケージ (実体集約版)。

Stage R6-fix (Phase 3-fix) 完了状態:
- 旧 utils/pii_detector.py / utils/classification_engine.py / metadata_engine.py root の
  実装をすべて本パッケージに移植 (薄いラッパーから実体集約へ昇格)
- 14 カテゴリ (Smart Ingestion 仕様) + 5 種 (旧 metadata_engine 仕様) を統合
- LLM judge (HIGH-5) を pii.llm_judge_pi として追加

Phase 3 Recon Agent E §3-1 の統合ターゲットを満たす実装。
"""

from utils.metadata.classification import (
    CATEGORIES,
    ClassificationEngine,
    HybridClassifier,
    LightweightClassifier,
    LLMClassifier,
    classify_document_type,
    get_classifier,
)
from utils.metadata.enrich import enrich_chunk_metadata
from utils.metadata.pii import (
    FALLBACK_PATTERNS,
    HIGH_RISK_TYPES,
    detect_pii,
    get_active_recognizers,
    get_pii_detection_mode,
    get_sensitivity_label,
    get_sensitivity_score,
    llm_judge_pi,
    mask_pii,
    set_pii_detection_mode,
)
from utils.metadata.sensitivity import SENSITIVITY_RULES, score_sensitivity

__all__ = [
    "CATEGORIES",
    "ClassificationEngine",
    "FALLBACK_PATTERNS",
    "HIGH_RISK_TYPES",
    "HybridClassifier",
    "LLMClassifier",
    "LightweightClassifier",
    "SENSITIVITY_RULES",
    "classify_document_type",
    "detect_pii",
    "enrich_chunk_metadata",
    "get_active_recognizers",
    "get_classifier",
    "get_pii_detection_mode",
    "get_sensitivity_label",
    "get_sensitivity_score",
    "llm_judge_pi",
    "mask_pii",
    "score_sensitivity",
    "set_pii_detection_mode",
]

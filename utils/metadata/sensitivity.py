"""utils.metadata.sensitivity — 感度スコア計算 (実体集約版)。

Stage R6-fix (Phase 3-fix): 旧 metadata_engine.score_sensitivity の実装を本ファイルに移植。
"""

from __future__ import annotations

import re
from typing import Any

SENSITIVITY_RULES: list[dict[str, Any]] = [
    {
        "level": "confidential",
        "score": 4,
        "patterns": [r"(機密|confidential|top\s+secret|極秘|マイナンバー|個人番号)"],
        "label_en": "Confidential",
        "label_ja": "機密",
    },
    {
        "level": "internal",
        "score": 3,
        "patterns": [r"(社内|internal\s+use|for\s+internal|取扱注意|社外秘)"],
        "label_en": "Internal",
        "label_ja": "社内",
    },
    {
        "level": "restricted",
        "score": 2,
        "patterns": [r"(restricted|要注意|limited\s+distribution|関係者限り)"],
        "label_en": "Restricted",
        "label_ja": "関係者限り",
    },
    {
        "level": "public",
        "score": 1,
        "patterns": [r"(public|公開|プレスリリース|press\s+release)"],
        "label_en": "Public",
        "label_ja": "公開",
    },
]


def score_sensitivity(text: str) -> dict[str, Any]:
    """ドキュメント先頭 5000 文字から感度をスコアリング.

    キーワードが見つからない場合は internal をデフォルトに置く。
    PII (email/phone) を含む場合も internal 以上扱い。
    """
    sample = (text or "")[:5000]
    for rule in sorted(SENSITIVITY_RULES, key=lambda r: r["score"], reverse=True):
        for pat in rule["patterns"]:
            if re.search(pat, sample, re.IGNORECASE):
                return {
                    "level": rule["level"],
                    "score": rule["score"],
                    "label_en": rule["label_en"],
                    "label_ja": rule["label_ja"],
                }
    # PII fallback (循環依存回避のため遅延 import)
    try:
        from utils.metadata.pii import detect_pii

        if detect_pii(sample):
            return {"level": "internal", "score": 3, "label_en": "Internal", "label_ja": "社内"}
    except Exception:
        pass
    return {"level": "internal", "score": 3, "label_en": "Internal", "label_ja": "社内"}


__all__ = ["SENSITIVITY_RULES", "score_sensitivity"]
